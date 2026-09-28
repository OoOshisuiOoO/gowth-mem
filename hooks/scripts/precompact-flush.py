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
from _atomic import atomic_write, safe_write  # type: ignore
from _debug import log_debug  # type: ignore
from _home import active_workspace, journal_dir, workspace_dir  # type: ignore
from _lock import file_lock  # type: ignore

FLUSH_GRACE = 300  # seconds — recent flush window
MIN_USER_TURNS = 2  # below this, transcript has nothing substantive to flush
# v3.6: cap the raw safety-net snapshot small. Journals are the ephemeral
# hippocampal buffer (canon §3): `_forget.py` archives them after `raw_ttl_days`
# (7d). The dump is a recent-context safety net, NOT durable knowledge — so a
# tight cap keeps today's journal (loaded at bootstrap) cheap to read. Was
# 80_000, which let a single day's journal balloon past 1 MB.
RAW_DUMP_MAX_CHARS = 20_000


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


ASSISTANT_CHUNK_CHARS = 500   # v4.8: cap each assistant chunk in the dump


def _read_records(transcript_path: str) -> list:
    if not transcript_path:
        return []
    p = Path(transcript_path)
    if not p.is_file():
        return []
    out: list = []
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
                if isinstance(rec, dict):
                    out.append(rec)
    except OSError:
        return []
    return out


def _prompt_records(records: list) -> list:
    """v4.8 (holistic review H2): indexes of the records that are the USER's
    words, via `_capture._classify_record` — human prompts always, unconfirmed
    ones only when an assistant record answered them before the next prompt,
    machine records (notification relays, hook feedback) and non-prompts never.
    The old count took every `type:"user"` record with text (77 on the lead
    transcript vs 7 human)."""
    try:
        from _capture import _classify_record  # type: ignore
    except Exception:
        return []
    kinds = [(_classify_record(r) if not (isinstance(r, dict) and r.get("type") == "assistant") else "assistant")
             for r in records]
    keep: list = []
    for i, kind in enumerate(kinds):
        if kind == "human":
            keep.append(i)
        elif kind == "unconfirmed":
            for k in kinds[i + 1:]:
                if k == "assistant":
                    keep.append(i)
                    break
                if k in ("human", "unconfirmed", "machine"):
                    break
    return keep


def user_turn_count(transcript_path: str) -> int:
    """Count the user's substantive prompts in the transcript (see _prompt_records)."""
    return len(_prompt_records(_read_records(transcript_path)))


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
    """Read transcript JSONL and return the most recent user+assistant text turns,
    oldest-first, capped at `max_chars`. User turns = the user's own words
    (v4.8 classification); each assistant chunk is capped at ASSISTANT_CHUNK_CHARS."""
    records = _read_records(transcript_path)
    if not records:
        return ""
    try:
        from _capture import prompt_text  # type: ignore
    except Exception:
        return ""
    keep = set(_prompt_records(records))
    turns: list = []
    for i, rec in enumerate(records):
        if i in keep:
            text = prompt_text(rec).strip()
            if text:
                turns.append(("user", text))
        elif rec.get("type") == "assistant":
            text = _extract_text((rec.get("message") or {}).get("content")).strip()
            if not text:
                continue
            if len(text) > ASSISTANT_CHUNK_CHARS:
                text = text[:ASSISTANT_CHUNK_CHARS].rstrip() + f" [+{len(text) - ASSISTANT_CHUNK_CHARS} chars]"
            turns.append(("assistant", text))

    # Take from tail until budget exhausted, then re-reverse to chronological.
    selected: list = []
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
            "_Ephemeral safety-net snapshot (raw working memory). Distill the signal "
            "into topic files via `/mem-distill`; the raw is auto-archived after "
            "`journal.raw_ttl_days` (default 7d) by `_forget.py`._\n\n"
        )
        snapshot = header + text + "\n"

        with file_lock(f"journal-{ws}", timeout=5.0):
            existing = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
            # safe_write: raw transcript dump into the synced vault must be
            # sanitized (same defect class as _capture.py).
            red = safe_write(target, existing + snapshot)
            if red:
                log_debug("precompact-flush", f"redacted {red} secret(s)")
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
