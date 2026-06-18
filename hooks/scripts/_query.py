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
) -> list[dict]:
    """Return chunks filtered by tag, optionally ranked by BM25.

    Parameters
    ----------
    ws:
        Workspace name (e.g. "myproject"). Pass "" or "*" to search all workspaces.
    tag:
        Schema tag to filter on: "decision", "exp", "ref", "tool", "reflection",
        "skill-ref", "secret-ref", "goal", "hypothesis". Pass "" to skip tag filtering (returns all chunks
        ranked by query — same as legacy behaviour).
    query:
        FTS5 query string. When empty, results are ordered by rowid DESC (most recent
        first). When non-empty, results are ranked by BM25 score ascending (lower =
        better in FTS5 convention).
    limit:
        Maximum number of results to return.

    Returns
    -------
    list of dicts with keys: path, line_no, content, tag, bm25_score.
    line_no is always 0 (chunks table does not store per-line offsets).
    Returns empty list on any error (fail-open).
    """
    db_path = index_db()
    if not db_path.is_file():
        return []
    try:
        db = sqlite3.connect(str(db_path))
        db.execute("PRAGMA busy_timeout=2000")
        # Guard: check that tag column exists (pre-migration DB).
        cols = {row[1] for row in db.execute("PRAGMA table_info(chunks)")}
        if "tag" not in cols:
            db.close()
            return []

        results: list[dict] = []

        if query.strip():
            # BM25 path: filter tag in WHERE, rank by FTS5 bm25().
            # chunks_fts has columns (tag, content); bm25() scores the whole row.
            if tag:
                # Filter by both tag column in base table AND tag in FTS index.
                sql = """
                    SELECT c.path, c.content, c.tag,
                           bm25(chunks_fts) AS score
                    FROM chunks_fts
                    JOIN chunks c ON chunks_fts.rowid = c.id
                    WHERE chunks_fts MATCH ?
                      AND c.tag = ?
                    ORDER BY score
                    LIMIT ?
                """
                rows = db.execute(sql, (query, tag, limit)).fetchall()
            else:
                sql = """
                    SELECT c.path, c.content, c.tag,
                           bm25(chunks_fts) AS score
                    FROM chunks_fts
                    JOIN chunks c ON chunks_fts.rowid = c.id
                    WHERE chunks_fts MATCH ?
                    ORDER BY score
                    LIMIT ?
                """
                rows = db.execute(sql, (query, limit)).fetchall()
            for path, content, chunk_tag, score in rows:
                # Optionally filter by workspace prefix.
                if ws and ws != "*":
                    if not _path_in_ws(path, ws):
                        continue
                results.append({
                    "path": path,
                    "line_no": 0,
                    "content": content,
                    "tag": chunk_tag,
                    "bm25_score": score,
                })
        else:
            # No query: return most-recent rows matching tag (rowid DESC).
            if tag:
                sql = """
                    SELECT c.path, c.content, c.tag
                    FROM chunks c
                    WHERE c.tag = ?
                    ORDER BY c.id DESC
                    LIMIT ?
                """
                rows = db.execute(sql, (tag, limit * 3)).fetchall()
            else:
                sql = """
                    SELECT c.path, c.content, c.tag
                    FROM chunks c
                    ORDER BY c.id DESC
                    LIMIT ?
                """
                rows = db.execute(sql, (limit * 3,)).fetchall()
            for path, content, chunk_tag in rows:
                if ws and ws != "*":
                    if not _path_in_ws(path, ws):
                        continue
                results.append({
                    "path": path,
                    "line_no": 0,
                    "content": content,
                    "tag": chunk_tag,
                    "bm25_score": 0.0,
                })
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
    ap.add_argument("--limit", type=int, default=20, help="Max results (default 20)")
    args = ap.parse_args()

    hits = query_by_type(ws=args.ws, tag=args.tag, query=args.query, limit=args.limit)
    if not hits:
        print("(no results)")
        sys.exit(0)
    for hit in hits:
        score_str = f"  bm25={hit['bm25_score']:.4f}" if hit["bm25_score"] else ""
        tag_str = f"[{hit['tag']}]" if hit["tag"] else "[untagged]"
        print(f"{hit['path']}  {tag_str}{score_str}")
        # Compact content preview (first 120 chars, single line)
        preview = hit["content"].replace("\n", " ")[:120]
        print(f"  {preview}")
