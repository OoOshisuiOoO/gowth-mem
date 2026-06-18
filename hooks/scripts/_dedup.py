"""Short-window dedup for journal/lesson appends (pattern from agentmemory).

Keeps a rolling SHA-256 set in `~/.gowth-mem/.dedup-window.json` with per-entry
expiry. Default TTL = 300s. Callers ask `seen_recently(text) -> bool` before
appending; if True, the caller MAY skip the write.

File format:
    {"window_seconds": 300, "entries": {"<sha256>": <unix_ts_float>, ...}}

Designed to FAIL OPEN: any IO/JSON failure returns False (no dedup), so a
broken window file never blocks user writes.

v3.4 addition: tag-aware dedup. Hash is computed over (tag, normalized_content)
so `[decision] foo` and `[exp] foo` produce DIFFERENT hashes and both are stored.
`is_duplicate(ws_root, tag, content)` checks the SQLite index DB for any prior
row with the same (tag, content_hash) across all files and sessions — cross-file,
cross-time dedup layer on top of the 300s hot-path cache.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import atomic_write  # type: ignore
from _home import gowth_home, index_db  # type: ignore
from _lock import file_lock  # type: ignore

DEFAULT_WINDOW_SECONDS = 300
WHITESPACE_RE = re.compile(r"\s+")
TAG_RE = re.compile(r"^(?:#{2,6}\s*)?\[([a-z-]+)\]\s*")  # v3.8: bullet OR `## [type]` block


def _window_path() -> Path:
    return gowth_home() / ".dedup-window.json"


def _normalize(text: str) -> str:
    return WHITESPACE_RE.sub(" ", text or "").strip().lower()


def _extract_tag(content: str) -> str:
    """Return the leading [tag] value from content, or '' if absent."""
    m = TAG_RE.match((content or "").lstrip())
    return m.group(1) if m else ""


def _digest(text: str) -> str:
    """Legacy content-only digest (used by existing seen_recently / record API)."""
    return hashlib.sha256(_normalize(text).encode("utf-8", errors="ignore")).hexdigest()


def _tag_digest(tag: str, content: str) -> str:
    """v3.4: hash over (tag, normalized_content) so same text with different tag
    produces a different hash — [decision] foo != [exp] foo.
    """
    norm = _normalize(content)
    key = f"{tag}\x00{norm}"
    return hashlib.sha256(key.encode("utf-8", errors="ignore")).hexdigest()


def is_duplicate(ws_root: str | Path, tag: str, content: str) -> bool:
    """v3.4: cross-file, cross-session duplicate check backed by the SQLite index.

    Returns True if ANY row in the chunks table has the same (tag, content_hash)
    as the supplied (tag, content) pair. Falls open on any error.

    The content_hash stored in the `hash` column is SHA-1[:16] over raw content
    (set by _index.py). We therefore recompute it the same way here so the check
    is consistent without requiring a full-text scan.
    """
    import hashlib as _hl
    try:
        db_path = index_db()
        if not db_path.is_file():
            return False
        norm = _normalize(content)
        # Use the same SHA-1[:16] hash that _index.py stores in the hash column.
        raw_hash = _hl.sha1(content.encode("utf-8", errors="ignore")).hexdigest()[:16]
        with sqlite3.connect(str(db_path)) as db:
            db.execute("PRAGMA busy_timeout=2000")
            # Check column existence first — DB might be pre-migration.
            cols = {row[1] for row in db.execute("PRAGMA table_info(chunks)")}
            if "tag" not in cols:
                return False
            row = db.execute(
                "SELECT 1 FROM chunks WHERE tag=? AND hash=? LIMIT 1",
                (tag, raw_hash),
            ).fetchone()
            return row is not None
    except Exception:
        return False


def _fresh() -> dict:
    return {"window_seconds": DEFAULT_WINDOW_SECONDS, "entries": {}}


def _load() -> dict:
    """Read the window file with structural self-healing.

    Any of: missing file, JSON parse failure, non-dict root, non-dict `entries`,
    non-numeric values → returns a fresh empty window. A poisoned file (e.g.
    `entries` as a list, TTL as a string) used to silently disable dedup for
    the lifetime of the install; now it's transparently repaired on next write.
    """
    p = _window_path()
    if not p.is_file():
        return _fresh()
    try:
        d = json.loads(p.read_text())
        if not isinstance(d, dict):
            return _fresh()
        ents = d.get("entries")
        if not isinstance(ents, dict):
            ents = {}
        clean_ents = {}
        for k, v in ents.items():
            if not isinstance(k, str):
                continue
            try:
                clean_ents[k] = float(v)
            except (TypeError, ValueError):
                continue
        ws = d.get("window_seconds")
        try:
            window = float(ws) if ws is not None else DEFAULT_WINDOW_SECONDS
        except (TypeError, ValueError):
            window = DEFAULT_WINDOW_SECONDS
        return {"window_seconds": window, "entries": clean_ents}
    except Exception:
        return _fresh()


def _prune_expired(d: dict, now: float) -> dict:
    ttl = float(d.get("window_seconds") or DEFAULT_WINDOW_SECONDS)
    cutoff = now - ttl
    d["entries"] = {k: v for k, v in d.get("entries", {}).items() if float(v) >= cutoff}
    return d


def seen_recently(text: str) -> bool:
    """Return True if *text* (normalized) was recorded within the window.

    v3.4: uses the same tag-aware digest as check_and_record so the read side
    matches the write side (untagged text uses tag="").
    """
    if not isinstance(text, str) or not text.strip():
        return False
    tag = _extract_tag(text)
    digest = _tag_digest(tag, text)
    try:
        with file_lock("dedup", timeout=1.0):
            d = _prune_expired(_load(), time.time())
            return digest in d.get("entries", {})
    except Exception:
        return False


def record(text: str) -> None:
    """Add *text* to the window (silently no-ops on lock contention).

    v3.4: uses tag-aware digest so paired seen_recently()/check_and_record()
    lookups hit the same entry regardless of which writer recorded it.
    """
    if not isinstance(text, str) or not text.strip():
        return
    tag = _extract_tag(text)
    digest = _tag_digest(tag, text)
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
    """Atomic seen-then-record. Returns True if duplicate (caller should skip).

    v3.4: uses tag-aware digest so [decision] foo and [exp] foo are NOT
    considered duplicates of each other in the hot-path 300s window.
    """
    if not isinstance(text, str) or not text.strip():
        return False
    tag = _extract_tag(text)
    digest = _tag_digest(tag, text)
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
