"""Resolve the global gowth-mem root + workspace-scoped paths.

v3.0 layout (topic-folder + dated-aspect):
  ~/.gowth-mem/
  ├── AGENTS.md  settings.json  config.json  state.json  index.db  .locks/  .backup/
  ├── shared/{_MAP.md, secrets.md, tools.md, files.md, skills/<slug>.md}
  └── workspaces/<ws>/{workspace.json, AGENTS.md, _MAP.md, docs/, journal/, skills/,
                       <slug>/{00-README.md, YYYY-MM-DD-<aspect>.md, lessons.md}}
       v3.0: topic = FOLDER; files inside are aspects. Reserved subdirs:
       docs, journal, skills, research. Reserved files at root: _MAP.md, AGENTS.md, workspace.json.
       Reserved filenames INSIDE a topic folder: 00-README.md (MOC), lessons.md (ledger).

Read-path is permissive: detects v3 (`<slug>/00-README.md`), v2.4 folder-note
(`<slug>/<slug>.md`), and v2.3 flat (`<slug>.md`) layouts. Write-path is strict v3.

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

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import atomic_write  # type: ignore

SESSION_WS_FILE = ".session-workspace"
DEFAULT_WORKSPACE = "default"


# ─── root ───────────────────────────────────────────────────────────────

def gowth_home() -> Path:
    """Return the gowth-mem root directory ($GOWTH_MEM_HOME or ~/.gowth-mem)."""
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


# ─── typed settings access (v4.8) ────────────────────────────────────────

_TRUE_WORDS = frozenset({"true", "1", "yes", "on"})
_FALSE_WORDS = frozenset({"false", "0", "no", "off", ""})


def coerce_bool(value, default: bool) -> bool:
    """bool for a hand-edited JSON knob. A JSON string "false" is False, an
    explicit null or anything unrecognisable is the default (v4.7.1 rule,
    now applied to every boolean read)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in _TRUE_WORDS:
            return True
        if v in _FALSE_WORDS:
            return False
    return default


def setting(path: str, kind: type = str, default=None, settings: "dict | None" = None):
    """Read one dotted settings key with PER-KEY fallback.

    `setting("reflection.enabled", bool, True)` — a missing key, an explicit
    null, a non-dict section, or a value that cannot be coerced to `kind`
    yields `default` for THAT key only. Before v4.8 a malformed sibling
    (`turn_interval: "15m"`) reset its whole section to defaults, silently
    re-enabling privacy opt-outs (holistic review M1). `settings` is an
    optional snapshot so a hook parses settings.json once per event.
    """
    node = read_settings() if settings is None else settings
    for part in path.split("."):
        if not isinstance(node, dict):
            return default
        node = node.get(part)
    if node is None:
        return default
    if kind is bool:
        return coerce_bool(node, bool(default))
    if kind is int:
        if isinstance(node, bool):
            return default
        try:
            return int(node)
        except (TypeError, ValueError):
            return default
    if kind is float:
        if isinstance(node, bool):
            return default
        try:
            return float(node)
        except (TypeError, ValueError):
            return default
    if kind is str:
        return node if isinstance(node, str) else default
    return node if isinstance(node, kind) else default


# ─── host context limit (v4.8) ────────────────────────────────────────────

# Claude Code (measured on 2.1.283) persists any hook additionalContext or
# SessionStart stdout of >= 10,000 chars to a tool-results file and shows the
# model a 2,000-char preview. 9,500 chars is delivered in full. Every emitter
# clamps below this so nothing gowth-mem injects can fall into that path.
HOOK_CONTEXT_MAX = 9000
_CLAMP_MARKER = "[gowth-mem: truncated to fit the host limit]"


