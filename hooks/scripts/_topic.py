"""Topic router: pick or create the topics/<slug>.md file for a memory entry.

Algorithm:
  1. Extract keywords from content (\\b\\w{4,}\\b, lowercased, deduped, drop stopwords).
  2. For each existing topics/*.md, count keyword overlap with file content.
  3. If max_overlap >= settings.topic_routing.min_keyword_overlap → that slug.
  4. Else create a new topic from top-2 most-distinctive keywords.
  5. Else fall back to settings.topic_routing.default_topic ('misc').

Slug generation:
  - lowercased, hyphen-separated, alphanum only, max 40 chars
  - never contains stopwords
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _home import gowth_home, settings_path, topics_dir  # type: ignore

STOPWORDS = {
    "this", "that", "with", "from", "have", "been", "were", "they", "them",
    "their", "there", "would", "could", "should", "about", "which", "what",
    "when", "where", "while", "into", "than", "then", "some", "such", "very",
    "just", "only", "your", "you", "for", "the", "and", "but", "not", "all",
    "any", "are", "was", "has", "had", "its", "out", "via", "use", "uses",
    "used", "using",
}


def _extract_keywords(text: str, min_len: int = 4) -> set[str]:
    words = re.findall(rf"\b\w{{{min_len},}}\b", text.lower())
    return {w for w in words if w not in STOPWORDS}


def _slugify(words: list[str]) -> str:
    s = "-".join(words)
    s = re.sub(r"[^a-z0-9-]+", "-", s.lower())
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:40] or "misc"


def _load_settings() -> dict:
    p = settings_path()
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def route(content: str, settings: dict | None = None) -> str:
    """Return topic slug for the given memory entry."""
    s = settings or _load_settings()
    routing = s.get("topic_routing", {}) if isinstance(s, dict) else {}
    min_overlap = int(routing.get("min_keyword_overlap", 3))
    default_topic = routing.get("default_topic", "misc")

    kws = _extract_keywords(content)
    if not kws:
        return default_topic

    td = topics_dir()
    if not td.is_dir():
        # Suggest a new topic from the top keywords.
        top = sorted(kws, key=len, reverse=True)[:2]
        return _slugify(top) or default_topic

    best_slug = default_topic
    best_overlap = 0
    for f in sorted(td.glob("*.md")):
        if f.name == "_index.md":
            continue
        try:
            text = f.read_text(errors="ignore")
        except Exception:
            continue
        file_kws = _extract_keywords(text)
        overlap = len(kws & file_kws)
        if overlap > best_overlap:
            best_overlap = overlap
            best_slug = f.stem

    if best_overlap >= min_overlap:
        return best_slug

    # Create new topic name from top-2 distinctive keywords (those not in any
    # existing topic).
    distinctive = sorted(kws, key=len, reverse=True)[:2]
    new_slug = _slugify(distinctive)
    return new_slug or default_topic


def ensure_topic(slug: str, title: str | None = None) -> Path:
    """Create topics/<slug>.md if missing. Return path."""
    from datetime import date
    td = topics_dir()
    td.mkdir(parents=True, exist_ok=True)
    p = td / f"{slug}.md"
    if p.is_file():
        return p
    today = date.today().isoformat()
    nice_title = title or slug.replace("-", " ").title()
    p.write_text(
        f"---\nslug: {slug}\ntitle: {nice_title}\ncreated: {today}\nlast_touch: {today}\n---\n\n"
        f"# {nice_title}\n\n"
    )
    return p


def list_topics() -> list[tuple[str, str, float]]:
    """Return [(slug, title, mtime)] sorted by mtime desc."""
    td = topics_dir()
    if not td.is_dir():
        return []
    out: list[tuple[str, str, float]] = []
    for f in td.glob("*.md"):
        if f.name == "_index.md":
            continue
        title = f.stem.replace("-", " ").title()
        try:
            head = f.read_text(errors="ignore").splitlines()[:10]
            for line in head:
                m = re.match(r"^title:\s*(.+)$", line.strip())
                if m:
                    title = m.group(1).strip()
                    break
        except Exception:
            pass
        out.append((f.stem, title, f.stat().st_mtime))
    out.sort(key=lambda t: t[2], reverse=True)
    return out


def regenerate_index() -> None:
    """Rewrite topics/_index.md from current topic files."""
    from datetime import datetime
    td = topics_dir()
    if not td.is_dir():
        return
    rows = list_topics()
    lines = ["# Topic Index\n"]
    for slug, title, mtime in rows:
        ts = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
        lines.append(f"- [[{slug}]] — {title} (touched {ts})")
    sys.path.insert(0, str(Path(__file__).parent))
    from _atomic import atomic_write  # type: ignore
    atomic_write(td / "_index.md", "\n".join(lines) + "\n")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--route", help="Route content to a topic slug")
    ap.add_argument("--list", action="store_true", help="List topics")
    ap.add_argument("--regen-index", action="store_true", help="Regenerate _index.md")
    ap.add_argument("--ensure", help="Ensure topic exists (slug)")
    args = ap.parse_args()
    if args.route:
        print(route(args.route))
    elif args.list:
        for slug, title, mtime in list_topics():
            print(f"{slug}\t{title}")
    elif args.regen_index:
        regenerate_index()
        print(f"regenerated {topics_dir() / '_index.md'}")
    elif args.ensure:
        p = ensure_topic(args.ensure)
        print(p)
    else:
        ap.print_help()
