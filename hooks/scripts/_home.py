"""Resolve the global gowth-mem root: ~/.gowth-mem/.

v2.0 centralizes everything in a single home-directory folder so memory is
shared across projects and machines (synced via git). v1.0 used a per-workspace
.gowth-mem/ folder; we keep a one-time fallback to that path for users who
haven't migrated yet.

Resolution order:
  1. Env var GOWTH_MEM_HOME (explicit override)
  2. ~/.gowth-mem/ if it exists
  3. <workspace>/.gowth-mem/ if v1.0 fallback applies (transition aid)
  4. ~/.gowth-mem/ (default; will be created on first write)

Migration aid: when fallback fires, _home() prints a deprecation note ONCE
per session via a sentinel in /tmp.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


SENTINEL = Path("/tmp/.gowth-mem-deprecation-warned")


def _warn_once(workspace_path: Path) -> None:
    if SENTINEL.is_file():
        return
    try:
        SENTINEL.write_text("1")
    except Exception:
        pass
    print(
        f"[gowth-mem] DEPRECATION: using legacy per-workspace path {workspace_path}. "
        f"Run /mem-migrate-global to move into ~/.gowth-mem/.",
        file=sys.stderr,
    )


def gowth_home(workspace: Path | None = None) -> Path:
    """Return the gowth-mem root directory."""
    explicit = os.environ.get("GOWTH_MEM_HOME")
    if explicit:
        return Path(explicit).expanduser()

    home = Path.home() / ".gowth-mem"
    if home.is_dir():
        return home

    if workspace is not None:
        ws = Path(workspace)
        legacy = ws / ".gowth-mem"
        if legacy.is_dir():
            _warn_once(legacy)
            return legacy

    return home


def docs_dir(workspace: Path | None = None) -> Path:
    return gowth_home(workspace) / "docs"


def topics_dir(workspace: Path | None = None) -> Path:
    return gowth_home(workspace) / "topics"


def journal_dir(workspace: Path | None = None) -> Path:
    return gowth_home(workspace) / "journal"


def skills_dir(workspace: Path | None = None) -> Path:
    return gowth_home(workspace) / "skills"


def agents_md(workspace: Path | None = None) -> Path:
    return gowth_home(workspace) / "AGENTS.md"


def settings_path(workspace: Path | None = None) -> Path:
    return gowth_home(workspace) / "settings.json"


def config_path(workspace: Path | None = None) -> Path:
    return gowth_home(workspace) / "config.json"


def state_path(workspace: Path | None = None) -> Path:
    return gowth_home(workspace) / "state.json"


def index_db(workspace: Path | None = None) -> Path:
    return gowth_home(workspace) / "index.db"


def conflict_md(workspace: Path | None = None) -> Path:
    return gowth_home(workspace) / "SYNC-CONFLICT.md"


def locks_dir(workspace: Path | None = None) -> Path:
    return gowth_home(workspace) / ".locks"
