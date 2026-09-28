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
from _atomic import atomic_write, safe_write  # type: ignore
from _debug import log_debug  # type: ignore
from _home import journal_dir  # type: ignore
from _lock import file_lock  # type: ignore

TAIL_BYTES = 512 * 1024         # read only the transcript tail (recent turns)
PER_BLOCK_THINKING_CHARS = 400  # cap each thinking block before joining
CLAUDE_HEAD_CHARS = 300         # first-300-chars assistant text = reasoning summary
ACTIONS_MAX_CHARS = 500         # cap the joined tool-use trace
COMMAND_HEAD_CHARS = 60         # cap non-path key args (command/pattern/query)
DEFAULT_MAX_PROMPT_CHARS = 2000
MAX_PROMPT_CHARS_CEILING = 8192  # reflection.max_prompt_chars is clamped here
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


def read_transcript_tail(transcript_path) -> list[dict]:
    """Parsed tail records of a transcript; [] on any problem. Never raises.

    Public so the Stop hook parses the tail ONCE per Stop and shares it between
    turn identity (`find_human_prompt`) and `capture_turn(records=…)`.
    """
    try:
        if not isinstance(transcript_path, str) or not transcript_path:
            return []
        p = Path(transcript_path)
        if not p.is_file():
            return []
        return _read_tail_records(p)
    except Exception:
        return []


# v4.7.3 — which transcript records are prompts, and whose.
#
# Claude Code writes far more than the human's words as `type: "user"`:
# `isMeta` records (Stop-hook feedback, skill expansions, image stubs, caveats),
# tool results, and plain records for machine re-invocations — while a prompt
# typed WHILE the model works is an `attachment` (`queued_command`), not a user
# record. Verified against live 2.1.2xx transcripts:
#   * HUMAN (authoritative) — `origin.kind == "human"` on a user record, or on a
#     `queued_command` attachment with `commandMode: "prompt"`. Text never
#     overrides it: users paste the gold "Stop hook feedback: …" label too.
#   * MACHINE — any other `origin.kind` (Claude Code's own contract: "a host
#     wrapping keyboard input must stamp {kind:'human'}"; task-notification,
#     peer, coordinator, plugin, observer, auto-continuation, unclassified, …),
#     a queued non-prompt command, or — origin-less — a known machine prefix
#     (teammate messages; notifications / hook feedback in pre-`origin`
#     transcripts). Except `channel` / `slack-ping`: they relay what a person
#     typed on another surface (e.g. a Telegram channel plugin) → UNCONFIRMED.
#   * TRANSPARENT — never a prompt: `isMeta`, compaction summaries
#     (isCompactSummary / isVisibleInTranscriptOnly), tool results, interrupt
#     markers, local-command output, `!cmd` bash OUTPUT (it can hold secrets and
#     is never the user's words).
#   * PROVENANCE (origin-less records, 2.1.181+): `promptSource` typed | queued
#     | sdk (`claude -p`) or `turnOrigin` human | sdk → HUMAN; `promptSource`
#     system or `turnOrigin` task_notification → MACHINE.
#   * UNCONFIRMED — any other origin-less record: `/goal`, `<bash-input>`,
#     legacy transcripts' prompts, but also `/model`-style local commands. An
#     ask only if the model ANSWERED it — an assistant record follows before the
#     next prompt, OR its promptId is the Stop input's `prompt_id` (Claude Code
#     says this Stop ends the response to it). The second test is required: the
#     FINAL assistant record is written after the Stop hook runs (verified with
#     a transcript snapshot taken inside a real Stop hook), so a tool-less
#     answer is never visible yet. /goal and bash mode can invoke the model,
#     /model never does.
_TRANSPARENT_PREFIXES = (
    "[Request interrupted",
    "<local-command-",
    "<bash-stdout>",
    "<bash-stderr>",
    "This session is being continued from a previous conversation",
)
_MACHINE_PREFIXES = (
    "<task-notification",
    "Stop hook feedback",
    "SubagentStop hook feedback",
    "Another Claude session sent a message",
    "<teammate-message",
)


