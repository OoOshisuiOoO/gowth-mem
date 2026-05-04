"""MOC (Map of Content) regenerator for v2.2.

Builds three kinds of MOCs:

1. Per-workspace topic MOC      → workspaces/<ws>/_MAP.md
   Children: top-level topics in workspaces/<ws>/topics/<slug>.md
   Subfolders: workspaces/<ws>/topics/<dir>/_MAP.md targets

2. Workspaces registry          → workspaces/_MAP.md
   Children: each workspace with topic count + last_touched

3. Shared registry              → shared/_MAP.md
   Children: secrets/tools/files
   Subfolders: shared/skills

4. Topic-folder MOC             → workspaces/<ws>/topics/<dir>/_MAP.md
   For nested topic dirs (lazy-nest result).

All atomic, under file_lock("moc"). The "## Cross-links (manual)" section is
preserved verbatim across rebuilds.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import atomic_write  # type: ignore
from _frontmatter import parse_file  # type: ignore
from _home import (  # type: ignore
    RESERVED_FILES,
    RESERVED_SUBDIRS,
    is_reserved,
    list_workspaces,
    shared_dir,
    shared_moc,
    topics_dir,
    workspace_dir,
    workspace_meta,
    workspace_moc,
    workspaces_registry_moc,
    workspaces_root,
)
from _lock import file_lock  # type: ignore

MANUAL_HEADING = "## Cross-links (manual)"


# ─── helpers ─────────────────────────────────────────────────────────────

def _extract_manual_block(path: Path) -> str:
    """Read existing _MAP.md and return the verbatim text from MANUAL_HEADING
    onward, including the heading. Empty string if file missing or section absent."""
    if not path.is_file():
        return ""
    try:
        text = path.read_text(errors="ignore")
    except Exception:
        return ""
    idx = text.find(MANUAL_HEADING)
    if idx < 0:
        return ""
    return text[idx:]


def _summary_line(fm: dict, fallback: str) -> str:
    title = fm.get("title") or fallback
    status = fm.get("status") or ""
    if status:
        return f"{title} _(status: {status})_"
    return title


# ─── per-workspace MOCs ──────────────────────────────────────────────────

def rebuild_workspace_moc(ws: str) -> Path:
    """v2.3: Regenerate workspaces/<ws>/_MAP.md — workspace root IS the topic tree root.

    Children = top-level *.md files (excluding RESERVED_FILES).
    Subfolders = direct subdirs (excluding RESERVED_SUBDIRS).
    Recursive rebuild of nested folder MOCs is owned by rebuild_all.
    """
    ws_path = workspace_dir(ws)
    moc_path = ws_path / "_MAP.md"
    today = date.today().isoformat()

    children: list[str] = []
    subfolders: list[str] = []

    if ws_path.is_dir():
        for entry in sorted(ws_path.iterdir()):
            if entry.name.startswith(".") or is_reserved(entry.name):
                continue
            if entry.is_file() and entry.suffix == ".md":
                fm, _ = parse_file(entry)
                slug = fm.get("slug") or entry.stem
                children.append(f"- [[{slug}]] — {_summary_line(fm, slug.replace('-', ' ').title())}")
            elif entry.is_dir():
                subfolders.append(f"- [[{entry.name}/_MAP|{entry.name}]]")

    if not children:
        children.append("(no topics yet — sẽ tạo qua `mems` / `/mem-save`)")
    if not subfolders:
        subfolders.append("(none — lazy nest khi ≥5 topic chung domain)")

    manual = _extract_manual_block(moc_path) or (
        f"{MANUAL_HEADING}\n\n"
        f"- [[../../shared/_MAP|shared]] — cross-workspace registries\n"
        f"- [[docs/handoff|handoff]] — session state THIS workspace\n"
    )

    body = (
        f"---\n"
        f"type: MOC\n"
        f"folder: workspaces/{ws}\n"
        f"workspace: {ws}\n"
        f"last_rebuilt: {today}\n"
        f"---\n\n"
        f"# Workspace: {ws}\n\n"
        f"## Children (auto)\n\n" + "\n".join(children) + "\n\n"
        f"## Subfolders (auto)\n\n" + "\n".join(subfolders) + "\n\n"
        f"## Parent (auto)\n\n- [[../_MAP|workspaces]]\n\n"
        f"{manual}"
    )
    atomic_write(moc_path, body)
    return moc_path


def rebuild_topic_folder_moc(ws: str, folder: Path) -> Path:
    """v2.3: Regenerate _MAP.md inside a domain subfolder under workspace root.

    `folder` is relative to workspace_dir(ws) (e.g. starrocks, monitoring/grafana).
    Reserved subdirs (docs/journal/skills) are NOT MOC'd by this function — caller skips.
    """
    moc_path = folder / "_MAP.md"
    today = date.today().isoformat()
    ws_root = workspace_dir(ws)
    rel = folder.relative_to(ws_root)
    children: list[str] = []
    subfolders: list[str] = []

    for entry in sorted(folder.iterdir()):
        if entry.name.startswith(".") or is_reserved(entry.name):
            continue
        if entry.is_file() and entry.suffix == ".md":
            fm, _ = parse_file(entry)
            slug = fm.get("slug") or entry.stem
            children.append(f"- [[{slug}]] — {_summary_line(fm, slug.replace('-', ' ').title())}")
        elif entry.is_dir():
            subfolders.append(f"- [[{entry.name}/_MAP|{entry.name}]]")
            rebuild_topic_folder_moc(ws, entry)

    if not children:
        children.append("(empty)")
    if not subfolders:
        subfolders.append("(none)")

    manual = _extract_manual_block(moc_path) or (
        f"{MANUAL_HEADING}\n\n(curate sibling MOCs here)\n"
    )

    parent_link = f"../_MAP|{rel.parent.name if rel.parent.name else ws}"

    body = (
        f"---\n"
        f"type: MOC\n"
        f"folder: workspaces/{ws}/{rel.as_posix()}\n"
        f"workspace: {ws}\n"
        f"last_rebuilt: {today}\n"
        f"---\n\n"
        f"# {rel.as_posix()}\n\n"
        f"## Children (auto)\n\n" + "\n".join(children) + "\n\n"
        f"## Subfolders (auto)\n\n" + "\n".join(subfolders) + "\n\n"
        f"## Parent (auto)\n\n- [[{parent_link}]]\n\n"
        f"{manual}"
    )
    atomic_write(moc_path, body)
    return moc_path


def rebuild_topic_subfolders(ws: str) -> list[Path]:
    """v2.3: rebuild _MAP.md for every non-reserved direct subfolder under workspace root.
    Returns list of MOC paths written. Recurses into nested subfolders."""
    out: list[Path] = []
    ws_root = workspace_dir(ws)
    if not ws_root.is_dir():
        return out
    for entry in sorted(ws_root.iterdir()):
        if not entry.is_dir() or entry.name.startswith(".") or is_reserved(entry.name):
            continue
        out.append(rebuild_topic_folder_moc(ws, entry))
    return out


# ─── workspace registry ──────────────────────────────────────────────────

def rebuild_workspaces_registry() -> Path:
    today = date.today().isoformat()
    moc_path = workspaces_registry_moc()
    rows: list[str] = []
    for name in list_workspaces():
        meta_path = workspace_meta(name)
        meta: dict = {}
        try:
            meta = json.loads(meta_path.read_text())
        except Exception:
            pass
        topics_root = workspace_dir(name) / "topics"
        topic_count = 0
        last_mtime = 0.0
        if topics_root.is_dir():
            for p in topics_root.rglob("*.md"):
                if p.name == "_MAP.md":
                    continue
                topic_count += 1
                try:
                    last_mtime = max(last_mtime, p.stat().st_mtime)
                except Exception:
                    pass
        last = date.fromtimestamp(last_mtime).isoformat() if last_mtime else "-"
        rows.append(
            f"| [[{name}/_MAP\\|{name}]] | {meta.get('title', name)} | {meta.get('created', '-')} | {topic_count} | {last} |"
        )

    if not rows:
        rows.append("| _(no workspaces)_ | | | | |")

    archive_dir = workspaces_root() / "_archive"
    archived: list[str] = []
    if archive_dir.is_dir():
        for d in sorted(archive_dir.iterdir()):
            if d.is_dir():
                archived.append(f"- [[_archive/{d.name}/_MAP|{d.name}]]")
    if not archived:
        archived.append("(none)")

    manual = _extract_manual_block(moc_path) or (
        f"{MANUAL_HEADING}\n\n"
        f"- [[../shared/_MAP|shared]] — cross-workspace registries\n"
        f"- [[../AGENTS|global rules]]\n"
    )

    body = (
        f"---\n"
        f"type: MOC\n"
        f"folder: workspaces\n"
        f"last_rebuilt: {today}\n"
        f"---\n\n"
        f"# Workspaces Registry\n\n"
        f"## Active workspaces (auto)\n\n"
        f"| Slug | Title | Created | Topics | Last touched |\n"
        f"|---|---|---|---|---|\n" + "\n".join(rows) + "\n\n"
        f"## Archived (auto)\n\n" + "\n".join(archived) + "\n\n"
        f"{manual}"
    )
    atomic_write(moc_path, body)
    return moc_path


# ─── shared MOC ──────────────────────────────────────────────────────────

def rebuild_shared_moc() -> Path:
    today = date.today().isoformat()
    moc_path = shared_moc()
    sd = shared_dir()
    children: list[str] = []
    for name in ("secrets", "tools", "files"):
        f = sd / f"{name}.md"
        if f.is_file():
            children.append(f"- [[{name}]] — {name} registry")
    if not children:
        children.append("(empty)")
    subfolders: list[str] = []
    skills = sd / "skills"
    if skills.is_dir():
        subfolders.append("- [[skills/_index|skills]] — shared Voyager workflows")
    if not subfolders:
        subfolders.append("(none)")

    manual = _extract_manual_block(moc_path) or (
        f"{MANUAL_HEADING}\n\n"
        f"- [[../workspaces/_MAP|workspaces]] — workspace registry\n"
        f"- [[../AGENTS|global rules]]\n"
    )

    body = (
        f"---\n"
        f"type: MOC\n"
        f"folder: shared\n"
        f"last_rebuilt: {today}\n"
        f"---\n\n"
        f"# Shared Map\n\n"
        f"## Children (auto)\n\n" + "\n".join(children) + "\n\n"
        f"## Subfolders (auto)\n\n" + "\n".join(subfolders) + "\n\n"
        f"{manual}"
    )
    atomic_write(moc_path, body)
    return moc_path


# ─── orchestrator ────────────────────────────────────────────────────────

def rebuild_all(ws: str | None = None) -> dict:
    """v2.3: rebuild shared + workspace registry + per-ws workspace MOC + nested folder MOCs.
    Workspace MOC IS the topic-root MOC (no separate topics/_MAP)."""
    written: dict[str, str] = {}
    with file_lock("moc"):
        written["shared"] = str(rebuild_shared_moc())
        written["registry"] = str(rebuild_workspaces_registry())
        targets = [ws] if ws is not None else list_workspaces()
        for w in targets:
            written[f"ws:{w}"] = str(rebuild_workspace_moc(w))
            for sub in rebuild_topic_subfolders(w):
                written[f"sub:{w}:{sub.parent.name}"] = str(sub)
    return written


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws", help="Rebuild only this workspace + registry + shared")
    ap.add_argument("--all", action="store_true", help="Rebuild every workspace MOC")
    args = ap.parse_args()
    if args.all:
        out = rebuild_all()
    else:
        out = rebuild_all(ws=args.ws)
    for k, v in out.items():
        print(f"{k}\t{v}")
