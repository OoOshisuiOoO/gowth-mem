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

v4.9: a topic CARD (`_card.py`) lives inside the TL;DR and is kept whole —
even when a hand edit put a `## ` line inside it, which used to end the TL;DR
there and drop the rest. Frontmatter keys the rebuild does not render are
kept verbatim (after the known keys, original order), and a scalar
`aliases:/parents:/links:/tags:` value is a 1-item list, no longer one item
per character. A README with no card, no unknown key and no scalar list
value renders byte-identically to v4.8 (tests/test_moc_golden.py). A card
the rebuild would NOT keep (it sits below a hand-written `## ` heading, under
a renamed TL;DR or under `## Aspects (auto)`) is archived verbatim to
`<topic>/.history/card-<sha12>.txt` first; if that fails the README is left
as it is (review cards#4).

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
from _card import _archive as _archive_card  # type: ignore
from _card import card_region, card_spans, is_begin_marker, is_end_marker  # type: ignore
from _frontmatter import _FRONT_RE, parse, parse_file  # type: ignore
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

def _extract_manual_block(path: Path) -> str:
    """Read existing MOC and return verbatim Cross-links (manual) onward.
    Empty if absent."""
    if not path.is_file():
        return ""
    try:
        text = path.read_text(errors="ignore")
    except Exception:
        return ""
    return _manual_block_from_text(text)


def _manual_block_from_text(text: str) -> str:
    idx = text.find(MANUAL_HEADING)
    if idx < 0:
        return ""
    return text[idx:]


def _extract_tldr_block(path: Path) -> str:
    """Return verbatim `## TL;DR` section (up to next `## `). Empty if missing."""
    if not path.is_file():
        return ""
    try:
        text = path.read_text(errors="ignore")
    except Exception:
        return ""
    return _tldr_block_from_text(text)


def _tldr_bounds(text: str):
    """(start, end) in `text` of the TL;DR block `_tldr_block_from_text` keeps
    (before its rstrip), or None when there is no `## TL;DR`."""
    idx = text.find(TLDR_HEADING)
    if idx < 0:
        return None
    rest = text[idx:]
    end = len(rest)
    for stop in (ASPECTS_HEADING, MANUAL_HEADING, "## "):
        pos = rest.find("\n" + stop, len(TLDR_HEADING))
        if pos >= 0 and pos < end:
            end = pos + 1  # keep the trailing newline before stop heading
    region = card_region(text)
    if region is not None and region[0] >= idx and region[1] - idx > end:
        pos = rest.find("\n## ", region[1] - idx)
        end = pos + 1 if pos >= 0 else len(rest)
    return idx, idx + end


def _tldr_block_from_text(text: str) -> str:
    """`_extract_section(TL;DR)` on text (v4.8 semantics), extended over a
    card that starts in the TL;DR and runs past where the section would end
    (a hand-typed `## ` line inside the card) — the card is never cut."""
    bounds = _tldr_bounds(text)
    if bounds is None:
        return ""
    return text[bounds[0]:bounds[1]].rstrip() + "\n"


def _dropped_cards(text: str) -> str:
    """The card text of an existing README that `rebuild_topic_readme` would
    NOT copy, "" when none: every card span (`_card.card_spans`, corrupt ones
    included) outside the TL;DR block and the manual block — the two pieces
    of the old README a rebuild copies verbatim — joined in README order.
    Review cards#4: a card below a hand-written `## ` heading (between the
    summary and the card), under a renamed TL;DR or under `## Aspects (auto)`
    sat in a section the rebuild regenerates, and vanished without a copy.
    Linear: one pass for the spans, two `find`s for the kept blocks."""
    spans = card_spans(text)
    if not spans:
        return ""
    kept = []
    bounds = _tldr_bounds(text)
    if bounds is not None:
        kept.append(bounds)
    mi = text.find(MANUAL_HEADING)
    if mi >= 0:
        kept.append((mi, len(text)))
    return "\n".join(text[a:b].rstrip("\r\n") for a, b in spans
                     if not any(s <= a and b <= e for s, e in kept))


def _summary_line(fm: dict, fallback: str) -> str:
    title = fm.get("title") or fallback
    status = fm.get("status") or ""
    if status:
        return f"{title} _(status: {status})_"
    return title


def _first_tldr_line(path: Path) -> str:
    """Pull the first non-empty TL;DR line (after `## TL;DR`) for workspace MOC.
    Strips leading `>` blockquote markers. Empty string if missing. v4.9: card
    lines (markers included) are never the label."""
    block = _extract_tldr_block(path)
    if not block:
        return ""
    in_card = False
    for line in block.splitlines()[1:]:  # skip heading
        if in_card:
            in_card = not is_end_marker(line)
            continue
        if is_begin_marker(line):
            in_card = True
            continue
        s = line.strip()
        if not s:
            continue
        s = s.lstrip(">").strip()
        if not s:
            continue
        return s
    return ""


# Frontmatter keys `rebuild_topic_readme` renders itself; any other key is
# kept verbatim (v4.9).
_KNOWN_FM_KEYS = ("slug", "title", "type", "status", "maturity", "created",
                  "last_touched", "parents", "links", "aliases", "tags")
# `_frontmatter.parse`'s own key-line test, so "known"/"unknown" agree with it.
_FM_KEY_RE = re.compile(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$")


def _frontmatter_items(text: str) -> list:
    """[(key, [raw lines])] of the frontmatter block in file order: a key line
    as `_frontmatter.parse` reads one, plus every following line that is not
    a key line (block-list items, comments, continuation). Lines before the
    first key are dropped, as v4.8 dropped them."""
    m = _FRONT_RE.match(text or "")
    if not m:
        return []
    items: list = []
    for line in m.group("body").splitlines():
        s = line.rstrip()
        kv = _FM_KEY_RE.match(s) if s and not s.startswith("#") else None
        if kv:
            items.append((kv.group(1), [line]))
        elif items:
            items[-1][1].append(line)
    return items


def _unknown_frontmatter_lines(items: list) -> list:
    """Raw lines of every key the rebuild does not render, original order;
    trailing blank lines of an item dropped."""
    out: list = []
    for key, lines in items:
        if key in _KNOWN_FM_KEYS:
            continue
        keep = list(lines)
        while len(keep) > 1 and not keep[-1].strip():
            keep.pop()
        out.extend(keep)
    return out


def _as_list(value, items: list, key: str) -> list:
    """A list frontmatter value: a list stays; a scalar is ONE item (v4.8
    joined a string's characters: `aliases: foo` → `[f, o, o]`); an empty
    value with `- item` lines below it (block form) yields those items."""
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    for k, lines in reversed(items):   # parse() keeps the LAST occurrence
        if k != key:
            continue
        out = []
        for ln in lines[1:]:
            s = ln.strip()
            if s.startswith("- "):
                v = s[2:].strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                if v:
                    out.append(v)
        return out
    return []


def _fmt_item(x) -> str:
    """One flow-list item; quoted only when it holds a comma (which would
    split it on the next read)."""
    x = str(x)
    if "," in x:
        if '"' not in x:
            return f'"{x}"'
        if "'" not in x:
            return f"'{x}'"
    return x


def _fmt_list(values: list) -> str:
    return "[" + ", ".join(_fmt_item(v) for v in values) + "]"


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
    (defensive — caller should pre-filter via `is_topic_folder`) or resolves
    outside the workspaces tree, or (v4.9) when a topic card the rebuild would
    drop could not be archived first — nothing is written then.

    v4.7.6: a topic folder symlinked to notes kept elsewhere (another repo) was
    rebuilt THROUGH the link — its hand-written README overwritten by the
    skeleton rebuild after any lesson save. Such folders are read, never written.
    """
    if not is_topic_folder(folder):
        return None
    try:
        folder.resolve().relative_to(workspaces_root().resolve())
    except (ValueError, OSError):
        return None
    readme = topic_readme(folder)
    today = date.today().isoformat()

    # Read the existing README once (or synthesize from folder name).
    existing: str | None = None
    if readme.is_file():
        try:
            existing = readme.read_text(errors="ignore")
        except Exception:
            existing = None
    fm = parse(existing)[0] if existing is not None else {}
    items = _frontmatter_items(existing) if existing is not None else []
    slug = fm.get("slug") or folder.name
    title = fm.get("title") or slug.replace("-", " ").title()
    topic_type = fm.get("type") or "misc"
    status = fm.get("status") or "draft"
    maturity = fm.get("maturity") or "draft"
    created = fm.get("created") or today
    parents = _as_list(fm.get("parents"), items, "parents")
    links = _as_list(fm.get("links"), items, "links")
    aliases = _as_list(fm.get("aliases"), items, "aliases")
    tags = _as_list(fm.get("tags"), items, "tags")
    extra = _unknown_frontmatter_lines(items)

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

    # Preserve TL;DR (card included) + manual cross-links verbatim
    tldr = _tldr_block_from_text(existing) if existing is not None else ""
    if not tldr:
        tldr = f"{TLDR_HEADING}\n\n> Cốt lõi 1 dòng (TODO).\n"

    manual = (_manual_block_from_text(existing) if existing is not None else "") or (
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
        f"parents: {_fmt_list(parents)}",
        f"links: {_fmt_list(links)}",
        f"aliases: {_fmt_list(aliases)}",
        f"tags: {_fmt_list(tags)}",
        *extra,
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
    if existing is not None:
        try:
            # Compare ignoring `last_touched:` line (otherwise we'd churn daily).
            def _strip_lt(t: str) -> str:
                return re.sub(r"^last_touched: .+$", "last_touched: X", t, flags=re.M)
            if _strip_lt(existing) == _strip_lt(body):
                return readme
        except Exception:
            pass
        # v4.9 (review cards#4): card text this rebuild would drop is archived
        # verbatim to `<folder>/.history/card-<sha12>.txt` first (one file per
        # rebuild, under the moc lock `rebuild_all` holds, as `_card.py`
        # writes); when that fails NOTHING is written — the README keeps its
        # card and `_card.py --check` keeps reporting it. Cards the rebuild
        # keeps (inside the TL;DR or the manual block — `unbalanced` and
        # `duplicate` ones included) are untouched: their README is rebuilt
        # as before, never frozen.
        dropped = _dropped_cards(existing)
        if dropped and not _archive_card(readme, dropped, why="moc-rebuild"):
            _warn_card_kept(readme)
            return None

    safe_write(readme, body)
    return readme


def _warn_card_kept(readme: Path) -> None:
    msg = (f"WARN: _moc left {readme} unchanged: a topic card there sits outside its TL;DR "
           f"and could not be archived to .history/ — see `_card.py --check`")
    try:
        print(msg, file=sys.stderr)
    except Exception:
        pass
    try:
        from _debug import log_debug  # type: ignore
        log_debug("moc", msg)
    except Exception:
        pass


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
