"""Topic router (v2.2): pick or create the workspaces/<ws>/topics/<slug>.md
file for a memory entry, route to the correct in-file section.

Algorithm (route):
  1. Determine active workspace.
  2. Extract keywords from content (≥4 chars, dropping stopwords).
  3. For each existing workspaces/<ws>/topics/**/<slug>.md, count overlap.
  4. If max overlap >= settings.topic_routing.min_keyword_overlap → that slug.
  5. Else create a new topic from top-2 distinctive keywords.
  6. Else fall back to settings.topic_routing.default_topic ('misc').

Section mapping (from line prefix):
  [exp]/[reflection] → "## [exp]"
  [ref]/[tool]       → "## [ref]"
  [decision]         → "## [decision]"
  [skill-ref]        → frontmatter.links append (no body section)
  [secret-ref]       → caller routes to shared/secrets.md
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import atomic_write  # type: ignore
from _frontmatter import parse_file  # type: ignore
from _home import (  # type: ignore
    active_workspace,
    settings_path,
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

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")


def _validate_slug(slug: str) -> str:
    """Reject path-traversal and non-conforming slugs. Returns the slug if valid."""
    if not SLUG_RE.match(slug):
        raise ValueError(
            f"invalid slug: {slug!r} (must match {SLUG_RE.pattern})"
        )
    return slug


def _extract_keywords(text: str, min_len: int = 4) -> set[str]:
    words = re.findall(rf"\b\w{{{min_len},}}\b", text.lower())
    return {w for w in words if w not in STOPWORDS}


def _slugify(words: list[str]) -> str:
    s = "-".join(words)
    s = re.sub(r"[^a-z0-9-]+", "-", s.lower())
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:60] or "misc"


def _load_settings() -> dict:
    """Backward-compat alias — prefer _home.read_settings in new code."""
    from _home import read_settings  # type: ignore
    return read_settings()


def _walk_topics(ws: str | None) -> list[Path]:
    td = topics_dir(ws)
    if not td.is_dir():
        return []
    return [p for p in td.rglob("*.md") if p.name != "_MAP.md" and p.name != "_index.md"]


def detect_section(line: str) -> str | None:
    """Match `[exp]`/`[ref]`/`[decision]`/... at start (after optional `- ` bullet)."""
    m = re.match(r"^\s*[-*]?\s*\[(?P<tag>[a-z-]+)\]", line)
    if not m:
        return None
    tag = m.group("tag")
    return SECTION_FOR_PREFIX.get(tag)


def route(content: str, ws: str | None = None, settings: dict | None = None) -> tuple[str, Path, str | None]:
    """Return (slug, file_path, section_hint) for the entry.

    `ws` defaults to active workspace. `section_hint` is the markdown heading
    where the line should go (e.g. "## [exp]"); None means caller decides.
    """
    s = settings or _load_settings()
    routing = s.get("topic_routing", {}) if isinstance(s, dict) else {}
    min_overlap = int(routing.get("min_keyword_overlap", 3))
    default_topic = routing.get("default_topic", "misc")
    ws = ws or active_workspace()

    section_hint = detect_section(content.splitlines()[0] if content else "")

    kws = _extract_keywords(content)
    if not kws:
        return default_topic, _path_for(ws, default_topic), section_hint

    candidates = _walk_topics(ws)
    # Build slug→path map once: prefer frontmatter slug over filename stem.
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
        slug = fm.get("slug") or f.stem
        if SLUG_RE.match(slug) and slug not in slug_index:
            slug_index[slug] = f
        file_kws = _extract_keywords(text)
        overlap = len(kws & file_kws)
        if overlap > best_overlap:
            best_overlap = overlap
            best_slug = slug
            best_path = f

    if best_overlap >= min_overlap and best_path is not None:
        return best_slug, best_path, section_hint

    distinctive = sorted(kws, key=len, reverse=True)[:2]
    new_slug = _slugify(distinctive) or default_topic
    # P0-3: if a topic with this slug already exists (possibly nested via /mem-restructure),
    # route to it instead of creating a flat duplicate that would silently shadow the original.
    existing = slug_index.get(new_slug)
    if existing is not None:
        return new_slug, existing, section_hint
    return new_slug, _path_for(ws, new_slug), section_hint


def _path_for(ws: str, slug: str) -> Path:
    """Default flat path for a new topic. Existing nested topics take precedence
    via _walk_topics in route(); this only fires for genuinely new slugs."""
    return topics_dir(ws) / f"{slug}.md"


def ensure_topic(slug: str, ws: str | None = None, title: str | None = None, parents: list[str] | None = None) -> Path:
    """Create workspaces/<ws>/topics/<parents>/<slug>.md with v2.2 frontmatter if missing.

    Slug must match SLUG_RE; each parent must too. Path is resolved and asserted to live
    inside topics_dir(ws) to defend against any mis-routing or symlink games.
    """
    _validate_slug(slug)
    ws = ws or active_workspace()
    parents = parents or []
    for parent in parents:
        if not SLUG_RE.match(parent):
            raise ValueError(f"invalid parent segment: {parent!r}")
    base = topics_dir(ws).resolve()
    path = base
    for parent in parents:
        path = path / parent
    path.mkdir(parents=True, exist_ok=True)
    p = (path / f"{slug}.md").resolve()
    try:
        p.relative_to(base)
    except ValueError:
        raise ValueError(f"resolved path {p} escapes topics root {base}")
    if p.is_file():
        return p
    today = date.today().isoformat()
    nice_title = title or slug.replace("-", " ").title()
    body = (
        f"---\n"
        f"slug: {slug}\n"
        f"title: {nice_title}\n"
        f"status: draft\n"
        f"created: {today}\n"
        f"last_touched: {today}\n"
        f"parents: [{', '.join(parents)}]\n"
        f"links: []\n"
        f"aliases: []\n"
        f"---\n\n"
        f"# {nice_title}\n\n"
        f"> Cốt lõi 1 dòng (TODO).\n\n"
        f"## [exp]\n(empty)\n\n"
        f"## [ref]\n(empty)\n\n"
        f"## [decision]\n(empty)\n\n"
        f"## [reflection]\n(empty)\n"
    )
    atomic_write(p, body)
    return p


def list_topics(ws: str | None = None) -> list[dict]:
    """Return [{slug, title, status, last_touched, parents, path}] sorted by last_touched desc."""
    ws = ws or active_workspace()
    out: list[dict] = []
    for f in _walk_topics(ws):
        fm, _ = parse_file(f)
        parents = fm.get("parents") or []
        if not isinstance(parents, list):  # P1-3: malformed scalar coerced to []
            parents = []
        out.append({
            "slug": fm.get("slug") or f.stem,
            "title": fm.get("title") or f.stem.replace("-", " ").title(),
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
    args = ap.parse_args()
    if args.route:
        slug, path, section = route(args.route, ws=args.ws)
        print(f"{slug}\t{path}\t{section or ''}")
    elif args.list:
        for t in list_topics(args.ws):
            print(f"{t['slug']:30s} {t['status']:10s} {t['last_touched']:10s} {t['title']}")
    elif args.ensure:
        p = ensure_topic(args.ensure, ws=args.ws)
        print(p)
    else:
        ap.print_help()
