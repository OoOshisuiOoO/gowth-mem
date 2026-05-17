"""Audit-log helper for destructive operations (pattern from agentmemory).

Today: only `_prune.py` deletes user entries silently; that's a footgun. This
helper appends one JSON-line per delete to `~/.gowth-mem/.audit/prune-<YYYY-MM>.log`
so users can recover what was dropped and when, without inflating the synced
tree (audit dir is gitignored).

Each line:
    {"ts": "<iso8601>", "op": "prune-delete", "file": "<rel>",
     "reason": "<superseded|expired|duplicate>", "preview": "<<=80 chars>"}

Fail open: any IO failure is swallowed (the prune itself must always succeed).
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _home import gowth_home  # type: ignore

PREVIEW_MAX = 80


def _audit_dir() -> Path:
    return gowth_home() / ".audit"


def _current_log() -> Path:
    return _audit_dir() / f"prune-{date.today().strftime('%Y-%m')}.log"


def _preview(text: str) -> str:
    if not isinstance(text, str):
        return ""
    one_line = " ".join(text.split())
    return one_line[:PREVIEW_MAX]


def log_prune_delete(rel_path: str, reason: str, entry_text: str) -> None:
    """Append one audit line for a prune deletion. Silent on failure."""
    try:
        line = json.dumps({
            "ts": datetime.now().isoformat(timespec="seconds"),
            "op": "prune-delete",
            "file": rel_path,
            "reason": reason,
            "preview": _preview(entry_text),
        }, ensure_ascii=False)
        d = _audit_dir()
        d.mkdir(parents=True, exist_ok=True)
        with _current_log().open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
