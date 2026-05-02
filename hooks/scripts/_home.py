"""Resolve the global gowth-mem root + workspace-scoped paths.

v2.2 layout:
  ~/.gowth-mem/
  ├── AGENTS.md  settings.json  config.json  state.json  index.db  .locks/
  ├── shared/{_MAP.md, secrets.md, tools.md, files.md, skills/<slug>.md}
  └── workspaces/<ws>/{workspace.json, AGENTS.md, _MAP.md, docs/, topics/, journal/, skills/}

Active-workspace resolution (first match wins):
  1. Env GOWTH_WORKSPACE=<name>
  2. Session-scoped file `<gowth_home>/.session-workspace` (set by /mem-workspace)
  3. config.json.workspace_map glob match against $PWD
  4. config.json.active_workspace
  5. "default"

GOWTH_MEM_HOME env still overrides root.
"""
from __future__ import annotations

import fnmatch
import json
import os
import sys
from pathlib import Path

SESSION_WS_FILE = ".session-workspace"
DEFAULT_WORKSPACE = "default"


# ─── root ───────────────────────────────────────────────────────────────

def gowth_home(workspace: Path | str | None = None) -> Path:
    """Return the gowth-mem root directory.

    The `workspace` parameter is accepted for backward compatibility with
    v1.0/v2.0 call sites and is ignored in v2.2 (no per-workspace fallback).
    """
    explicit = os.environ.get("GOWTH_MEM_HOME")
    if explicit:
        return Path(explicit).expanduser()
    return Path.home() / ".gowth-mem"


# ─── config readers ─────────────────────────────────────────────────────

def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def read_config() -> dict:
    """Public reader for ~/.gowth-mem/config.json (returns {} on missing/invalid)."""
    return _read_json(gowth_home() / "config.json")


def read_settings() -> dict:
    """Public reader for ~/.gowth-mem/settings.json (returns {} on missing/invalid)."""
    return _read_json(gowth_home() / "settings.json")


# ─── active-workspace resolution ────────────────────────────────────────

def _read_session_workspace() -> str | None:
    p = gowth_home() / SESSION_WS_FILE
    if not p.is_file():
        return None
    try:
        v = p.read_text().strip()
        return v or None
    except Exception:
        return None


def write_session_workspace(name: str) -> None:
    """Persist a session-scoped workspace switch (cleared by clear_session_workspace)."""
    p = gowth_home() / SESSION_WS_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(name.strip())


def clear_session_workspace() -> None:
    p = gowth_home() / SESSION_WS_FILE
    try:
        p.unlink()
    except FileNotFoundError:
        pass


def _match_glob(cwd_str: str, pattern: str) -> bool:
    """Match a path against a workspace_map glob.

    Supports the common ``/prefix/**`` form (treated as `prefix` plus any
    descendant) AND vanilla fnmatch for bare patterns. We try both because
    fnmatch alone misses ``cwd == prefix`` exact matches (no trailing slash).
    """
    # Direct equality
    if cwd_str == pattern.rstrip("/*"):
        return True
    # `<prefix>/**` form
    if pattern.endswith("/**"):
        prefix = pattern[:-3]  # strip "/**"
        if cwd_str == prefix or cwd_str.startswith(prefix + "/"):
            return True
    if pattern.endswith("/*"):
        prefix = pattern[:-2]
        if cwd_str == prefix or cwd_str.startswith(prefix + "/"):
            return True
    # Trailing slash variant for fnmatch
    if fnmatch.fnmatch(cwd_str, pattern) or fnmatch.fnmatch(cwd_str + "/", pattern):
        return True
    return False


def active_workspace(cwd: Path | None = None) -> str:
    """Resolve the active workspace name. See module docstring for order."""
    env = os.environ.get("GOWTH_WORKSPACE", "").strip()
    if env:
        return env
    sess = _read_session_workspace()
    if sess:
        return sess
    cfg = read_config()
    settings = read_settings()
    if settings.get("workspace", {}).get("auto_detect_from_cwd", True):
        cwd_str = str((cwd or Path.cwd()).resolve())
        ws_map = cfg.get("workspace_map", {}) or {}
        for pattern, name in ws_map.items():
            if _match_glob(cwd_str, pattern):
                return name
    return cfg.get("active_workspace") or settings.get("workspace", {}).get("default") or DEFAULT_WORKSPACE


