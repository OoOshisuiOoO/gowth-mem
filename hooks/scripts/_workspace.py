"""Workspace management for v2.2: resolve active, list, scaffold, archive, switch.

A workspace is a self-contained namespace under `workspaces/<name>/` that holds
its own topics, docs, journal, and (optional) skills + AGENTS.md override.
The active workspace at session-time scopes recall and writes.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import atomic_write  # type: ignore
from _home import (  # type: ignore
    active_workspace,
    clear_session_workspace,
    gowth_home,
    iter_topic_files,
    list_workspaces,
    workspace_dir,
    workspace_meta,
    workspaces_root,
    write_session_workspace,
)


def resolve_active() -> str:
    """Wrapper exposing _home.active_workspace at module level (used by hooks)."""
    return active_workspace()


def list_all() -> list[dict]:
    """Return list of {name, title, description, created, tags, last_touched, topic_count}.

    v2.4+: walks workspace root via iter_topic_files (skips reserved subdirs/files).
    """
    out: list[dict] = []
    for name in list_workspaces():
        meta_path = workspace_meta(name)
        meta = {}
        try:
            meta = json.loads(meta_path.read_text())
        except Exception:
            pass
        topic_count = 0
        last_mtime = 0.0
        for p in iter_topic_files(name):
            topic_count += 1
            try:
                last_mtime = max(last_mtime, p.stat().st_mtime)
            except Exception:
                pass
        last_touched = ""
        if last_mtime:
            last_touched = date.fromtimestamp(last_mtime).isoformat()
        out.append(
            {
                "name": name,
                "title": meta.get("title", name),
                "description": meta.get("description", ""),
                "created": meta.get("created", ""),
                "tags": meta.get("tags", []),
                "topic_count": topic_count,
                "last_touched": last_touched,
            }
        )
    return out


# ─── scaffold ───────────────────────────────────────────────────────────

_WS_AGENTS = """# AGENTS.md (workspace: {name})

{title} — workspace-specific delta. Cross-workspace rules ở `shared/AGENTS.md` áp dụng đầy đủ.

## Focus

- (mô tả 1-2 dòng scope của workspace `{name}`)

## Workspace-specific guardrails

- (KHÔNG ... — luật riêng của ws này, không có trong shared)

## Conventions

- (folder layout / naming convention nếu khác mặc định)

## Common topics

- (gợi ý topic folders sẽ xuất hiện)
"""


_TOPIC_MISC = """---
slug: misc
title: Misc
status: draft
created: {today}
last_touched: {today}
parents: []
links: []
aliases: [misc, fallback]
---

# Misc

> Cốt lõi: Default fallback cho entries chưa match topic nào trong workspace `{name}`.

## [exp]
(empty)

## [ref]
(empty)

## [decision]
(empty)

## [reflection]
(empty)
"""

_WS_MAP = """---
type: MOC
folder: workspaces/{name}
workspace: {name}
last_rebuilt: {today}
---

# Workspace: {name}

> {title}

## Children (auto)

- [[misc]] — fallback topic for unrouted entries

## Subfolders (auto)

(none yet — lazy nest khi ≥5 topic chung domain, e.g. starrocks/, monitoring/grafana/)

## Parent (auto)

- [[../_MAP|workspaces]]

## Cross-links (manual)

- [[../../shared/_MAP|shared]] — cross-workspace registries
- [[docs/handoff|handoff]] — session state THIS workspace
- [[docs/files|files]] — workspace tree map
"""

_HANDOFF = """# Handoff — workspace {name}

Per-session state for this workspace. Each line: `host:<machine> [doing|next|blocker|thread] <text>`.

> Stale >7 ngày → DELETE.

## Entries

(empty)
"""

_DOCS_EXP = """# Workspace exp.md ({name})

Cross-topic episodic overflow. Format: `- [exp] 1-2 dòng (Source: <reproducible>)`.