_RELAYED_HUMAN_KINDS = ("channel", "slack-ping")
_HUMAN_SOURCES = ("typed", "queued", "sdk")
_MACHINE_SOURCES = ("system",)
_HUMAN_TURN_ORIGINS = ("human", "sdk")
_MACHINE_TURN_ORIGINS = ("task_notification",)


def prompt_text(rec) -> str:
    """The words of a prompt record — user-record content or a queued
    attachment's `prompt` (str or content parts). '' when there are none."""
    try:
        if not isinstance(rec, dict):
            return ""
        if rec.get("type") == "attachment":
            att = rec.get("attachment")
            return _extract_text_parts(att.get("prompt")) if isinstance(att, dict) else ""
        return _extract_text_parts((rec.get("message") or {}).get("content"))
    except Exception:
        return ""


_CMD_TAG_RE = re.compile(r"<(command-message|command-name|command-args)>(.*?)</\1>", re.S)


def _render_command(s: str) -> str | None:
    """`/name args` when `s` is NOTHING BUT Claude Code command tags, else None.

    Linear time: tags are matched ANCHORED at the cursor and the first
    non-tag stops the scan, so each failed attempt ends the parse. (Review:
    `\\s*(.*?)\\s*` backtracked cubically — 31 s on an unclosed tag + 4k spaces —
    and an unanchored .sub() was quadratic on many unclosed starts — 20k:
    19.5 s. hooks.json sets no timeout.) A repeated tag (two command blocks)
    is not a single command either → verbatim."""
    parts: dict = {}
    pos, n = 0, len(s)
    while True:
        while pos < n and s[pos].isspace():
            pos += 1
        if pos >= n:
            break
        m = _CMD_TAG_RE.match(s, pos)
        if m is None or m.group(1) in parts:
            return None
        parts[m.group(1)] = m.group(2).strip()
        pos = m.end()
    name = parts.get("command-name", "")
    return f"{name} {parts.get('command-args', '')}".strip() if name else None


def display_prompt(rec) -> str:
    """A prompt as the user typed it, for the session log's User: line:
    `/goal ship it` instead of Claude Code's <command-name>/<command-args>
    markup, `!kubectl …` instead of <bash-input>…</bash-input>. Only when the
    WHOLE text is that markup — anything else (a prompt quoting a tag, extra
    words after it, two command blocks) stays verbatim. Classification keeps
    the raw prompt_text."""
    txt = prompt_text(rec)
    s = txt.strip()
    if s.startswith("<bash-input>") and s.endswith("</bash-input>"):
        inner = s[len("<bash-input>"):-len("</bash-input>")].strip()
        return "!" + inner if inner else txt
    if s.startswith(("<command-name>", "<command-message>")):
        rendered = _render_command(s)
        if rendered:
            return rendered
    return txt


# Headroom past a field's cap that is still sanitized: every shape _privacy
# knows is far shorter, so a secret straddling the cap is still recognised.
_SANITIZE_SLACK = 4096
_PRIVATE_OPEN_RE = re.compile(r"<private>", re.IGNORECASE)   # same flags as _privacy
_PRIVATE_CLOSE_RE = re.compile(r"</private>", re.IGNORECASE)


