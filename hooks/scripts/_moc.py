"""MOC (Map of Content) regenerator for v3.0 (topic-folder + dated-aspect).

Three kinds of MOCs:

1. Workspace MOC → `workspaces/<ws>/_MAP.md`
   Children: every topic folder under workspace root, listed as
   `[[<slug>]] — <TL;DR first line>` (TL;DR pulled from `<slug>/00-README.md`).
   Reserved subdirs (docs/journal/skills/research) and reserved files always skipped.

2. Topic README → `workspaces/<ws>/<slug>/00-README.md`
   Auto-regenerates `## Aspects (auto)` listing dated-aspect siblings (newest first)
   plus `lessons.md` if present. Preserves `## TL;DR` and `## Cross-links (manual)`
   verbatim across rebuilds (idempotent — no double-write if nothing changed).

3. Workspaces registry → `workspaces/_MAP.md`
4. Shared registry    → `shared/_MAP.md`

All writes atomic, under `file_lock("moc")`. `## Cross-links (manual)` and
`## TL;DR` are preserved verbatim across rebuilds.

F18 lock (2026-05-17): dropped legacy `rebuild_topic_folder_moc` /
`rebuild_topic_subfolders` (which emitted `_MAP.md` inside topic folders).
v3 puts the topic MOC inside `00-README.md` instead.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import atomic_write, safe_write  # type: ignore  # noqa: F401
from _frontmatter import parse_file  # type: ignore
from _home import (  # type: ignore
    RESERVED_FILES,
    RESERVED_SUBDIRS,
    TOPIC_LESSONS,
    TOPIC_README,
    derive_aspect_slug_from_filename,
    is_dated_aspect_filename,
    is_reserved,
    is_topic_folder,
    iter_topic_landings,
    list_workspaces,
    shared_dir,
    shared_moc,
    topic_landing,
    topic_readme,
    workspace_dir,
    workspace_meta,
    workspace_moc,
    workspaces_registry_moc,
    workspaces_root,
)
from _lock import file_lock  # type: ignore

MANUAL_HEADING = "## Cross-links (manual)"
TLDR_HEADING = "## TL;DR"
ASPECTS_HEADING = "## Aspects (auto)"


# ─── helpers ─────────────────────────────────────────────────────────────

def _extract_section(path: Path, heading: str, stop_headings: tuple[str, ...]) -> str:
    """Read file and return verbatim text starting at `heading` (inclusive) up
    to the next heading in `stop_headings` (exclusive). Empty if missing."""
    if not path.is_file():
        return ""
    try:
        text = path.read_text(errors="ignore")
    except Exception:
        return ""
    idx = text.find(heading)
    if idx < 0:
        return ""
    rest = text[idx:]
    end = len(rest)
    for stop in stop_headings:
        pos = rest.find("\n" + stop, len(heading))
        if pos >= 0 and pos < end:
            end = pos + 1  # keep the trailing newline before stop heading
    return rest[:end].rstrip() + "\n"


def _extract_manual_block(path: Path) -> str:
    """Read existing MOC and return verbatim Cross-links (manual) onward.
    Empty if absent."""
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


def _extract_tldr_block(path: Path) -> str:
    """Return verbatim `## TL;DR` section (up to next `## `). Empty if missing."""
    return _extract_section(path, TLDR_HEADING, (ASPECTS_HEADING, MANUAL_HEADING, "## "))


def _summary_line(fm: dict, fallback: str) -> str:
    title = fm.get("title") or fallback
    status = fm.get("status") or ""
    if status:
        return f"{title} _(status: {status})_"
    return title


def _first_tldr_line(path: Path) -> str:
    """Pull the first non-empty TL;DR line (after `## TL;DR`) for workspace MOC.
    Strips leading `>` blockquote markers. Empty string if missing."""
    block = _extract_tldr_block(path)
    if not block:
        return ""
    for line in block.splitlines()[1:]:  # skip heading
        s = line.strip()
        if not s:
            continue
        s = s.lstrip(">").strip()
        if not s:
            continue
        return s
    return ""


def _aspect_preview(path: Path) -> str:
    """First non-empty content line of an aspect file (after frontmatter +
    title). Used for `## Aspects (auto)` preview. Empty if nothing useful."""
    try:
        text = path.read_text(errors="ignore")
    except Exception:
        return ""
    # Strip frontmatter
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end > 0:
            text = text[end + 5:]
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            continue
        if s.startswith("## "):
            continue
        # skip the "> 1-2 lines describing..." placeholder bleed
        s2 = s.lstrip(">").strip()
        if not s2 or s2.startswith("("):
            continue
        return s2[:120]
    return ""


# ─── workspace MOC ───────────────────────────────────────────────────────

def rebuild_workspace_moc(ws: str) -> Path:
    """v3.0: Regenerate `workspaces/<ws>/_MAP.md` — workspace = topic tree root.

    Children: every topic folder under workspace root, sorted by slug, rendered
              as `- [[<slug>]] — <TL;DR first line>` (or title fallback).
    Subfolders: domain folders (non-topic-folder dirs) for lazy-nest legacy.
    Reserved subdirs (docs/journal/skills/research) and reserved files skipped.
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
                # Legacy flat topic file (v2.3) — still scannable.
                fm, _ = parse_file(entry)
                slug = fm.get("slug") or entry.stem
                children.append(
                    f"- [[{slug}]] — {_summary_line(fm, slug.replace('-', ' ').title())} _(legacy flat)_"
                )
            elif entry.is_dir():
                if is_topic_folder(entry):
                    landing = topic_landing(entry)
                    fm, _ = parse_file(landing)
                    slug = fm.get("slug") or entry.name
                    tldr = _first_tldr_line(landing)
                    label = tldr or _summary_line(fm, slug.replace("-", " ").title())
                    children.append(f"- [[{slug}]] — {label}")
                else:
                    subfolders.append(f"- {entry.name}/ — domain folder")

    if not children:
        children.append("(no topics yet — sẽ tạo qua `mems` / `/mem-save`)")
    if not subfolders:
        subfolders.append("(none)")

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
        f"layout_version: 3\n"
        f"last_rebuilt: {today}\n"
        f"---\n\n"
        f"# Workspace: {ws}\n\n"
        f"## Children (auto)\n\n" + "\n".join(children) + "\n\n"
        f"## Subfolders (auto)\n\n" + "\n".join(subfolders) + "\n\n"
        f"## Parent (auto)\n\n- [[../_MAP|workspaces]]\n\n"
        f"{manual}"
    )
    safe_write(moc_path, body)
    return moc_path


