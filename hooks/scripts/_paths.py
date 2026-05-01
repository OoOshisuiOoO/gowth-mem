"""v2.0 path resolver — thin wrapper over _home.py.

Kept for backward compatibility. New code should import _home directly.

Resolution order (see _home.py):
  1. GOWTH_MEM_HOME env var
  2. ~/.gowth-mem/
  3. <workspace>/.gowth-mem/ (one-time deprecation warning)
  4. ~/.gowth-mem/ (created on first write)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _home import (  # type: ignore
    agents_md,
    docs_dir,
    gowth_home,
    topics_dir,
)


def is_v1_layout(workspace: Path) -> bool:
    """Compatibility: legacy callers expect v1.0 detection."""
    gh = gowth_home(workspace)
    return gh.is_dir()


def resolve_root(workspace: Path) -> Path:
    return gowth_home(workspace)


def docs_root(workspace: Path) -> Path:
    return docs_dir(workspace)
