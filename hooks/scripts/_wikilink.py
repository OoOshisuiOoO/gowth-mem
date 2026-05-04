"""Wikilink resolver for v2.2.

Forms accepted (Obsidian-style):
  [[slug]]              same workspace
  [[ws:slug]]           cross-workspace explicit
  [[shared:secrets]]    shared registry
  [[slug|alt]]          alias display text
  [[slug#section]]      section anchor

Resolution order for a (ws, slug):
  1. index.db.slugs row (workspace, slug) → path
  2. Filesystem fallback: workspaces/<ws>/**/<slug>.md (excluding reserved subdirs; first match)
  3. Aliases scan via index.db.slugs (slug == alias)
  4. None (broken link)

`shared` is a virtual workspace pointing to `shared/<slug>.md`.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _home import (  # type: ignore
    active_workspace,
    index_db,
    shared_dir,
    topics_dir,
)

WIKILINK_RE = re.compile(r"\[\[([^\[\]\|#]+)(#[^\[\]\|]+)?(\|[^\[\]]+)?\]\]")


def parse_token(raw: str) -> tuple[str | None, str]:
    """Split 'ws:slug' → ('ws', 'slug'); plain 'slug' → (None, 'slug')."""
    raw = raw.strip()
    if ":" in raw:
        ws, slug = raw.split(":", 1)
        return ws.strip() or None, slug.strip()
    return None, raw


def parse(text: str) -> list[dict]:
    """Return [{raw, target_ws, slug, section, alias}] for every wikilink in text."""
    out: list[dict] = []
    for m in WIKILINK_RE.finditer(text):
        raw_target = m.group(1)
        section = (m.group(2) or "")[1:] or None
        alias = (m.group(3) or "")[1:] or None
        ws, slug = parse_token(raw_target)
        out.append({
            "raw": m.group(0),
            "target_ws": ws,
            "slug": slug,
            "section": section,
            "alias": alias,
        })
    return out


def _query_db(ws: str, slug: str) -> Path | None:
    db_path = index_db()
    if not db_path.is_file():
        return None
    try:
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return None
    try:
        cur = db.execute(
            "SELECT path FROM slugs WHERE workspace=? AND slug=? LIMIT 1",
            (ws, slug),
        )
        row = cur.fetchone()
        if row:
            return Path(row[0])
        # alias fallback — sentinel-wrapped exact-token match (P0-4 fix)
        cur = db.execute(
            "SELECT path FROM slugs WHERE workspace=? AND aliases LIKE ? LIMIT 1",
            (ws, f"%,{slug},%"),
        )
        row = cur.fetchone()
        if row:
            return Path(row[0])
        return None
    except sqlite3.OperationalError:
        return None
    finally:
        db.close()


def _fs_fallback(ws: str, slug: str) -> Path | None:
    """v2.4: prefer Obsidian folder-note landing `<slug>/<slug>.md`, then any flat `<slug>.md`."""
    if ws == "shared":
        cand = shared_dir() / f"{slug}.md"
        return cand if cand.is_file() else None
    base = topics_dir(ws)
    if not base.is_dir():
        return None
    # Prefer folder-note landing
    for f in base.rglob(f"{slug}/{slug}.md"):
        return f
    # Fall back to any matching flat file (legacy v2.3)
    for f in base.rglob(f"{slug}.md"):
        return f
    return None


def resolve(token: str, current_ws: str | None = None) -> Path | None:
    """Resolve a single token like 'slug', 'ws:slug', 'shared:secrets' → Path or None."""
    target_ws, slug = parse_token(token)
    ws = target_ws or current_ws or active_workspace()
    p = _query_db(ws, slug)
    if p:
        return p
    return _fs_fallback(ws, slug)


def resolve_all(text: str, current_ws: str | None = None) -> list[dict]:
    """Resolve every wikilink in `text`. Augments parse() output with `path`."""
    current_ws = current_ws or active_workspace()
    out = parse(text)
    for item in out:
        ws = item["target_ws"] or current_ws
        item["path"] = _fs_fallback(ws, item["slug"]) or _query_db(ws, item["slug"])
        item["broken"] = item["path"] is None
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("token", help="e.g. ema-cross or trade:ema-cross or shared:secrets")
    ap.add_argument("--ws", help="current workspace context")
    args = ap.parse_args()
    p = resolve(args.token, current_ws=args.ws)
    print(p or "(unresolved)")
