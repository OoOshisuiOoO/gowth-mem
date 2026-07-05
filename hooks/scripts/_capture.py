#!/usr/bin/env python3
"""v4.0 session capture — log each turn's user prompt + Claude's actions trace.

Part of the metacognition layer (`.claude/research/v4.0-metacognition.md` §3).
Called from the Stop hook (`auto-journal.py`) once per turn. Reads the tail of
the transcript JSONL, finds the last user prompt, and captures — from the
assistant records that followed it — the visible reasoning summary (first ~300
chars of assistant text) and an **actions trace** (the tool-use sequence), then
appends a compact turn record to `<ws>/journal/sessions/<YYYY-MM-DD>-<sid8>.md`.

Why actions, not thinking: in Claude Code transcripts the extended-thinking
blocks are signature-only — the `thinking` text field is EMPTY (verified: 24/24
blocks in a live transcript). A thinking-based capture would silently store
nothing. The tool-use trace (`Read(x) → Edit(y) → Bash(…)`) is the honest proxy
for "hướng suy nghĩ" — what Claude actually decided to do. An opportunistic
thinking extractor is kept: if a future Claude Code populates the `thinking`
text, it is appended (gated by `reflection.capture_thinking`).

Session logs live under `journal/` so `_forget.py` archives them past
`journal.raw_ttl_days` (the same ephemeral-buffer TTL as raw journals). They
feed the every-N-turn honest self-review (`/mem-review`).

NEVER raises: any failure → `_debug.log_debug` + return False. The Stop hook
must never break the session.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import atomic_write  # type: ignore
from _debug import log_debug  # type: ignore
from _home import journal_dir  # type: ignore
from _lock import file_lock  # type: ignore

TAIL_BYTES = 512 * 1024         # read only the transcript tail (recent turns)
PER_BLOCK_THINKING_CHARS = 400  # cap each thinking block before joining
CLAUDE_HEAD_CHARS = 300         # first-300-chars assistant text = reasoning summary
ACTIONS_MAX_CHARS = 500         # cap the joined tool-use trace
COMMAND_HEAD_CHARS = 60         # cap non-path key args (command/pattern/query)
DEFAULT_MAX_PROMPT_CHARS = 2000
DEFAULT_MAX_THINKING_CHARS = 1500

_TURN_RE = re.compile(r"^##\s+turn\s+(\d+)\b", re.MULTILINE)


def _oneline(s: str) -> str:
    """Collapse all whitespace (incl. newlines) to single spaces + strip.

    Keeps each captured field on a single markdown line so the `## turn N`
    idempotence scan stays reliable and the log reads cleanly.
    """
    return re.sub(r"\s+", " ", s or "").strip()


def _read_tail_records(p: Path, max_bytes: int = TAIL_BYTES) -> list[dict]:
    """Return parsed JSONL records from the last `max_bytes` of the transcript.

    Drops the first (likely partial) line when the file was truncated to the
    tail. Malformed lines are skipped. Any I/O error → empty list.
    """
    try:
        size = p.stat().st_size
    except OSError:
        return []
    try:
        with p.open("rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            data = f.read()
    except OSError:
        return []
    text = data.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if size > max_bytes and lines:
        lines = lines[1:]  # first line is a partial record
    out: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _extract_text_parts(content) -> str:
    """Joined `type=="text"` text from a message.content (str or list-of-parts).

    A str content is returned verbatim. A list yields only text parts — so
    tool-result / tool-use records naturally produce "" and are skipped.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                t = part.get("text") or ""
                if t.strip():
                    parts.append(t)
        return "\n".join(parts)
    return ""


def _extract_thinking(content) -> list[str]:
    """Return thinking-block strings from an assistant message.content list.

    Thinking text lives under the `thinking` key; older/alt transcripts may
    carry it under `text` — keep that fallback.
    """
    out: list[str] = []
    if not isinstance(content, list):
        return out
    for part in content:
        if not isinstance(part, dict) or part.get("type") != "thinking":
            continue
        t = part.get("thinking")
        if not (isinstance(t, str) and t.strip()):
            t = part.get("text")
        if isinstance(t, str) and t.strip():
            out.append(t)
    return out


def _tool_arg(input_obj) -> str:
    """Pick the most informative single arg from a tool_use input.

    Priority: file_path (basename) → command head → pattern → query → url head.
    """
    if not isinstance(input_obj, dict):
        return ""
    for k in ("file_path", "notebook_path", "path"):
        v = input_obj.get(k)
        if isinstance(v, str) and v.strip():
            return Path(v.strip()).name
    for k in ("command", "pattern", "query", "url"):
        v = input_obj.get(k)
        if isinstance(v, str) and v.strip():
            return _oneline(v)[:COMMAND_HEAD_CHARS]
    return ""


def _extract_actions(content) -> list[str]:
    """Return `ToolName(key arg)` for each tool_use part in an assistant message."""
    out: list[str] = []
    if not isinstance(content, list):
        return out
    for part in content:
        if not isinstance(part, dict) or part.get("type") != "tool_use":
            continue
        name = part.get("name") or "tool"
        arg = _tool_arg(part.get("input"))
        out.append(f"{name}({arg})" if arg else str(name))
    return out