def _sanitized(text: str, cap: int) -> tuple[str, int]:
    """Privacy-sanitize the first `cap + _SANITIZE_SLACK` chars, THEN the
    caller slices to `cap`. Sanitizing after the cap cut secrets into
    fragments the sanitizer no longer recognised; sanitizing the WHOLE field
    let a pasted prompt reach _privacy regexes that are super-linear on
    adversarial input (200 KB of 'a.a.a': 144 s for one Stop). Never raises."""
    head = text[:cap + _SANITIZE_SLACK]
    try:
        from _privacy import sanitize  # type: ignore
        out, n = sanitize(head)
        n = max(int(n or 0), 0)  # -1 = sanitizer bypassed
        # A <private> block whose closing tag lies BEYOND the window never
        # matched, and its first ~cap chars leaked (v4.7.5). Cut at the tag
        # (regex on the same text — `.lower()` changes lengths, e.g. 'İ'),
        # only when a closing tag really follows: a bare mention stays.
        m = _PRIVATE_OPEN_RE.search(out)
        # (- 9: a </private> STRADDLING the window edge starts inside it)
        if m and _PRIVATE_CLOSE_RE.search(text, max(0, len(head) - 9)):
            out, n = out[:m.start()] + "[REDACTED:private-block]", n + 1
        return out, n
    except Exception:
        return head, 0


def _origin_kind(origin):
    if isinstance(origin, dict):
        k = origin.get("kind")
        if isinstance(k, str) and k:
            return k
    return None


def _classify_record(rec) -> str | None:
    """"human" | "machine" | "unconfirmed" | None (not a prompt). Never raises."""
    try:
        if not isinstance(rec, dict):
            return None
        if rec.get("type") == "attachment":
            att = rec.get("attachment")
            if not isinstance(att, dict) or att.get("type") != "queued_command":
                return None
            if not prompt_text(rec).strip():
                return None
            if _origin_kind(att.get("origin")) == "human" and att.get("commandMode") == "prompt":
                return "human"
            return "machine"
        if rec.get("type") != "user" or rec.get("isMeta"):
            return None
        if rec.get("isCompactSummary") or rec.get("isVisibleInTranscriptOnly"):
            return None
        txt = prompt_text(rec).lstrip()
        if not txt:
            return None  # tool_result-only records carry no prompt
        kind = _origin_kind(rec.get("origin"))
        if kind == "human":
            return "human"
        if kind in _RELAYED_HUMAN_KINDS:
            return "unconfirmed"
        if kind is not None:
            return "machine"
        source, turn_origin = rec.get("promptSource"), rec.get("turnOrigin")
        if source in _HUMAN_SOURCES or turn_origin in _HUMAN_TURN_ORIGINS:
            return "human"
        if source in _MACHINE_SOURCES or turn_origin in _MACHINE_TURN_ORIGINS:
            return "machine"
        if txt.startswith(_TRANSPARENT_PREFIXES):
            return None
        if txt.startswith(_MACHINE_PREFIXES):
            return "machine"
        return "unconfirmed"
    except Exception:
        return None


def _is_assistant(rec) -> bool:
    return isinstance(rec, dict) and rec.get("type") == "assistant"


def _newest_human(items, current_prompt_id=None):
    """(index, record) of the newest human prompt in `items` — an iterable of
    (index, record) NEWEST FIRST — or (-1, None).

    Machine prompts are skipped, not decisive: a teammate message delivered
    mid-turn must not swallow the human turn it interrupted. An UNCONFIRMED
    record counts only when answered: an assistant record was seen after it
    and before any newer prompt (replies to a newer prompt do not answer an
    older one), or it IS the prompt `current_prompt_id` names (the Stop
    input's `prompt_id` — the response to it may not be on disk yet).
    """
    answered = False
    for i, rec in items:
        if _is_assistant(rec):
            answered = True
            continue
        kind = _classify_record(rec)
        if kind is None:
            continue
        if kind == "unconfirmed" and current_prompt_id \
                and isinstance(rec, dict) and rec.get("promptId") == current_prompt_id:
            answered = True
        if kind == "human" or (kind == "unconfirmed" and answered):
            return i, rec
        answered = False
    return -1, None


