"""Short-window dedup for journal/lesson appends (pattern from agentmemory).

Keeps a rolling SHA-256 set in `~/.gowth-mem/.dedup-window.json` with per-entry
expiry. Default TTL = 300s. Callers ask `seen_recently(text) -> bool` before
appending; if True, the caller MAY skip the write.

File format:
    {"window_seconds": 300, "entries": {"<sha256>": <unix_ts_float>, ...}}

Designed to FAIL OPEN: any IO/JSON failure returns False (no dedup), so a
broken window file never blocks user writes.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import atomic_write  # type: ignore
from _home import gowth_home  # type: ignore
from _lock import file_lock  # type: ignore

DEFAULT_WINDOW_SECONDS = 300
WHITESPACE_RE = re.compile(r"\s+")


def _window_path() -> Path:
    return gowth_home() / ".dedup-window.json"


def _normalize(text: str) -> str:
    return WHITESPACE_RE.sub(" ", text or "").strip().lower()


def _digest(text: str) -> str:
    return hashlib.sha256(_normalize(text).encode("utf-8", errors="ignore")).hexdigest()


def _load() -> dict:
    p = _window_path()
    if not p.is_file():
        return {"window_seconds": DEFAULT_WINDOW_SECONDS, "entries": {}}
    try:
        d = json.loads(p.read_text())
        d.setdefault("window_seconds", DEFAULT_WINDOW_SECONDS)
        d.setdefault("entries", {})
        return d
    except Exception:
        return {"window_seconds": DEFAULT_WINDOW_SECONDS, "entries": {}}


def _prune_expired(d: dict, now: float) -> dict:
    ttl = float(d.get("window_seconds") or DEFAULT_WINDOW_SECONDS)
    cutoff = now - ttl
    d["entries"] = {k: v for k, v in d.get("entries", {}).items() if float(v) >= cutoff}
    return d


def seen_recently(text: str) -> bool:
    """Return True if *text* (normalized) was recorded within the window."""
    if not isinstance(text, str) or not text.strip():
        return False
    digest = _digest(text)
    try:
        with file_lock("dedup", timeout=1.0):
            d = _prune_expired(_load(), time.time())
            return digest in d.get("entries", {})
    except Exception:
        return False


def record(text: str) -> None:
    """Add *text* to the window (silently no-ops on lock contention)."""
    if not isinstance(text, str) or not text.strip():
        return
    digest = _digest(text)
    now = time.time()
    try:
        with file_lock("dedup", timeout=1.0):
            d = _prune_expired(_load(), now)
            d["entries"][digest] = now
            try:
                atomic_write(_window_path(), json.dumps(d, indent=2))
            except Exception:
                pass
    except Exception:
        pass


def check_and_record(text: str) -> bool:
    """Atomic seen-then-record. Returns True if duplicate (caller should skip)."""
    if not isinstance(text, str) or not text.strip():
        return False
    digest = _digest(text)
    now = time.time()
    try:
        with file_lock("dedup", timeout=1.0):
            d = _prune_expired(_load(), now)
            entries = d.setdefault("entries", {})
            if digest in entries:
                return True
            entries[digest] = now
            try:
                atomic_write(_window_path(), json.dumps(d, indent=2))
            except Exception:
                pass
            return False
    except Exception:
        return False
