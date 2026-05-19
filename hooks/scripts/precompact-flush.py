#!/usr/bin/env python3
"""PreCompact hook (v3.5.1): best-effort transcript dump, NEVER blocks /compact.

v3.5.1 — REMOVES the fallback HARD-BLOCK. The hook now exits 0 with empty
stdout under every failure path. Rationale: auto-compact fires when context
is overflowing; blocking it strands the user. The v3.5 fallback that
printed `{"decision": "block"}` when raw-dump failed contradicted the v3.5
promise of "zero manual retries". Failures are surfaced via `log_debug`
(see `_debug.py`) instead.

v3.5 baseline (kept):
  Deterministic raw-dump of recent transcript turns into
  `<ws>/journal/<today>.md`. Classification (decisions/exp/ref → topic
  files) is deferred to `/mem-distill`, runnable when context is fresh.

Pass-through paths (all return 0, no stdout):
  1. Transcript has < MIN_USER_TURNS substantive user prompts (session-start)
  2. recently_flushed() — any *.md under workspace touched in last FLUSH_GRACE
  3. Workspace not materialized (fresh install, before /mem-install)
  4. extract_recent_turns returned empty (tool-result-only transcript)
  5. raw_dump_to_journal raised — logged, not surfaced
  6. Happy path — dump succeeded
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import atomic_write  # type: ignore
from _debug import log_debug  # type: ignore
from _home import active_workspace, journal_dir, workspace_dir  # type: ignore
from _lock import file_lock  # type: ignore

FLUSH_GRACE = 300  # seconds — recent flush window
MIN_USER_TURNS = 2  # below this, transcript has nothing substantive to flush
RAW_DUMP_MAX_CHARS = 80_000  # cap snapshot size to avoid huge journal entries


def recently_flushed(grace: int = FLUSH_GRACE) -> bool:
    """True if any markdown under the active workspace was modified within
    `grace` seconds — heuristic that a flush just completed."""
    try:
        wsd = workspace_dir(active_workspace())
    except Exception:
        return False
    if not wsd.is_dir():
        return False
    cutoff = time.time() - grace
    for p in wsd.rglob("*.md"):
        try:
            if p.stat().st_mtime > cutoff:
                return True
        except OSError:
            continue
    return False


def user_turn_count(transcript_path: str) -> int:
    """Count substantive user prompts in the transcript.

    A 'user turn' is a `type: "user"` record whose `message.content` carries
    real text (string OR a `text` part). Tool-result user records and entries
    without text content are excluded — they do not represent user input.
    """
    if not transcript_path:
        return 0
    p = Path(transcript_path)
    if not p.is_file():
        return 0
    n = 0
    try:
        with p.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "user":
                    continue
                content = (rec.get("message") or {}).get("content")
                if isinstance(content, str):
                    if content.strip():
                        n += 1
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            if (part.get("text") or "").strip():
                                n += 1
                                break
    except OSError:
        return 0
    return n


def _extract_text(content) -> str:
    """Return joined plain text from a message.content (str or list-of-parts)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict) and p.get("type") == "text":
                t = p.get("text") or ""
                if t.strip():
                    parts.append(t)
        return "\n".join(parts)
    return ""


def extract_recent_turns(transcript_path: str, max_chars: int = RAW_DUMP_MAX_CHARS) -> str:
    """Read transcript JSONL and return the most recent substantive user+assistant
    text turns, oldest-first, capped at `max_chars`."""
    if not transcript_path:
        return ""
    p = Path(transcript_path)
    if not p.is_file():
        return ""
    turns: list[tuple[str, str]] = []
    try:
        with p.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                role = rec.get("type")
                if role not in ("user", "assistant"):
                    continue
                content = (rec.get("message") or {}).get("content")
                text = _extract_text(content).strip()
                if text:
                    turns.append((role, text))
    except OSError:
        return ""

    # Take from tail until budget exhausted, then re-reverse to chronological.
    selected: list[str] = []
    total = 0
    for role, text in reversed(turns):
        chunk = f"### [{role}]\n\n{text}\n"
        if total + len(chunk) > max_chars and selected:
            break
        selected.append(chunk)
        total += len(chunk)
    selected.reverse()
    return "\n".join(selected)


def raw_dump_to_journal(text: str, ws: str) -> bool:
    """Append a raw transcript snapshot to <ws>/journal/<today>.md atomically.

    Returns True on success, False on any failure (so caller can fall back).
    """
    if not text.strip():
        return False
    try:
        jd = journal_dir(ws)
        jd.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        timestamp = now.strftime("%H:%M:%S")
        target = jd / f"{today}.md"

        header = (
            f"\n\n## [auto-precompact-dump] {today} {timestamp}\n\n"
            "_Pre-compact snapshot. Run `/mem-distill` to classify into topic files._\n\n"
        )
        snapshot = header + text + "\n"

        with file_lock(f"journal-{ws}", timeout=5.0):
            existing = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
            atomic_write(target, existing + snapshot)
        return True
    except Exception as e:
        log_debug("precompact-flush", f"raw_dump failed: {e}")
        return False


def read_payload() -> dict:
    """Read the PreCompact JSON payload from stdin. Empty/invalid → {}."""
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def main() -> int:
    payload = read_payload()
    transcript_path = payload.get("transcript_path", "") if isinstance(payload, dict) else ""

    # Pass-through if transcript has nothing substantive to flush yet.
    if user_turn_count(transcript_path) < MIN_USER_TURNS:
        return 0

    # Pass-through if a flush already happened recently (mtime heuristic).
    if recently_flushed():
        return 0

    # v3.5: deterministic raw-dump → pass /compact through with zero manual retries.
    # Skip silently if ~/.gowth-mem/ isn't materialized (per CLAUDE.md graceful-missing rule).
    try:
        ws = active_workspace()
        ws_ok = bool(ws) and workspace_dir(ws).is_dir()
    except Exception as e:
        log_debug("precompact-flush", f"active_workspace failed: {e}")
        ws, ws_ok = "", False

    if ws_ok:
        text = extract_recent_turns(transcript_path)
        if not text:
            log_debug("precompact-flush", "no substantive turns extracted; pass-through")
        elif not raw_dump_to_journal(text, ws):
            log_debug("precompact-flush", "raw_dump_to_journal failed; pass-through")
    else:
        log_debug("precompact-flush", "workspace not materialized; pass-through")

    # v3.5.1: NEVER block /compact. Failures are logged, not surfaced. Auto-compact
    # firing on context overflow must not be blockable — the user's recovery path
    # (re-run /compact) doesn't exist when context is already gone.
    return 0


if __name__ == "__main__":
    sys.exit(main())