def find_human_prompt(records: list[dict], current_prompt_id=None) -> int:
    """Index of the newest human prompt record in `records`; -1 if none.

    The turn's prompt — for the Stop hook's turn identity and capture_turn's
    "User:" line. v4.7.3: it used to be "the last user record carrying text":
    Stop-hook feedback, skill expansions and notification XML were recorded as
    the user's prompt (the judge scored machine text as the user's prompting)
    and every machine re-invocation looked like a new turn.
    """
    try:
        return _newest_human(((i, records[i]) for i in range(len(records) - 1, -1, -1)),
                             current_prompt_id)[0]
    except Exception:
        return -1


def _is_stop_summary(rec) -> bool:
    """The record Claude Code writes after a Stop's hooks ran — the boundary
    between one turn's records and the next."""
    return isinstance(rec, dict) and rec.get("type") == "system" \
        and rec.get("subtype") == "stop_hook_summary" and rec.get("hookLabel") in (None, "Stop")


def _turn_window_start(records: list[dict]) -> int | None:
    """Index just after the newest Stop summary in `records`, or None when the
    tail holds none (the turn may have begun before the tail)."""
    for i in range(len(records) - 1, -1, -1):
        if _is_stop_summary(records[i]):
            return i + 1
    return None


def _asks_since_previous_stop(transcript_path, max_bytes: int | None = None) -> list[dict]:
    """Human asks between the previous Stop's summary and EOF, oldest first —
    for turns longer than the 512 KB tail (a request, heavy tool output, then
    a typed-ahead follow-up). Bounded reverse scan. Never raises."""
    asks: list[dict] = []
    try:
        for _, rec in _reverse_scan(Path(transcript_path), max_bytes or SCAN_BACK_MAX_BYTES):
            if _is_stop_summary(rec):
                break
            if _classify_record(rec) == "human":
                asks.append(rec)
    except Exception:
        pass
    asks.reverse()
    return asks


def has_record_identity(records: list[dict]) -> bool:
    """True when the transcript carries per-record uuids (every real Claude
    Code transcript). Hand-built / legacy ones do not — their Stops keep the
    pre-v4.7.3 per-Stop counting."""
    return any(isinstance(r, dict) and isinstance(r.get("uuid"), str) and r.get("uuid")
               for r in records)


SCAN_BACK_MAX_BYTES = 32 * 1024 * 1024  # bound on the beyond-the-tail search
_SCAN_CHUNK = 1024 * 1024
_BIG_LINE = 64 * 1024
_ASSISTANT_MARKER = {"type": "assistant"}


def _parse_scan_line(raw: bytes):
    """One JSONL line → a record worth classifying, the assistant marker, or
    None. Assistant lines are recognised by substring, never parsed (they are
    most of the bytes); big tool results are skipped unparsed."""
    if b'"type":"assistant"' in raw or b'"type": "assistant"' in raw:
        if b'"type":"user"' not in raw[:4096] and b'"type": "user"' not in raw[:4096]:
            return _ASSISTANT_MARKER
    if b'stop_hook_summary' in raw and len(raw) < _BIG_LINE:
        try:
            rec = json.loads(raw.decode("utf-8", errors="replace"))
        except (ValueError, UnicodeDecodeError):
            rec = None
        if _is_stop_summary(rec):
            return rec  # the turn boundary (_asks_since_previous_stop stops here)
    if b'"user"' not in raw and b'queued_command' not in raw:
        return None
    if len(raw) > _BIG_LINE and b'"tool_result"' in raw[:8192]:
        return None  # a big tool result — never a prompt, never worth parsing
    try:
        rec = json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeDecodeError):
        return None
    return rec if isinstance(rec, dict) else None


def _reverse_scan(p: Path, max_bytes: int):
    """Yield (None, record-or-marker) newest first over the last `max_bytes`."""
    pos = p.stat().st_size
    carry, scanned = b"", 0
    with p.open("rb") as f:
        while pos > 0 and scanned < max_bytes:
            n = min(_SCAN_CHUNK, pos)
            pos -= n
            scanned += n
            f.seek(pos)
            lines = (f.read(n) + carry).split(b"\n")
            # lines[0] may start mid-line: carry it into the earlier chunk.
            carry = lines[0] if pos > 0 else b""
            for raw in reversed(lines[1:] if pos > 0 else lines):
                rec = _parse_scan_line(raw)
                if rec is not None:
                    yield None, rec


