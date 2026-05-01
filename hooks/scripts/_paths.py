"""Path resolution helper — v1.0 centralized vs v0.9 legacy fallback.

Returns canonical paths for AGENTS.md and docs/ regardless of layout.

v1.0 layout (centralized):     v0.9 layout (legacy):
  .gowth-mem/AGENTS.md           AGENTS.md
  .gowth-mem/docs/*.md           docs/*.md

If `.gowth-mem/AGENTS.md` exists OR `.gowth-mem/docs/` is a directory, treat
the workspace as v1.0. Otherwise fall back to v0.9 root paths.
"""
from __future__ import annotations

from pathlib import Path


def is_v1_layout(workspace: Path) -> bool:
    gm = workspace / ".gowth-mem"
    if not gm.is_dir():
        return False
    return (gm / "AGENTS.md").is_file() or (gm / "docs").is_dir()


def resolve_root(workspace: Path) -> Path:
    """Return the directory containing AGENTS.md and docs/."""
    return workspace / ".gowth-mem" if is_v1_layout(workspace) else workspace


def docs_root(workspace: Path) -> Path:
    """Return the directory holding docs/* files."""
    return resolve_root(workspace) / "docs"


def agents_md(workspace: Path) -> Path:
    return resolve_root(workspace) / "AGENTS.md"
