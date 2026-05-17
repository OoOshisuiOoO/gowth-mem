"""Audit-log helper for destructive operations (pattern from agentmemory).

Today: only `_prune.py` deletes user entries silently; that's a footgun. This
helper appends one JSON-line per delete to `~/.gowth-mem/.audit/prune-<YYYY-MM>.log`
so users can recover what was dropped and when, without inflating the synced
tree (audit dir is gitignored).

Each line:
    {"ts": "<iso8601>", "op": "prune-delete", "file": "<rel>",
     "reason": "<superseded|expired|duplicate-newer-dropped>",
     "preview": "<<=80 chars>"}

Permissions:
  - `.audit/` directory: `0700` (owner-only) — previews may contain leaked
    content that the privacy filter missed; protect from other local users.
  - `prune-YYYY-MM.log`: `0600` — same reasoning, also lifted from stdlib
    `tempfile.mkstemp` default.

Fail open: any IO failure is swallowed (the prune itself must always succeed).
"""
from __future__ import annotations

import json
import os
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


def _open_log_secure(path: Path):
    """Open the log file in append mode with 0600 perms; survives a pre-existing
    file with relaxed perms by chmod'ing on open."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except Exception:
        pass
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.fchmod(fd, 0o600)
    except Exception:
        pass
    # buffering=1 → line-buffered; survives a crash mid-stream
    return os.fdopen(fd, "a", encoding="utf-8", buffering=1)


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
        with _open_log_secure(_current_log()) as f:
            f.write(line + "\n")
            try:
                f.flush()
                os.fsync(f.fileno())
            except Exception:
                pass
    except Exception:
        pass