def _thinking_digest(blocks: list[str], total_cap: int) -> str:
    """One-line digest: each block capped at PER_BLOCK_THINKING_CHARS, joined,
    then the whole capped at `total_cap`."""
    capped = []
    for b in blocks:
        b = _oneline(b)
        if b:
            capped.append(b[:PER_BLOCK_THINKING_CHARS])
    return _oneline(" / ".join(capped))[:total_cap]


def _last_turn_no(text: str) -> int | None:
    """Return the N of the last `## turn N` heading, or None."""
    matches = _TURN_RE.findall(text)
    if not matches:
        return None
    try:
        return int(matches[-1])
    except ValueError:
        return None


def capture_turn(transcript_path: str, ws: str, session_id: str,
                 turn_no: int, settings: dict | None = None) -> bool:
    """Capture one turn (prompt + thinking digest + outcome) into the session log.

    Returns True on write (or idempotent skip), False on any failure or when
    there is nothing to capture. Never raises.
    """
    try:
        if not transcript_path:
            return False
        p = Path(transcript_path)
        if not p.is_file():
            return False

        refl = (settings or {}).get("reflection", {}) if isinstance(settings, dict) else {}
        if not isinstance(refl, dict):
            refl = {}
        try:
            max_prompt = int(refl.get("max_prompt_chars", DEFAULT_MAX_PROMPT_CHARS))
        except (TypeError, ValueError):
            max_prompt = DEFAULT_MAX_PROMPT_CHARS
        try:
            max_thinking = int(refl.get("max_thinking_chars", DEFAULT_MAX_THINKING_CHARS))
        except (TypeError, ValueError):
            max_thinking = DEFAULT_MAX_THINKING_CHARS
        capture_thinking = bool(refl.get("capture_thinking", True))

        records = _read_tail_records(p)
        if not records:
            return False

        # Last user record carrying real text = the prompt for this turn.
        last_user_idx = -1
        user_text = ""
        for i in range(len(records) - 1, -1, -1):
            rec = records[i]
            if rec.get("type") != "user":
                continue
            txt = _extract_text_parts((rec.get("message") or {}).get("content"))
            if txt.strip():
                last_user_idx = i
                user_text = txt
                break
        if last_user_idx < 0:
            return False

        # Assistant records AFTER that user prompt → visible text + actions trace
        # (+ opportunistic thinking, usually empty in real transcripts).
        thinking_blocks: list[str] = []
        text_heads: list[str] = []
        actions: list[str] = []
        for rec in records[last_user_idx + 1:]:
            if rec.get("type") != "assistant":
                continue
            content = (rec.get("message") or {}).get("content")
            if capture_thinking:
                thinking_blocks.extend(_extract_thinking(content))
            atext = _extract_text_parts(content).strip()
            if atext:
                text_heads.append(atext)
            actions.extend(_extract_actions(content))

        prompt = _oneline(user_text)[:max_prompt]
        claude_head = _oneline(" ".join(text_heads))[:CLAUDE_HEAD_CHARS]
        actions_trace = _oneline(" → ".join(actions))[:ACTIONS_MAX_CHARS]
        # Opportunistic only: real transcripts carry signature-only (empty) thinking.
        digest = _thinking_digest(thinking_blocks, max_thinking) if capture_thinking else ""

        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        hhmm = now.strftime("%H:%M")
        sid8 = (session_id or "default")[:8] or "default"
        target = journal_dir(ws) / "sessions" / f"{today}-{sid8}.md"

        header = (
            f"# Session log — {today} — {sid8}\n\n"
            "_Auto-captured turn log (user prompt + Claude summary + actions trace). "
            "Ephemeral: archived by `_forget.py` after `journal.raw_ttl_days`. "
            "Feeds the every-N-turn self-review (`/mem-review`)._\n"
        )
        block_lines = [
            f"\n## turn {turn_no} — {hhmm}",
            f"**User:** {prompt}",
            f"**Claude:** {claude_head}",
            f"**Actions:** {actions_trace}",
        ]
        # Only emit a Thinking line when the extractor actually found text —
        # avoids a wall of empty `**Thinking:**` lines in real-world logs.
        if digest:
            block_lines.append(f"**Thinking:** {digest}")
        block = "\n".join(block_lines) + "\n"

        with file_lock(f"capture-{ws}", timeout=5.0):
            existing = ""
            if target.is_file():
                try:
                    existing = target.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    existing = ""
            if existing:
                last = _last_turn_no(existing)
                if last is not None and last == turn_no:
                    return True  # idempotent: this turn already captured
            atomic_write(target, (existing if existing else header) + block)
        return True
    except TimeoutError as e:
        log_debug("capture", f"lock timeout ws={ws} turn={turn_no}: {e}")
        return False
    except Exception as e:  # never break the Stop hook
        log_debug("capture", f"capture_turn failed ws={ws} turn={turn_no}: {e}")
        return False
