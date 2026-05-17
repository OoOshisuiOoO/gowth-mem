"""Atomic file writes via tempfile + os.replace.

Prevents truncated/half-written files when two sessions race on the same
markdown file. POSIX guarantees os.replace is atomic on the same filesystem.

`safe_write` is the privacy-aware chokepoint: any `.md` write under
`workspaces/` or `shared/` (i.e. anything that syncs) MUST go through this
function. It calls `_privacy.sanitize` before persisting and returns the
redaction count so callers can log when content was mutated.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically. Creates parent dirs if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


_SYNCED_DIRS = ("workspaces", "shared")
_MARKDOWN_SUFFIXES = {".md", ".markdown"}


def _is_synced_markdown(path: Path) -> bool:
    """True iff *path* is a markdown file under a synced subtree.

    Lazy import of `_home.gowth_home` avoids an import cycle (some helpers
    that use `atomic_write` are imported before `_home` is fully loaded).
    """
    if path.suffix.lower() not in _MARKDOWN_SUFFIXES:
        return False
    try:
        from _home import gowth_home  # type: ignore  # noqa: WPS433
        gh = gowth_home().resolve()
    except Exception:
        return False
    try:
        rel = path.resolve().relative_to(gh)
    except Exception:
        return False
    return rel.parts and rel.parts[0] in _SYNCED_DIRS


def safe_write(path: Path, content: str) -> int:
    """Privacy-aware atomic write. Returns redaction count (0 = clean, -1 = filter bypassed).

    Sanitizes `.md` writes under `workspaces/` / `shared/` (anything that
    syncs to the git remote). Non-markdown / non-synced files pass through
    `atomic_write` unchanged.

    Callers can inspect the return value to log mutations. Writes always
    proceed — sanitize failures fall through to the original content so user
    data is never lost.
    """
    if not _is_synced_markdown(path):
        atomic_write(path, content)
        return 0
    try:
        from _privacy import sanitize  # type: ignore  # noqa: WPS433
    except Exception:
        atomic_write(path, content)
        return 0
    cleaned, n = sanitize(content)
    atomic_write(path, cleaned if isinstance(cleaned, str) else content)
    if n > 0:
        try:
            print(
                f"INFO: _privacy.sanitize redacted {n} secret(s) before writing {path}",
                file=sys.stderr,
            )
        except Exception:
            pass
    return n