# ─── shared/ paths ──────────────────────────────────────────────────────

def shared_dir() -> Path:
    return gowth_home() / "shared"


def shared_moc() -> Path:
    return shared_dir() / "_MAP.md"


def secrets_md() -> Path:
    return shared_dir() / "secrets.md"


def shared_tools_md() -> Path:
    return shared_dir() / "tools.md"


def shared_files_md() -> Path:
    return shared_dir() / "files.md"


def shared_skills_dir() -> Path:
    return shared_dir() / "skills"


# ─── workspaces/ paths ──────────────────────────────────────────────────

def workspaces_root() -> Path:
    return gowth_home() / "workspaces"


def workspaces_registry_moc() -> Path:
    return workspaces_root() / "_MAP.md"


def workspace_dir(ws: str | None = None) -> Path:
    return workspaces_root() / (ws or active_workspace())


def workspace_meta(ws: str | None = None) -> Path:
    return workspace_dir(ws) / "workspace.json"


def workspace_agents_md(ws: str | None = None) -> Path:
    return workspace_dir(ws) / "AGENTS.md"


def workspace_moc(ws: str | None = None) -> Path:
    return workspace_dir(ws) / "_MAP.md"


def topics_dir(ws: Path | str | None = None) -> Path:
    """Workspace topics dir. v2.0 callers passing a `Path` (legacy workspace) get the active ws."""
    if isinstance(ws, Path):
        ws = None  # ignore legacy arg
    return workspace_dir(ws) / "topics"


def docs_dir(ws: Path | str | None = None) -> Path:
    if isinstance(ws, Path):
        ws = None
    return workspace_dir(ws) / "docs"


def journal_dir(ws: Path | str | None = None) -> Path:
    if isinstance(ws, Path):
        ws = None
    return workspace_dir(ws) / "journal"


def skills_dir(ws: Path | str | None = None, shared: bool = False) -> Path:
    if shared:
        return shared_skills_dir()
    if isinstance(ws, Path):
        ws = None
    return workspace_dir(ws) / "skills"


# ─── per-workspace docs convenience ─────────────────────────────────────

def handoff_md(ws: str | None = None) -> Path:
    return docs_dir(ws) / "handoff.md"


def workspace_exp_md(ws: str | None = None) -> Path:
    return docs_dir(ws) / "exp.md"


def workspace_ref_md(ws: str | None = None) -> Path:
    return docs_dir(ws) / "ref.md"


def workspace_tools_md(ws: str | None = None) -> Path:
    return docs_dir(ws) / "tools.md"


def workspace_files_md(ws: str | None = None) -> Path:
    return docs_dir(ws) / "files.md"


# ─── global files ───────────────────────────────────────────────────────

def agents_md(workspace: Path | str | None = None) -> Path:
    """Global AGENTS.md. Backward-compat: ignores legacy workspace arg."""
    return gowth_home() / "AGENTS.md"


def settings_path(workspace: Path | str | None = None) -> Path:
    return gowth_home() / "settings.json"


def config_path(workspace: Path | str | None = None) -> Path:
    return gowth_home() / "config.json"


def state_path(workspace: Path | str | None = None) -> Path:
    return gowth_home() / "state.json"


def index_db(workspace: Path | str | None = None) -> Path:
    return gowth_home() / "index.db"


def conflict_md(workspace: Path | str | None = None) -> Path:
    return gowth_home() / "SYNC-CONFLICT.md"


def locks_dir(workspace: Path | str | None = None) -> Path:
    return gowth_home() / ".locks"


# ─── enumeration helpers ────────────────────────────────────────────────

def list_workspaces() -> list[str]:
    """Return workspace names (folders under workspaces/ that contain workspace.json),
    excluding _archive."""
    root = workspaces_root()
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        if (d / "workspace.json").is_file():
            out.append(d.name)
    return out
