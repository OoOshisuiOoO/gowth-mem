"""Type-aware FTS5 query API for gowth-mem index.db (v3.4).

Provides:
  query_by_type(ws, tag, query, limit) -> list[dict]
    Pre-filter chunks by tag column, then rank by BM25 (or return most-recent
    when query is empty). Falls back gracefully when DB is absent or pre-migration.

All paths in returned dicts are relative to ~/.gowth-mem/ (as stored in index.db).
Does NOT affect v3.3 deterministic retrieval — BM25 + Jaccard paths in _lexical.py
are untouched. This module adds a NEW filter layer on top.

CLI:
  python3 _query.py --ws <name> --type <tag> [--query <text>] [--limit N]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _home import index_db  # type: ignore


def query_by_type(
    ws: str,
    tag: str,
    query: str = "",
    limit: int = 20,
    *,
    keyword: str = "",
    topic: str = "",
    days: int = 0,
) -> list[dict]:
    """Return chunks filtered by tag/keyword/topic/date, optionally ranked by BM25.

    Parameters
    ----------
    ws:
        Workspace name (e.g. "myproject"). Pass "" or "*" to search all workspaces.
    tag:
        Schema tag to filter on: "decision", "exp", "ref", "tool", "reflection",
        "skill-ref", "secret-ref", "goal", "hypothesis". Pass "" to skip tag filtering.
    query:
        FTS5 query string. When empty, results are ordered by rowid DESC (most recent
        first). When non-empty, results are ranked by a column-weighted BM25 —
        `bm25(chunks_fts, 5.0, 3.0, 1.0)` — so tag/keyword hits outrank body hits.
    limit:
        Maximum number of results to return.
    keyword:
        v4.0 — filter to chunks whose `keywords` column contains this token
        (auto-tag / frontmatter-tag match). LIKE substring, case-insensitive.
    topic:
        v4.0 — filter to chunks whose stored path contains `/<slug>/` (topic folder).
    days:
        v4.0 — only chunks modified within the last N days (chunk mtime cutoff).

    Returns
    -------
    list of dicts with keys: path, line_no, content, tag, keywords, bm25_score.
    line_no is always 0 (chunks table does not store per-line offsets).
    Returns empty list on any error (fail-open).
    """
    db_path = index_db()
    if not db_path.is_file():
        return []
    try:
        db = sqlite3.connect(str(db_path))
        db.execute("PRAGMA busy_timeout=2000")
        cols = {row[1] for row in db.execute("PRAGMA table_info(chunks)")}
        if "tag" not in cols:
            db.close()
            return []
        has_keywords = "keywords" in cols

        import time as _time
        mtime_cutoff = (_time.time() - days * 86400) if days and days > 0 else None

        # Shared non-FTS predicates (tag / keyword / topic / days).
        def _extra_where(params: list) -> str:
            clauses = []
            if tag:
                clauses.append("c.tag = ?")
                params.append(tag)
            if keyword and has_keywords:
                clauses.append("c.keywords LIKE ?")
                params.append(f"%{keyword.lower()}%")
            if topic:
                clauses.append("c.path LIKE ?")
                params.append(f"%/{topic}/%")
            if mtime_cutoff is not None:
                clauses.append("c.mtime >= ?")
                params.append(mtime_cutoff)
            return "".join(f" AND {c}" for c in clauses)

        kw_sel = "c.keywords" if has_keywords else "'' AS keywords"
        results: list[dict] = []

        if query.strip():
            params: list = [query]
            # Weighted BM25: tag(5) > keywords(3) > content(1) when keywords exist,
            # else fall back to (tag, content) 2-column weighting for older indexes.
            score_expr = ("bm25(chunks_fts, 5.0, 3.0, 1.0)"
                          if has_keywords else "bm25(chunks_fts, 5.0, 1.0)")
            sql = (
                f"SELECT c.path, c.content, c.tag, {kw_sel}, {score_expr} AS score "
                "FROM chunks_fts JOIN chunks c ON chunks_fts.rowid = c.id "
                "WHERE chunks_fts MATCH ?"
                + _extra_where(params)
                + " ORDER BY score LIMIT ?"
            )
            params.append(limit)
            rows = db.execute(sql, params).fetchall()
            for path, content, chunk_tag, kw, score in rows:
                if ws and ws != "*" and not _path_in_ws(path, ws):
                    continue
                results.append({"path": path, "line_no": 0, "content": content,
                                "tag": chunk_tag, "keywords": kw, "bm25_score": score})
        else:
            params = []
            sql = (
                f"SELECT c.path, c.content, c.tag, {kw_sel} "
                "FROM chunks c WHERE 1=1"
                + _extra_where(params)
                + " ORDER BY c.id DESC LIMIT ?"
            )
            params.append(limit * 3)
            rows = db.execute(sql, params).fetchall()
            for path, content, chunk_tag, kw in rows:
                if ws and ws != "*" and not _path_in_ws(path, ws):
                    continue
                results.append({"path": path, "line_no": 0, "content": content,
                                "tag": chunk_tag, "keywords": kw, "bm25_score": 0.0})
                if len(results) >= limit:
                    break

        db.close()
        return results
    except Exception:
        return []


def _path_in_ws(rel_path: str, ws: str) -> bool:
    """Return True if rel_path belongs to the given workspace.

    rel_path is relative to ~/.gowth-mem/ and follows the pattern:
      shared/<anything>           -> workspace "shared"
      workspaces/<ws>/<anything>  -> workspace <ws>

    ws="" or ws="*" means all workspaces — always returns True.
    """
    if not ws or ws == "*":
        return True
    parts = rel_path.replace("\\", "/").split("/")
    if not parts:
        return False
    if ws == "shared":
        return parts[0] == "shared"
    # workspaces/<ws>/...
    if len(parts) >= 2 and parts[0] == "workspaces":
        return parts[1] == ws
    return False


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Query gowth-mem index.db by memory type (tag)."
    )
    ap.add_argument("--ws", default="", help="Workspace name ('' = all workspaces)")
    ap.add_argument(
        "--type", dest="tag", default="",
        help="Schema tag to filter: decision|exp|ref|tool|reflection|skill-ref|secret-ref|goal|hypothesis"
             " ('' = no filter)",
    )
    ap.add_argument("--query", default="", help="FTS5 query string ('' = most recent)")
    ap.add_argument("--keyword", default="", help="Filter by auto-tag / frontmatter keyword")
    ap.add_argument("--topic", default="", help="Filter to a topic slug (path /<slug>/)")
    ap.add_argument("--days", type=int, default=0, help="Only chunks modified within N days")
    ap.add_argument("--limit", type=int, default=20, help="Max results (default 20)")
    ap.add_argument("query_pos", nargs="*", help="Query terms (joined; same as --query)")
    args = ap.parse_args()

    query = args.query or " ".join(args.query_pos)
    hits = query_by_type(ws=args.ws, tag=args.tag, query=query, limit=args.limit,
                         keyword=args.keyword, topic=args.topic, days=args.days)
    if not hits:
        print("(no results)")
        sys.exit(0)
    for hit in hits:
        score_str = f"  bm25={hit['bm25_score']:.4f}" if hit["bm25_score"] else ""
        tag_str = f"[{hit['tag']}]" if hit["tag"] else "[untagged]"
        kw = hit.get("keywords") or ""
        kw_str = f"  kw={kw}" if kw else ""
        print(f"{hit['path']}  {tag_str}{score_str}{kw_str}")
        # Compact content preview (first 120 chars, single line)
        preview = hit["content"].replace("\n", " ")[:120]
        print(f"  {preview}")