> Ưu tiên route entries vào `<slug>.md` ở workspace root. File này chỉ giữ overflow.

## Entries

(empty)
"""

_DOCS_REF = """# Workspace ref.md ({name})

Cross-topic verified facts. Format: `- [ref] 1-2 dòng (Source: <url|file|doc>)` — **`Source:` BẮT BUỘC**.

## Entries

(empty)
"""

_DOCS_TOOLS = """# Workspace tools.md ({name})

Tool quirks **specific to workspace `{name}`**. System tools → `shared/tools.md`.

## Entries

(empty)
"""

_DOCS_FILES = """---
type: files-map
workspace: {name}
last_rebuilt: {today}
---

# Workspace files map: {name}

## Tree (v2.3)

```
workspaces/{name}/
├── workspace.json        metadata (RESERVED)
├── AGENTS.md             optional override (RESERVED)
├── _MAP.md               workspace = topic root MOC (RESERVED)
├── docs/{{handoff,exp,ref,tools,files}}.md   RESERVED
├── journal/<date>.md     RESERVED
├── skills/<slug>.md      RESERVED (optional override of shared/skills/)
├── <slug>.md             top-level topic file (file-per-topic default)
└── <domain>/             lazy-nest, ≤3 cấp khi ≥5 topic chung domain
    ├── _MAP.md           folder MOC (auto-rebuild)
    ├── <slug>.md
    └── <sub>/<slug>.md   ví dụ: monitoring/grafana/alerting.md
```