def scan_back_for_human(transcript_path, max_bytes: int = SCAN_BACK_MAX_BYTES,
                        current_prompt_id=None):
    """Newest human prompt record in the last `max_bytes` of the transcript, or
    None. Never raises.

    For turns whose tool output pushed the prompt out of the 512 KB tail —
    routine in heavy sessions (a live 63-Stop replay: 15 Stops). Without it a
    background agent finishing after such a turn looked like a new turn.
    Reads backwards in 1 MB chunks; same classification as find_human_prompt.
    """
    try:
        return _newest_human(_reverse_scan(Path(transcript_path), max_bytes),
                             current_prompt_id)[1]
    except Exception:
        return None


def prompt_key(rec) -> str | None:
    """Stable identity of a human-prompt record: `uuid`, else `promptId`.

    Never the text — "tiếp" / "ok" / "làm đi" legitimately repeat. None when the
    transcript carries no record identity (legacy / hand-built): the caller then
    falls back to per-Stop counting.
    """
    if not isinstance(rec, dict):
        return None
    for key in ("uuid", "promptId"):
        v = rec.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


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
            # sanitize the RAW text before collapsing and cutting: a token
            # straddling char 60 leaked; key rules need real line breaks
            return _oneline(_sanitized(v, COMMAND_HEAD_CHARS)[0])[:COMMAND_HEAD_CHARS]
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
                 turn_no: int, settings: dict | None = None,
                 records: list[dict] | None = None,
                 prompt_rec: dict | None = None,
                 final_text: str | None = None) -> bool:
    """Capture one turn (prompt + thinking digest + outcome) into the session log.

    `records`: the already-parsed transcript tail (the Stop hook parses it once
    for turn identity); read from `transcript_path` when omitted.
    `prompt_rec`: the turn's human prompt when it lies BEYOND the tail
    (scan_back_for_human) — the whole tail is then that turn's work.
    `final_text`: the Stop input's `last_assistant_message` — the turn's final
    answer, which Claude Code writes to the transcript only AFTER the Stop hook
    (without it, 20% of live turns had an empty Claude: line).

    Returns True on write (or idempotent skip), False on any failure or when
    there is nothing to capture. Never raises.
    """
    try:
        if records is None:
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
        # v4.7.5: user-settable, so bounded — a huge value re-opened the
        # super-linear sanitize path (_SANITIZE_SLACK) on hostile pastes.
        max_prompt = min(max(max_prompt, 0), MAX_PROMPT_CHARS_CEILING)
        try:
            max_thinking = int(refl.get("max_thinking_chars", DEFAULT_MAX_THINKING_CHARS))
        except (TypeError, ValueError):
            max_thinking = DEFAULT_MAX_THINKING_CHARS
        from _home import coerce_bool  # type: ignore
        capture_thinking = coerce_bool(refl.get("capture_thinking"), True)

        if records is None:
            records = _read_tail_records(p)
        if not records:
            return False

        # The newest record where the HUMAN spoke = the prompt for this turn
        # (v4.7.3: not merely the last user record carrying text — see
        # find_human_prompt). Everything the assistant did after it, across
        # skill expansions and tool results, belongs to the turn.
        if not isinstance(prompt_rec, dict):
            idx = find_human_prompt(records)
            if idx < 0:
                return False
            prompt_rec = records[idx]
        # Every ask of THIS turn, oldest first: the human prompts since the
        # previous Stop's summary record (Claude Code writes it after that
        # Stop's hooks), plus the prompt the Stop hook identified (it may be an
        # origin-less ask confirmed by signals capture cannot see). Round-2
        # review: keying the log on the NEWEST ask alone dropped the main
        # request whenever the user typed a follow-up while the model worked
        # (92 live turns, 13%). Beyond the tail, the whole tail is the work.
        start = _turn_window_start(records)
        truncated = False
        if start is None:
            start = 0
            try:
                truncated = bool(transcript_path) and Path(transcript_path).stat().st_size > TAIL_BYTES
            except OSError:
                truncated = False
        asks = [i for i in range(start, len(records))
                if records[i] is prompt_rec or _classify_record(records[i]) == "human"]
        # Only real transcripts (record identity) are guaranteed Stop summaries
        # to stop the scan at; a hand-built one would join every ask in 32 MB.
        older = (_asks_since_previous_stop(transcript_path)
                 if truncated and has_record_identity(records) else [])
        if older:
            # The turn began before the tail: its asks come from the file, and
            # the whole tail is its work.
            ask_recs = list(older)
            uuids = {r.get("uuid") for r in ask_recs if r.get("uuid")}
            if prompt_rec.get("uuid") not in uuids and not any(r is prompt_rec for r in ask_recs):
                ask_recs.append(prompt_rec)
            last_user_idx = -1
            user_text = " ⟶ ".join(t for t in (display_prompt(r).strip() for r in ask_recs) if t)
        elif asks:
            last_user_idx = asks[0] - 1  # actions/text from the first ask on
            user_text = " ⟶ ".join(t for t in (display_prompt(records[i]).strip() for i in asks) if t)
        else:
            idx = next((i for i in range(len(records) - 1, -1, -1)
                        if records[i] is prompt_rec), -1)
            last_user_idx = idx
            user_text = display_prompt(prompt_rec)
        if not user_text.strip():
            return False

        # Assistant records AFTER that user prompt → visible text + actions trace
        # (+ opportunistic thinking, usually empty in real transcripts).
        thinking_blocks: list[str] = []
        text_heads: list[str] = []
        actions: list[str] = []
        for rec in records[last_user_idx + 1:]:  # -1 + 1 == 0: the whole tail
            if rec.get("type") != "assistant":
                continue
            content = (rec.get("message") or {}).get("content")
            if capture_thinking:
                thinking_blocks.extend(_extract_thinking(content))
            atext = _extract_text_parts(content).strip()
            if atext:
                text_heads.append(atext)
            actions.extend(_extract_actions(content))

        # Every field is sanitized RAW — before it is collapsed to one line
        # (key rules need real line breaks: the review found PGP / legacy /
        # partial keys leaking through the collapsed text) and before it is
        # capped (a secret cut at a cap escaped). safe_write re-checks the
        # whole block; redaction markers never re-match a secret shape.
        redacted = 0
        prompt, n = _sanitized(user_text, max_prompt); redacted += n
        prompt = _oneline(prompt)[:max_prompt]
        raw_texts = list(text_heads)
        final_raw = final_text if isinstance(final_text, str) else ""
        final = _oneline(final_raw)
        if final and raw_texts and _oneline(raw_texts[-1]) == final:
            raw_texts = raw_texts[:-1]  # the final record was already on disk
        narration, n = _sanitized("\n".join(raw_texts).strip(), CLAUDE_HEAD_CHARS); redacted += n
        narration = _oneline(narration)
        if final:
            # The final answer is not on disk at Stop time (the Stop input
            # carries it). Keep BOTH ends when they do not fit: the opening
            # narration (the plan) and the answer (the outcome).
            answer, n = _sanitized(final_raw, CLAUDE_HEAD_CHARS); redacted += n
            answer = _oneline(answer)
            claude_head = f"{narration} {answer}".strip()
            if len(claude_head) > CLAUDE_HEAD_CHARS and narration:
                head = narration[:CLAUDE_HEAD_CHARS // 2 - 2]
                claude_head = f"{head} … {answer[:CLAUDE_HEAD_CHARS - len(head) - 3]}"
            claude_head = claude_head[:CLAUDE_HEAD_CHARS]
        else:
            claude_head = narration[:CLAUDE_HEAD_CHARS]
        actions_trace, n = _sanitized(" → ".join(actions), ACTIONS_MAX_CHARS); redacted += n
        actions_trace = _oneline(actions_trace)[:ACTIONS_MAX_CHARS]
        # Opportunistic only: real transcripts carry signature-only (empty) thinking.
        if capture_thinking:
            clean_blocks = []
            for tb in thinking_blocks:
                cb, n = _sanitized(tb, PER_BLOCK_THINKING_CHARS); redacted += n
                clean_blocks.append(cb)
            digest = _thinking_digest(clean_blocks, max_thinking)
        else:
            digest = ""

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
            # safe_write (NOT atomic_write): this is RAW conversation text going
            # into the synced vault, so it must pass the privacy sanitizer. A
            # live GitLab-PAT-shaped string reached the remote through this path.
            red = safe_write(target, (existing if existing else header) + block)
            if red or redacted:
                log_debug("capture", f"redacted {max(red, 0) + redacted} secret(s) in {target.name}")
        return True
    except TimeoutError as e:
        log_debug("capture", f"lock timeout ws={ws} turn={turn_no}: {e}")
        return False
    except Exception as e:  # never break the Stop hook
        log_debug("capture", f"capture_turn failed ws={ws} turn={turn_no}: {e}")
        return False


def _ws_from_log_path(log_path: Path) -> str:
    """Derive the workspace from a session-log path
    (…/workspaces/<ws>/journal/sessions/<file>) so the append lock name
    matches capture_turn's `capture-{ws}`. Falls back to "default"."""
    try:
        parts = log_path.resolve().parts
        if "workspaces" in parts:
            i = parts.index("workspaces")
            if i + 1 < len(parts):
                return parts[i + 1]
    except Exception:
        pass
    return "default"


def append_review_block(log_path: str | Path, block_text: str) -> bool:
    """v4.7.1: append a block to a session log UNDER THE SAME LOCK capture_turn
    holds for its read-modify-write.

    The dispatched judge appends its `## [self-review]` block to the log while
    the main session keeps working — a raw Write/Edit races the next Stop's
    capture rewrite and one side's write is silently lost (a turn erased, or
    the review block deleted before _forget._salvage_reviews ever sees it).
    This is the serialization point; the rubric routes judges here via
    `python3 _capture.py --append-review <log>` with the block on stdin.

    Goes through safe_write like capture: raw review text enters the synced
    vault, so it must pass the privacy sanitizer. Never raises.
    """
    try:
        p = Path(log_path)
        ws = _ws_from_log_path(p)
        text = block_text if block_text.endswith("\n") else block_text + "\n"
        if not text.startswith("\n"):
            text = "\n" + text
        with file_lock(f"capture-{ws}", timeout=5.0):
            existing = ""
            if p.is_file():
                try:
                    existing = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    existing = ""
            p.parent.mkdir(parents=True, exist_ok=True)
            red = safe_write(p, existing + text)
            if red:
                log_debug("capture", f"redacted {red} secret(s) in {p.name} (review append)")
        return True
    except Exception as e:
        log_debug("capture", f"append_review_block failed for {log_path}: {e}")
        return False


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description="gowth-mem capture utilities (v4.7.1: locked review append)")
    ap.add_argument("--append-review", metavar="LOG_PATH",
                    help="append a review block (read from stdin) to a session log "
                         "under the capture lock — NEVER Write/Edit the log directly")
    args = ap.parse_args()
    if args.append_review:
        _block = sys.stdin.read()
        if _block.strip():
            print("ok" if append_review_block(args.append_review, _block) else "failed")
        else:
            print("failed: empty block on stdin")
    else:
        ap.print_help()
    sys.exit(0)
