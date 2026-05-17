"""Topic router (v3.0): topic = FOLDER named <slug>; files inside are dated aspects.

v3 layout — `~/.gowth-mem/workspaces/<ws>/<slug>/`:
  - `00-README.md`                     MOC (TL;DR + auto-index of aspects + manual cross-links)
  - `YYYY-MM-DD-<aspect>.md`           dated aspect file (one context per day)
  - `lessons.md`                       per-folder evergreen ledger (5-field schema)

Reserved at workspace root (NEVER topic): docs, journal, skills, research.
Reserved at workspace root files: _MAP.md, AGENTS.md, workspace.json.
Reserved INSIDE a topic folder: 00-README.md, lessons.md, _MAP.md (forbidden as
aspect slug; rejected by ASPECT_SLUG_RE / blocklist).

Wikilink `[[slug]]` resolves to `<ws>/<slug>/00-README.md` (legacy `<slug>/<slug>.md`
and flat `<slug>.md` still recognised for partial-migration state — see `_wikilink.py`).

`route()` algorithm (v3 §2.2):
  1. Active workspace.
  2. Side-channels: `[secret-ref]` → shared/secrets.md; `[skill-ref]` → workspaces/<ws>/skills/<slug>.md.
  3. Best topic-folder match by keyword overlap (Jaccard) ≥ min_overlap.
  4. New topic → folder slug from top-2 distinctive keywords.
  5. ensure_topic_folder() — F3 fix: always idempotent mkdir + skeleton README.
  6. Aspect slug from top 3-5 distinctive keywords (kebab-case, ≤60).
  7. Target = `<folder>/YYYY-MM-DD-<aspect>.md` (existing today's file → append).
  8. `00-README.md` is NEVER the route target — rebuilt separately by `_moc.rebuild_topic_readme`.

`resolve_topic_folder(slug, ws)` (F4): folder-only resolver for callers (lessons,
reflections, evergreen ledger) that need the folder Path WITHOUT creating a
dated aspect file. Shares `ensure_topic_folder` with `route()` so the folder
exists when callers append to `lessons.md`.

Section mapping (from line prefix):
  [exp]/[reflection] → "## [exp]"
  [ref]/[tool]       → "## [ref]"
  [decision]         → "## [decision]"
  [skill-ref]        → side-channel, no body section
  [secret-ref]       → caller routes to shared/secrets.md
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import safe_write  # type: ignore
from _frontmatter import parse_file  # type: ignore
from _home import (  # type: ignore
    RESERVED_FILES,
    RESERVED_SUBDIRS,
    RESERVED_TOPIC_FILES,
    TOPIC_README,
    active_workspace,
    is_reserved,
    is_topic_folder,
    iter_topic_files,
    iter_topic_landings,
    read_settings,
    shared_dir,
    skills_dir,
    slug_for_path,
    topic_landing,
    topic_readme,
    topics_dir,
    workspace_dir,
)

STOPWORDS = {
    "this", "that", "with", "from", "have", "been", "were", "they", "them",
    "their", "there", "would", "could", "should", "about", "which", "what",
    "when", "where", "while", "into", "than", "then", "some", "such", "very",
    "just", "only", "your", "you", "for", "the", "and", "but", "not", "all",
    "any", "are", "was", "has", "had", "its", "out", "via", "use", "uses",
    "used", "using",
}

SECTION_FOR_PREFIX = {
    "exp": "## [exp]",
    "reflection": "## [exp]",  # reflections live in exp section per AGENTS.md
    "ref": "## [ref]",
    "tool": "## [ref]",
    "decision": "## [decision]",
}

# v3.0: topic folder slug (unique per workspace)
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")

# v3.0: aspect slug (the <aspect> portion of `YYYY-MM-DD-<aspect>.md`)
ASPECT_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")

# Inside-topic-folder names that cannot be used as an aspect slug.
ASPECT_BLOCKLIST = frozenset({"readme", "lessons", "00-readme"})


def _validate_slug(slug: str) -> str:
    """Reject path-traversal and non-conforming topic slugs."""
    if not SLUG_RE.match(slug):
        raise ValueError(
            f"invalid slug: {slug!r} (must match {SLUG_RE.pattern})"
        )
    return slug


def _validate_aspect_slug(slug: str) -> str:
    """Reject invalid aspect slugs (must match ASPECT_SLUG_RE, not in blocklist,
    not leading `_`, not pure digits — digits would collide with the date prefix)."""
    if not slug:
        raise ValueError("empty aspect slug")
    if slug in ASPECT_BLOCKLIST:
        raise ValueError(f"aspect slug {slug!r} is reserved (00-README/lessons)")
    if slug.startswith("_"):
        raise ValueError(f"aspect slug {slug!r} cannot start with '_'")
    if slug.isdigit():
        raise ValueError(f"aspect slug {slug!r} cannot be pure digits (date prefix collision)")
    if not ASPECT_SLUG_RE.match(slug):
        raise ValueError(
            f"invalid aspect slug: {slug!r} (must match {ASPECT_SLUG_RE.pattern})"
        )
    return slug


def _extract_keywords(text: str, min_len: int = 4) -> set[str]:
    words = re.findall(rf"\b\w{{{min_len},}}\b", text.lower())
    return {w for w in words if w not in STOPWORDS}


def _slugify(words: list[str], max_len: int = 60) -> str:
    s = "-".join(words)
    s = re.sub(r"[^a-z0-9-]+", "-", s.lower())
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:max_len] or "misc"


def derive_aspect_slug(content: str, max_words: int = 5, max_len: int = 60) -> str:
    """v3.0: derive `<aspect>` from top 3-5 distinctive keywords of an entry.

    Returns a sanitized kebab-case slug ≤max_len chars. Falls back to 'note'
    when keyword extraction yields no usable slug, or when the candidate
    collides with the blocklist (00-README / lessons) / leading `_` / pure digits.
    """
    words = _extract_keywords(content)
    if not words:
        return "note"
    ranked = sorted(words, key=len, reverse=True)[:max_words]
    candidate = _slugify(ranked, max_len=max_len)
    if not candidate or candidate in ASPECT_BLOCKLIST or candidate.startswith("_") or candidate.isdigit():
        return "note"
    if not ASPECT_SLUG_RE.match(candidate):
        return "note"
    return candidate


def _walk_topics(ws: str | None) -> list[Path]:
    """v3.0: walks workspace root, skipping reserved subdirs + reserved files."""
    return iter_topic_files(ws)


def detect_section(line: str) -> str | None:
    """Match `[exp]`/`[ref]`/`[decision]`/... at start (after optional `- ` bullet)."""
    m = re.match(r"^\s*[-*]?\s*\[(?P<tag>[a-z-]+)\]", line)
    if not m:
        return None
    tag = m.group("tag")
    return SECTION_FOR_PREFIX.get(tag)


def _detect_line_type(line: str) -> str | None:
    """Return the raw line-type tag (`exp`/`ref`/`decision`/`tool`/`reflection`/
    `skill-ref`/`secret-ref`) or None. Used for side-channel routing."""
    m = re.match(r"^\s*[-*]?\s*\[(?P<tag>[a-z-]+)\]", line)
    if not m:
        return None
    return m.group("tag")


def _today_aspect_path(folder: Path, aspect_slug: str, today: str | None = None) -> Path:
    """v3.0: build `<folder>/YYYY-MM-DD-<aspect>.md` for today (or supplied date)."""
    today = today or date.today().isoformat()
    return folder / f"{today}-{aspect_slug}.md"


def ensure_topic_folder(slug: str, ws: str | None = None,
                        title: str | None = None,
                        parents: list[str] | None = None,
                        topic_type: str = "misc",
                        summary: str = "") -> Path:
    """v3.0 (F3 fix): idempotent — mkdir + write empty `00-README.md` if missing.

    Returns the topic FOLDER path (not the README path). Existing folder is
    untouched; existing README is preserved verbatim. Without this idempotent
    ensure, writes to `<slug>/<date>-<aspect>.md` silently fail when the folder
    doesn't exist on the local machine (e.g. multi-machine sync edge case).
    """
    from _topic_templates import render as _render_readme  # type: ignore

    _validate_slug(slug)
    if is_reserved(slug) or is_reserved(f"{slug}.md"):
        raise ValueError(
            f"slug {slug!r} collides with reserved name "
            f"(docs/journal/skills/research/_MAP/AGENTS/workspace.json)"
        )

    ws = ws or active_workspace()
    parents = parents or []
    for parent in parents:
        if not SLUG_RE.match(parent):
            raise ValueError(f"invalid parent segment: {parent!r}")
        if parent in RESERVED_SUBDIRS:
            raise ValueError(f"parent {parent!r} is a reserved subdir")

    base = workspace_dir(ws).resolve()
    folder = base
    for parent in parents:
        folder = folder / parent
    folder = folder / slug
    folder = folder.resolve() if folder.exists() else folder
    folder.mkdir(parents=True, exist_ok=True)

    # Path-escape guard (resolve AFTER mkdir so symlinks are caught)
    resolved = folder.resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"resolved path {resolved} escapes workspace root {base}") from exc

    readme = resolved / TOPIC_README
    if not readme.is_file():
        today = date.today().isoformat()
        nice_title = title or slug.replace("-", " ").title()
        body = _render_readme(topic_type, slug, nice_title, today, parents, summary)
        safe_write(readme, body)

    return resolved


def resolve_topic_folder(slug: str, ws: str | None = None) -> Path:
    """v3.0 (F4 fix): folder-only resolver for lessons / reflections / evergreen.

    Returns the topic FOLDER path. Idempotently ensures the folder exists
    (via `ensure_topic_folder`) but does NOT create any dated aspect file.
    Use this when the caller writes to `<folder>/lessons.md` directly.
    """
    return ensure_topic_folder(slug, ws=ws)


def derive_topic_slug(content: str, ws: str | None = None,
                      settings: dict | None = None) -> str:
    """v3.0: return the topic FOLDER slug for `content` without spawning files.

    Mirrors `route()` slug-selection logic (existing-topic match by keyword
    overlap, else top-2 distinctive keywords, else default `misc`) but never
    calls `ensure_topic_folder` and never returns a file path. Used by
    `_lesson.py` to pick the topic folder for `lessons.md` without creating
    a parasitic dated-aspect file as a side-effect.
    """
    s = settings or read_settings()
    routing = s.get("topic_routing", {}) if isinstance(s, dict) else {}
    min_overlap = int(routing.get("min_keyword_overlap", 3))
    default_topic = routing.get("default_topic", "misc")
    ws = ws or active_workspace()

    kws = _extract_keywords(content)
    if not kws:
        return default_topic

    ws_root = workspace_dir(ws).resolve()
    best_slug = default_topic
    best_overlap = 0
    for f in _walk_topics(ws):
        try:
            text = f.read_text(errors="ignore")
        except Exception:
            continue
        fm, _ = parse_file(f)
        slug = fm.get("slug") or slug_for_path(f, ws_root)
        if not SLUG_RE.match(slug):
            continue
        overlap = len(kws & _extract_keywords(text))
        if overlap > best_overlap:
            best_overlap = overlap
            best_slug = slug

    if best_overlap >= min_overlap:
        return best_slug

    distinctive = sorted(kws, key=len, reverse=True)[:2]
    return _slugify(distinctive) or default_topic


def route(content: str, ws: str | None = None,
          settings: dict | None = None) -> tuple[str, Path, str | None]:
    """v3.0: return `(slug, file_path, section_hint)` for a memory entry.

    `slug` = topic folder name. `file_path` = the EXACT file to append to
    (today's dated aspect, NEVER `00-README.md`). `section_hint` is the
    in-file heading (e.g. "## [exp]") or None for caller-decides.

    Side-channels (returned early, no folder ensure):
      `[secret-ref]` → `shared/secrets.md`
      `[skill-ref]`  → `workspaces/<ws>/skills/<slug>.md`

    Otherwise routes to `<ws>/<slug>/<today>-<aspect>.md`.
    """
    s = settings or read_settings()
    routing = s.get("topic_routing", {}) if isinstance(s, dict) else {}
    min_overlap = int(routing.get("min_keyword_overlap", 3))
    default_topic = routing.get("default_topic", "misc")
    ws = ws or active_workspace()

    first_line = content.splitlines()[0] if content else ""
    line_type = _detect_line_type(first_line)
    section_hint = SECTION_FOR_PREFIX.get(line_type) if line_type else None

    # Side-channel: [secret-ref] → shared/secrets.md
    if line_type == "secret-ref":
        return ("secrets", shared_dir() / "secrets.md", None)

    # Side-channel: [skill-ref] → workspaces/<ws>/skills/<slug>.md
    if line_type == "skill-ref":
        skill_slug = _derive_skill_slug(content) or default_topic
        return (skill_slug, skills_dir(ws) / f"{skill_slug}.md", None)

    kws = _extract_keywords(content)
    if not kws:
        # No keywords → default topic folder + today's "note" aspect
        folder = ensure_topic_folder(default_topic, ws=ws)
        return (default_topic, _today_aspect_path(folder, "note"), section_hint)

    ws_root = workspace_dir(ws).resolve()
    candidates = _walk_topics(ws)
    slug_index: dict[str, Path] = {}
    best_slug = default_topic
    best_overlap = 0
    best_path: Path | None = None
    for f in candidates:
        try:
            text = f.read_text(errors="ignore")
        except Exception:
            continue
        fm, _ = parse_file(f)
        slug = fm.get("slug") or slug_for_path(f, ws_root)
        if SLUG_RE.match(slug) and slug not in slug_index:
            slug_index[slug] = f
        file_kws = _extract_keywords(text)
        overlap = len(kws & file_kws)
        if overlap > best_overlap:
            best_overlap = overlap
            best_slug = slug
            best_path = f

    if best_overlap >= min_overlap and best_path is not None:
        # Match an existing topic — route to ITS folder's today-dated aspect.
        matched_folder = best_path.parent if best_path.parent != ws_root else None
        if matched_folder is not None:
            # F3 fix: ensure folder + README exist even when slug matched via index
            # (multi-machine sync may have deleted the folder locally).
            ensure_topic_folder(best_slug, ws=ws)
            aspect_slug = derive_aspect_slug(content)
            return (best_slug, _today_aspect_path(matched_folder, aspect_slug), section_hint)
        # Legacy flat match at ws root — promote to folder for new aspect.
        ensure_topic_folder(best_slug, ws=ws)
        new_folder = ws_root / best_slug
        aspect_slug = derive_aspect_slug(content)
        return (best_slug, _today_aspect_path(new_folder, aspect_slug), section_hint)

    # No good match → new topic from top-2 distinctive keywords.
    distinctive = sorted(kws, key=len, reverse=True)[:2]
    new_slug = _slugify(distinctive) or default_topic

    # If the new slug already exists in the index (e.g. nested via /mem-restructure),
    # route to that folder instead of shadowing.
    existing = slug_index.get(new_slug)
    if existing is not None:
        existing_folder = existing.parent if existing.parent != ws_root else None
        if existing_folder is not None:
            ensure_topic_folder(new_slug, ws=ws)
            aspect_slug = derive_aspect_slug(content)
            return (new_slug, _today_aspect_path(existing_folder, aspect_slug), section_hint)

    folder = ensure_topic_folder(new_slug, ws=ws)
    aspect_slug = derive_aspect_slug(content)
    return (new_slug, _today_aspect_path(folder, aspect_slug), section_hint)


def _derive_skill_slug(content: str) -> str | None:
    """Pull `[skill-ref:<slug>]` or fall back to first distinctive keyword."""
    m = re.search(r"\[skill-ref:([a-z0-9][a-z0-9-]{0,59})\]", content)
    if m:
        return m.group(1)
    kws = _extract_keywords(content)
    if not kws:
        return None
    distinctive = sorted(kws, key=len, reverse=True)[:1]
    return _slugify(distinctive) or None


def ensure_topic(slug: str, ws: str | None = None, title: str | None = None,
                 parents: list[str] | None = None, topic_type: str = "misc",
                 summary: str = "") -> Path:
    """v3.0: Create `workspaces/<ws>/<parents>/<slug>/00-README.md` from skeleton.

    Returns the README path (the canonical landing for the topic). Idempotent —
    existing README preserved. Folder is created if missing. Use this when
    callers need a stable landing path (e.g. /mem-topic --ensure); for routine
    writes use `route()` (dated aspect) or `resolve_topic_folder()` (folder-only).
    """
    folder = ensure_topic_folder(slug, ws=ws, title=title, parents=parents,
                                 topic_type=topic_type, summary=summary)
    return folder / TOPIC_README


def list_topics(ws: str | None = None) -> list[dict]:
    """Return [{slug, title, status, last_touched, parents, path}] sorted by last_touched desc.

    `path` points to the topic's README (canonical landing), not to a specific aspect.
    """
    ws = ws or active_workspace()
    ws_root = workspace_dir(ws).resolve()
    out: list[dict] = []
    seen: set[str] = set()
    for f in iter_topic_landings(ws):
        fm, _ = parse_file(f)
        slug = fm.get("slug") or slug_for_path(f, ws_root)
        if slug in seen:
            continue
        seen.add(slug)
        parents = fm.get("parents") or []
        if not isinstance(parents, list):
            parents = []
        out.append({
            "slug": slug,
            "title": fm.get("title") or slug.replace("-", " ").title(),
            "status": fm.get("status") or "",
            "last_touched": fm.get("last_touched") or "",
            "parents": parents,
            "path": f,
        })
    out.sort(key=lambda d: d.get("last_touched") or "", reverse=True)
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--route")
    ap.add_argument("--ws")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--ensure")
    ap.add_argument("--type", default="misc",
                    help="topic type for --ensure (runbook/incident/reference/research/strategy/how-to/concept/decision/tool/misc)")
    ap.add_argument("--title", default=None, help="title for --ensure (default: derived from slug)")
    ap.add_argument("--parents", default="", help="comma-separated parent segments for --ensure")
    ap.add_argument("--summary", default="", help="cốt lõi 1-line summary for --ensure")
    args = ap.parse_args()
    if args.route:
        slug, path, section = route(args.route, ws=args.ws)
        print(f"{slug}\t{path}\t{section or ''}")
    elif args.list:
        for t in list_topics(args.ws):
            print(f"{t['slug']:30s} {t['status']:10s} {t['last_touched']:10s} {t['title']}")
    elif args.ensure:
        parents = [p for p in args.parents.split(",") if p.strip()]
        p = ensure_topic(args.ensure, ws=args.ws, title=args.title,
                         parents=parents, topic_type=args.type, summary=args.summary)
        print(p)
    else:
        ap.print_help()