Reserved names cấm làm slug/domain: docs, journal, skills, _MAP.md, AGENTS.md, workspace.json.
"""


_WS_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")


def _validate_name(name: str) -> None:
    """Reject path-traversal, hidden, reserved, and non-conforming workspace names."""
    if (
        not name
        or name in {".", "..", "_archive"}
        or "/" in name
        or "\\" in name
        or name.startswith((".", "_"))
        or not _WS_NAME_RE.match(name)
    ):
        raise ValueError(
            f"invalid workspace name: {name!r} "
            f"(must match {_WS_NAME_RE.pattern}, not '.', '..', '_archive', or start with '.' / '_')"
        )


def scaffold(name: str, title: str = "", description: str = "", tags: list[str] | None = None) -> Path:
    """Create workspaces/<name>/ with full skeleton. Idempotent: skips files that exist."""
    _validate_name(name)
    today = date.today().isoformat()
    ws_path = workspace_dir(name)
    if ws_path.exists() and (ws_path / "workspace.json").is_file():
        return ws_path

    (ws_path / "docs").mkdir(parents=True, exist_ok=True)
    (ws_path / "journal").mkdir(parents=True, exist_ok=True)
    (ws_path / "skills").mkdir(parents=True, exist_ok=True)

    # workspace.json
    meta = {
        "name": name,
        "title": title or name.title(),
        "description": description,
        "created": today,
        "tags": tags or [],
        "remote": None,
    }
    meta_path = ws_path / "workspace.json"
    if not meta_path.exists():
        atomic_write(meta_path, json.dumps(meta, indent=2) + "\n")

    fmt = {"name": name, "title": meta["title"], "today": today}

    # v2.4: misc topic = folder `misc/` containing `misc.md` (Obsidian folder note)
    (ws_path / "misc").mkdir(parents=True, exist_ok=True)
    pairs = [
        (ws_path / "_MAP.md", _WS_MAP),
        (ws_path / "AGENTS.md", _WS_AGENTS),
        (ws_path / "misc" / "misc.md", _TOPIC_MISC),
        (ws_path / "docs" / "handoff.md", _HANDOFF),
        (ws_path / "docs" / "exp.md", _DOCS_EXP),
        (ws_path / "docs" / "ref.md", _DOCS_REF),
        (ws_path / "docs" / "tools.md", _DOCS_TOOLS),
        (ws_path / "docs" / "files.md", _DOCS_FILES),
    ]
    for p, tpl in pairs:
        if not p.exists():
            atomic_write(p, tpl.format(**fmt))

    return ws_path


def archive(name: str) -> Path:
    """Move workspaces/<name>/ → workspaces/_archive/<name>-<today>/."""
    src = workspace_dir(name)
    if not src.is_dir():
        raise FileNotFoundError(src)
    today = date.today().isoformat()
    archive_root = workspaces_root() / "_archive"
    archive_root.mkdir(parents=True, exist_ok=True)
    dst = archive_root / f"{name}-{today}"
    if dst.exists():
        raise FileExistsError(dst)
    shutil.move(str(src), str(dst))
    return dst


def set_active_session(name: str) -> None:
    """Persist active workspace for the current session via .session-workspace."""
    if name not in list_workspaces():
        raise ValueError(f"workspace not found: {name}. Use /mem-workspace-create first.")
    write_session_workspace(name)


def clear_session() -> None:
    """Drop session override; next bootstrap falls back to env > cwd-glob > config default."""
    clear_session_workspace()


def add_workspace_map(pattern: str, name: str) -> dict:
    """Add a workspace_map entry to config.json. Returns updated map."""
    cfg_path = gowth_home() / "config.json"
    cfg = {}
    if cfg_path.is_file():
        try:
            cfg = json.loads(cfg_path.read_text())
        except Exception:
            cfg = {}
    ws_map = cfg.get("workspace_map", {}) or {}
    ws_map[pattern] = name
    cfg["workspace_map"] = ws_map
    atomic_write(cfg_path, json.dumps(cfg, indent=2) + "\n")
    return ws_map


def remove_workspace_map(pattern: str) -> dict:
    cfg_path = gowth_home() / "config.json"
    cfg = json.loads(cfg_path.read_text()) if cfg_path.is_file() else {}
    ws_map = cfg.get("workspace_map", {}) or {}
    ws_map.pop(pattern, None)
    cfg["workspace_map"] = ws_map
    atomic_write(cfg_path, json.dumps(cfg, indent=2) + "\n")
    return ws_map


# ─── CLI for hooks/skills ────────────────────────────────────────────────

def _cli() -> int:
    import argparse

    p = argparse.ArgumentParser(prog="_workspace.py")
    sp = p.add_subparsers(dest="cmd", required=True)
    sp.add_parser("active")
    sp.add_parser("list")

    create = sp.add_parser("create")
    create.add_argument("name")
    create.add_argument("--title", default="")
    create.add_argument("--description", default="")

    arc = sp.add_parser("archive")
    arc.add_argument("name")

    sw = sp.add_parser("switch")
    sw.add_argument("name")
    sp.add_parser("clear")

    mp = sp.add_parser("map")
    mp.add_argument("pattern")
    mp.add_argument("name")

    rm = sp.add_parser("unmap")
    rm.add_argument("pattern")

    args = p.parse_args()

    if args.cmd == "active":
        print(resolve_active())
        return 0
    if args.cmd == "list":
        for w in list_all():
            print(f"{w['name']:20s} {w['topic_count']:>4d} topics  last={w['last_touched'] or '-':10s}  {w['title']}")
        return 0
    if args.cmd == "create":
        path = scaffold(args.name, title=args.title, description=args.description)
        print(f"created: {path}")
        return 0
    if args.cmd == "archive":
        dst = archive(args.name)
        print(f"archived: {dst}")
        return 0
    if args.cmd == "switch":
        set_active_session(args.name)
        print(f"active: {args.name}")
        return 0
    if args.cmd == "clear":
        clear_session()
        print("cleared session override")
        return 0
    if args.cmd == "map":
        m = add_workspace_map(args.pattern, args.name)
        print(json.dumps(m, indent=2))
        return 0
    if args.cmd == "unmap":
        m = remove_workspace_map(args.pattern)
        print(json.dumps(m, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(_cli())