def clamp_context(text: str, limit: int = HOOK_CONTEXT_MAX) -> str:
    """Return `text` unchanged when shorter than `limit`, else cut at the last
    line boundary that leaves room for the marker, and append the marker."""
    if not isinstance(text, str) or len(text) < limit:
        return text
    room = max(0, limit - len(_CLAMP_MARKER) - 2)
    head = text[:room]
    nl = head.rfind("\n")
    if nl > 0:
        head = head[:nl]
    return head + "\n" + _CLAMP_MARKER


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
    atomic_write(p, name.strip())


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
    if setting("workspace.auto_detect_from_cwd", bool, True, settings=settings):
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


# Reserved names under a workspace dir — NOT topics, NOT scannable as topic content
# v4.8: `memory/` holds MEMORY.md (Claude Code auto-memory, pointed here via
# autoMemoryDirectory) plus whatever Claude writes beside it — never a topic.
RESERVED_SUBDIRS = frozenset({"docs", "journal", "skills", "research", "memory"})
RESERVED_FILES = frozenset({"_MAP.md", "AGENTS.md", "workspace.json"})

# v3.0: reserved filenames INSIDE a topic folder
TOPIC_README = "00-README.md"
TOPIC_LESSONS = "lessons.md"
RESERVED_TOPIC_FILES = frozenset({TOPIC_README, TOPIC_LESSONS, "_MAP.md"})

# v3.0: dated-aspect filename pattern: YYYY-MM-DD-<aspect>.md
import re as _re  # local alias to avoid shadowing in helpers below
_DATED_ASPECT_RE = _re.compile(r"^(\d{4}-\d{2}-\d{2})-([a-z0-9][a-z0-9-]{0,59})\.md$")


def is_reserved(name: str) -> bool:
    """True if name is a reserved subdir or file at the workspace root."""
    return name in RESERVED_SUBDIRS or name in RESERVED_FILES


def topics_dir(ws: str | None = None) -> Path:
    """v2.3+: workspace root IS the topic tree root. Callers must filter RESERVED_SUBDIRS."""
    return workspace_dir(ws)


def topic_readme(folder: Path) -> Path:
    """v3.0: return the topic-folder MOC path: `<folder>/00-README.md`."""
    return folder / TOPIC_README


def topic_lessons(folder: Path) -> Path:
    """v3.0: return the topic-folder lessons ledger path: `<folder>/lessons.md`."""
    return folder / TOPIC_LESSONS


def is_topic_folder(p: Path) -> bool:
    """v3.0 (F7 fix): a folder IS a topic if it contains EITHER:
      - `<slug>/00-README.md`        (v3 layout)
      - `<slug>/<slug>.md`            (v2.4 folder-note legacy)
    Otherwise the folder is a DOMAIN (holds nested topic folders / sub-domains).
    Read-path stays permissive across partially-migrated workspaces.
    """
    if not p.is_dir():
        return False
    if (p / TOPIC_README).is_file():
        return True
    if (p / f"{p.name}.md").is_file():
        return True
    return False


def topic_landing(folder: Path) -> Path:
    """v3.0 read-path: prefer `<folder>/00-README.md`; fall back to v2.4 folder note.

    Always returns the README path even if it doesn't yet exist — callers should
    treat the result as the canonical write target after migration.
    """
    readme = folder / TOPIC_README
    if readme.is_file():
        return readme
    legacy = folder / f"{folder.name}.md"
    if legacy.is_file():
        return legacy
    return readme


def iter_topic_landings(ws: str | None = None) -> list[Path]:
    """v3.0: yield the MOC landing for each topic folder under workspace root.

    Detects v3 (00-README.md) and v2.4 (<slug>/<slug>.md) topic folders.
    Skips reserved subdirs (docs/journal/skills/research).
    """
    root = topics_dir(ws)
    if not root.is_dir():
        return []
    out: list[Path] = []
    for entry in root.iterdir():
        if entry.name.startswith(".") or is_reserved(entry.name):
            continue
        if not entry.is_dir():
            continue
        if is_topic_folder(entry):
            out.append(topic_landing(entry))
        else:
            # Domain folder (no landing) — recurse one level for lazy-nest legacy.
            for sub in entry.rglob("*"):
                if not sub.is_dir() or sub.name.startswith(".") or is_reserved(sub.name):
                    continue
                if is_topic_folder(sub):
                    out.append(topic_landing(sub))
    return out