# ─── topic README (the per-topic MOC) ────────────────────────────────────

def rebuild_topic_readme(folder: Path) -> Path | None:
    """v3.0: Regenerate `<folder>/00-README.md` `## Aspects (auto)` section.

    Preserves `## TL;DR` and `## Cross-links (manual)` verbatim. Idempotent:
    rebuilds frontmatter `last_touched` only if Aspects content changed.

    Returns the README path written, or None if `folder` is not a topic folder
    (defensive — caller should pre-filter via `is_topic_folder`).
    """
    if not is_topic_folder(folder):
        return None
    readme = topic_readme(folder)
    today = date.today().isoformat()

    # Read existing frontmatter (or synthesize from folder name).
    if readme.is_file():
        fm, _ = parse_file(readme)
    else:
        fm = {}
    slug = fm.get("slug") or folder.name
    title = fm.get("title") or slug.replace("-", " ").title()
    topic_type = fm.get("type") or "misc"
    status = fm.get("status") or "draft"
    maturity = fm.get("maturity") or "draft"
    created = fm.get("created") or today
    parents = fm.get("parents") or []
    links = fm.get("links") or []
    aliases = fm.get("aliases") or []
    tags = fm.get("tags") or []

    # Build Aspects (auto) — dated siblings, newest first, then lessons.md.
    aspect_rows: list[str] = []
    dated: list[tuple[str, str, Path]] = []  # (date, aspect_slug, path)
    for entry in folder.iterdir():
        if not entry.is_file() or entry.suffix != ".md":
            continue
        if entry.name in (TOPIC_README, TOPIC_LESSONS, "_MAP.md"):
            continue
        if is_dated_aspect_filename(entry.name):
            stem = entry.stem  # YYYY-MM-DD-<aspect>
            d = stem[:10]
            aspect = derive_aspect_slug_from_filename(entry.name) or "note"
            dated.append((d, aspect, entry))
        elif entry.name == f"{folder.name}.md":
            # v2.4 folder-note — emit a one-liner so users see legacy content.
            preview = _aspect_preview(entry)
            line = f"- `{entry.name}` (legacy v2.4 folder note)"
            if preview:
                line += f" — {preview}"
            aspect_rows.append(line)
        else:
            # v2.4 sub-aspect (`<aspect>.md` undated) — list as-is.
            preview = _aspect_preview(entry)
            line = f"- `{entry.name}` (legacy v2.4 aspect)"
            if preview:
                line += f" — {preview}"
            aspect_rows.append(line)

    # Newest-first by date, then aspect slug for stable order
    for d, aspect, p in sorted(dated, key=lambda x: (x[0], x[1]), reverse=True):
        preview = _aspect_preview(p)
        line = f"- [[{p.stem}|{d} — {aspect}]]"
        if preview:
            line += f" — {preview}"
        aspect_rows.append(line)

    lessons_path = folder / TOPIC_LESSONS
    if lessons_path.is_file():
        aspect_rows.append(f"- [[lessons]] — folder ledger")

    if not aspect_rows:
        aspect_rows.append("(empty — dated `YYYY-MM-DD-<aspect>.md` siblings appear here after first write)")

    # Preserve TL;DR + manual cross-links verbatim
    tldr = _extract_tldr_block(readme)
    if not tldr:
        tldr = f"{TLDR_HEADING}\n\n> Cốt lõi 1 dòng (TODO).\n"

    manual = _extract_manual_block(readme) or (
        f"{MANUAL_HEADING}\n\n"
        f"(curate `[[wikilinks]]` to related topics here — preserved across MOC rebuilds)\n"
    )

    fm_lines = [
        "---",
        f"slug: {slug}",
        f"title: {title}",
        f"type: {topic_type}",
        f"status: {status}",
        f"maturity: {maturity}",
        f"created: {created}",
        f"last_touched: {today}",
        f"parents: [{', '.join(parents)}]",
        f"links: [{', '.join(links)}]",
        f"aliases: [{', '.join(aliases)}]",
        f"tags: [{', '.join(tags)}]",
        "---",
        "",
    ]
    body = (
        "\n".join(fm_lines)
        + f"\n# {title}\n\n"
        + tldr.rstrip() + "\n\n"
        + f"{ASPECTS_HEADING}\n\n" + "\n".join(aspect_rows) + "\n\n"
        + manual
    )

    # Idempotency: only write if content actually changed (mtime hygiene + git noise).
    if readme.is_file():
        try:
            existing = readme.read_text(errors="ignore")
            # Compare ignoring `last_touched:` line (otherwise we'd churn daily).
            def _strip_lt(t: str) -> str:
                return re.sub(r"^last_touched: .+$", "last_touched: X", t, flags=re.M)
            if _strip_lt(existing) == _strip_lt(body):
                return readme
        except Exception:
            pass

    safe_write(readme, body)
    return readme


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
        # v3.0: count topic FOLDERS (one per topic), use README mtime for last_touched.
        landings = iter_topic_landings(name)
        topic_count = len(landings)
        last_mtime = 0.0
        for p in landings:
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
        f"layout_version: 3\n"
        f"last_rebuilt: {today}\n"
        f"---\n\n"
        f"# Workspaces Registry\n\n"
        f"## Active workspaces (auto)\n\n"
        f"| Slug | Title | Created | Topics | Last touched |\n"
        f"|---|---|---|---|---|\n" + "\n".join(rows) + "\n\n"
        f"## Archived (auto)\n\n" + "\n".join(archived) + "\n\n"
        f"{manual}"
    )
    safe_write(moc_path, body)
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
        f"layout_version: 3\n"
        f"last_rebuilt: {today}\n"
        f"---\n\n"
        f"# Shared Map\n\n"
        f"## Children (auto)\n\n" + "\n".join(children) + "\n\n"
        f"## Subfolders (auto)\n\n" + "\n".join(subfolders) + "\n\n"
        f"{manual}"
    )
    safe_write(moc_path, body)
    return moc_path


# ─── orchestrator ────────────────────────────────────────────────────────

def rebuild_all(ws: str | None = None) -> dict:
    """v3.0: rebuild shared + workspace registry + per-ws workspace MOC + every topic README."""
    written: dict[str, str] = {}
    with file_lock("moc"):
        written["shared"] = str(rebuild_shared_moc())
        written["registry"] = str(rebuild_workspaces_registry())
        targets = [ws] if ws is not None else list_workspaces()
        for w in targets:
            written[f"ws:{w}"] = str(rebuild_workspace_moc(w))
            # Rebuild every topic README in this workspace
            for landing in iter_topic_landings(w):
                folder = landing.parent
                out = rebuild_topic_readme(folder)
                if out is not None:
                    written[f"topic:{w}:{folder.name}"] = str(out)
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