def iter_topic_files(ws: str | None = None) -> list[Path]:
    """v3.0: yield every topic .md file (00-README.md + dated aspects + lessons.md
    inside topic folders) plus legacy fallbacks (v2.4 sub-aspects, v2.3 flat).

    Skips reserved subdirs (docs/journal/skills/research) and reserved file names
    (_MAP.md, AGENTS.md, workspace.json) at the workspace root level.
    """
    root = topics_dir(ws)
    if not root.is_dir():
        return []
    out: list[Path] = []
    for p in root.rglob("*.md"):
        try:
            rel = p.relative_to(root)
        except ValueError:
            continue
        if rel.parts and rel.parts[0] in RESERVED_SUBDIRS:
            continue
        if p.name in RESERVED_FILES:
            continue
        out.append(p)
    return out


def is_dated_aspect_filename(name: str) -> bool:
    """True if filename matches `YYYY-MM-DD-<aspect>.md`."""
    return bool(_DATED_ASPECT_RE.match(name))


def derive_aspect_slug_from_filename(name: str) -> str | None:
    """Return the `<aspect>` portion of a dated aspect filename, or None."""
    m = _DATED_ASPECT_RE.match(name)
    return m.group(2) if m else None


def slug_for_path(p: Path, ws_root: Path) -> str:
    """v3.0: derive the canonical slug for a topic file path.

    Rules (in order):
      - File inside a topic folder (parent != ws_root):
          - `00-README.md`                 → slug = parent folder name
          - `lessons.md`                   → slug = parent folder name
          - `YYYY-MM-DD-<aspect>.md`       → slug = parent folder name
          - v2.4 folder-note `<dir>/<dir>.md` → slug = parent folder name
          - any other sibling `<aspect>.md` → slug = parent folder name (v2.4 sub-aspect)
      - File at workspace root `<root>/<name>.md` → slug = name (legacy v2.3 flat).
    """
    if p.parent != ws_root:
        return p.parent.name
    return p.stem


def docs_dir(ws: str | None = None) -> Path:
    return workspace_dir(ws) / "docs"


def journal_dir(ws: str | None = None) -> Path:
    return workspace_dir(ws) / "journal"


def skills_dir(ws: str | None = None, shared: bool = False) -> Path:
    if shared:
        return shared_skills_dir()
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

def shared_agents_md() -> Path:
    """v2.7: Shared (cross-workspace) AGENTS.md — true global rules."""
    return shared_dir() / "AGENTS.md"


# Alias kept so external imports / docs that still say `agents_md` keep working.
agents_md = shared_agents_md


def settings_path() -> Path:
    return gowth_home() / "settings.json"


def config_path() -> Path:
    return gowth_home() / "config.json"


def state_path() -> Path:
    return gowth_home() / "state.json"


def index_db() -> Path:
    return gowth_home() / "index.db"


def conflict_md() -> Path:
    return gowth_home() / "SYNC-CONFLICT.md"


def locks_dir() -> Path:
    return gowth_home() / ".locks"


# ─── enumeration helpers ────────────────────────────────────────────────

def list_workspaces() -> list[str]:
    """Return workspace names: folders under workspaces/ (not `_*`) that hold
    workspace.json, docs/, journal/, or at least one topic folder.

    v4.8: workspace.json alone was the test, so a workspace that lost it
    (idol-ai, 122 files) had 0 index rows and was never TTL-archived.
    """
    root = workspaces_root()
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        if (d / "workspace.json").is_file() or (d / "docs").is_dir() or (d / "journal").is_dir():
            out.append(d.name)
            continue
        try:
            if any(is_topic_folder(c) for c in d.iterdir() if c.is_dir()):
                out.append(d.name)
        except OSError:
            continue
    return out
