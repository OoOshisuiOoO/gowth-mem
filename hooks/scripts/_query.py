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
from _profile import fts_match, profile  # type: ignore  # v4.3 phi(q) + safe FTS5 expr


def _fts_cols(db) -> list[str]:
    """Return chunks_fts's declared column names, in table order."""
    try:
        row = db.execute(
            "SELECT sql FROM sqlite_master WHERE name='chunks_fts' AND type='table'"
        ).fetchone()
    except sqlite3.Error:
        return []
    if not row or not row[0]:
        return []
    inner = row[0][row[0].find("(") + 1: row[0].rfind(")")]
    cols: list[str] = []
    for part in inner.split(","):
        name = part.strip().split("=")[0].strip()
        if name and "'" not in name and name in _COLUMN_WEIGHTS:
            cols.append(name)
    return cols


# Per-column BM25 weights. A term found in an entry's `[type] Title` is worth more
# than the same term buried in prose; the schema tag is the strongest signal.
_COLUMN_WEIGHTS = {"tag": 5.0, "heading": 4.0, "keywords": 3.0, "content": 1.0}


def _score_expr(cols: list[str]) -> str:
    """Build `bm25(chunks_fts, ...)` with one weight per indexed column, in order."""
    if not cols:
        return "bm25(chunks_fts)"
    weights = ", ".join(f"{_COLUMN_WEIGHTS[c]}" for c in cols)
    return f"bm25(chunks_fts, {weights})"


def _ws_predicate(ws: str, params: list, archive: bool = False) -> str:
    """Return a SQL fragment restricting rows to one workspace.

    v4.3 (D4): this used to be a Python post-filter applied AFTER `ORDER BY score
    LIMIT ?`, so a workspace whose hits ranked below the limit returned NOTHING —
    live repro: `--ws personal --limit 20 "python"` found 0 rows while 3 personal
    chunks matched. Filtering in SQL makes the limit mean "N hits in this
    workspace" instead of "N hits anywhere, then discard".
    """
    if not ws or ws == "*":
        return ""
    if archive:
        # Archived paths are `.archive/<kind>/<ws>/…`, so the live prefix pattern
        # would match nothing — filter on an embedded `/<ws>/` segment instead.
        params.append(f"%/{ws}/%")
        return " AND c.path LIKE ?"
    if ws == "shared":
        params.append("shared/%")
    else:
        params.append(f"workspaces/{ws}/%")
    return " AND c.path LIKE ?"


def query_ex(
    ws: str,
    tag: str,
    query: str = "",
    limit: int = 20,
    *,
    keyword: str = "",
    topic: str = "",
    days: int = 0,
    collapse: bool = True,
    include_archive: bool = False,
) -> dict:
    """Like `query_by_type` but reports WHY a result set is empty.

    Returns ``{"hits": list[dict], "error": str | None}``.

    `query_by_type` is a documented fail-open API returning ``list[dict]``, so it
    cannot distinguish "nothing matched" from "your query was invalid". That
    swallowed two real defects: FTS5 raised `no such column: recall` on
    `vector-recall` and every natural-language query hit FTS5's implicit AND —
    both surfaced to the user as a bare "(no results)". Callers that want the
    distinction (the /mem-recall CLI) use this; existing callers keep the old
    contract.
    """
    db_path = index_db()
    if not db_path.is_file():
        return {"hits": [], "error": f"index.db not found at {db_path} — run /mem-reindex"}

    match_expr = ""
    if query.strip():
        prof = profile(query, days=days)
        match_expr = fts_match(prof)
        if not match_expr:
            return {"hits": [],
                    "error": "query has no searchable terms (only stopwords or "
                             "punctuation) — try a distinctive word or identifier"}
    try:
        hits = _run_query(db_path, ws, tag, match_expr, limit,
                          keyword=keyword, topic=topic, days=days, collapse=collapse,
                          include_archive=include_archive)
    except sqlite3.Error as e:
        return {"hits": [], "error": f"sqlite/FTS5 error: {e}"}
    except Exception as e:  # pragma: no cover - defensive, hooks must never raise
        return {"hits": [], "error": f"{type(e).__name__}: {e}"}
    return {"hits": hits, "error": None}


def query_by_type(
    ws: str,
    tag: str,
    query: str = "",
    limit: int = 20,
    *,
    keyword: str = "",
    topic: str = "",
    days: int = 0,
    collapse: bool = True,
    include_archive: bool = False,
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
        Free-text query. v4.3: parsed into a profile by `_profile.profile()` and
        rendered as a SAFE FTS5 expression (quoted phrases joined by OR) — raw text
        is never passed to FTS5, because bare multi-word text is implicit-AND (0 hits
        for any question) and punctuation raises OperationalError. When empty,
        results are ordered by rowid DESC (most recent first). When non-empty,
        results are ranked by a column-weighted BM25 (tag 5 > heading 4 >
        keywords 3 > content 1), so a hit in an entry's `[type] Title` outranks the
        same term buried in prose. Weights are derived from the index's actual
        column arity, so an older index still scores correctly.
    limit:
        Maximum number of results to return.
    include_archive:
        v4.3 — search ONLY `.archive/**.gz` (forgotten memory past its raw TTL)
        instead of live content. Archived material is indexed but excluded by
        default so raw transcript cannot crowd out curated entries.
    collapse:
        v4.3 — keep only the best-ranked chunk per FILE (default True). Without it a
        single long file can fill every slot of `limit`. Pass False for chunk-level
        results.
    keyword:
        v4.0 — filter to chunks whose `keywords` column contains this token
        (auto-tag / frontmatter-tag match). LIKE substring, case-insensitive.
    topic:
        v4.0 — filter to chunks whose stored path contains `/<slug>/` (topic folder).
    days:
        v4.0 — only chunks modified within the last N days (chunk mtime cutoff).

    Returns
    -------
    list of dicts with keys: path, heading, line_no, content, tag, keywords,
    bm25_score. `heading` (v4.3) carries the chunk's section title — for curated
    entries that is the `[type] Title` marker, for session logs the
    `turn N — HH:MM` anchor; neither is reachable through the content column.
    line_no is always 0 (chunks table does not store per-line offsets).
    Returns empty list on any error (fail-open). Use `query_ex` when you need to
    tell "no matches" apart from "invalid query".
    """
    return query_ex(ws, tag, query, limit, keyword=keyword, topic=topic,
                    days=days, collapse=collapse,
                    include_archive=include_archive)["hits"]


def _collapse_per_path(rows: list[dict], limit: int) -> list[dict]:
    """Keep the best-ranked chunk per file, preserving overall rank order.

    Zero-Mem's `Dedup` step (eq 14) applied to gowth-mem's real failure mode: one long
    file can otherwise fill every slot of the caller's limit. Observed live before
    this: `--ws personal --limit 3 python` returned three chunks of the SAME session
    log. In a 5-query ablation on the live vault, per-path collapse was the only
    change that measurably improved ranking (MRR 1.000, vs 0.900 and 0.767 for the
    paper's entity-graph variants, which is why the graph was rejected).

    `rows` must already be in rank order; the first row seen for a path wins.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for r in rows:
        p = r.get("path") or ""
        if p in seen:
            continue
        seen.add(p)
        out.append(r)
        if len(out) >= limit:
            break
    return out


def _run_query(
    db_path,
    ws: str,
    tag: str,
    match_expr: str,
    limit: int,
    *,
    keyword: str = "",
    topic: str = "",
    days: int = 0,
    collapse: bool = True,
    include_archive: bool = False,
) -> list[dict]:
    """Execute the query. Raises on SQL/FTS5 errors so `query_ex` can report them.

    `match_expr` is already a safe FTS5 expression built by `_profile.fts_match`
    (quoted phrases joined by OR) — never raw user text. An empty `match_expr`
    means "no text query": rows are returned most-recent-first.
    """
    db = sqlite3.connect(str(db_path))
    try:
        db.execute("PRAGMA busy_timeout=2000")
        cols = {row[1] for row in db.execute("PRAGMA table_info(chunks)")}
        if "tag" not in cols:
            raise sqlite3.Error("index predates v3.4 (no tag column) — run /mem-reindex")
        has_keywords = "keywords" in cols

        import time as _time
        mtime_cutoff = (_time.time() - days * 86400) if days and days > 0 else None

        # Shared non-FTS predicates (workspace / tag / keyword / topic / days).
        # Each clause is appended to the SQL and its parameter to `params` in the
        # SAME step — placeholder order must equal bind order.
        def _extra_where(params: list) -> str:
            sql = _ws_predicate(ws, params, archive=include_archive)
            if not include_archive:
                # v4.3: `.archive/**.gz` is indexed so forgotten memory stays
                # FINDABLE, but archived raw transcript must not pollute normal
                # recall — opt in with include_archive / `--archive`.
                sql += " AND c.path NOT LIKE '.archive/%'"
            else:
                sql += " AND c.path LIKE '.archive/%'"

            if tag:
                sql += " AND c.tag = ?"
                params.append(tag)
            if keyword and has_keywords:
                sql += " AND c.keywords LIKE ?"
                params.append(f"%{keyword.lower()}%")
            if topic:
                sql += " AND c.path LIKE ?"
                params.append(f"%/{topic}/%")
            if mtime_cutoff is not None:
                sql += " AND c.mtime >= ?"
                params.append(mtime_cutoff)
            return sql

        kw_sel = "c.keywords" if has_keywords else "'' AS keywords"
        results: list[dict] = []

        # Collapse discards rows, so fetch a wider window first and trim after —
        # trimming before collapsing would return fewer than `limit` distinct files.
        fetch = min(limit * 4, 400) if collapse else limit

        if match_expr:
            params: list = [match_expr]
            # bm25() weights are POSITIONAL over the chunks_fts columns, so they must
            # match the arity of whatever schema version this index is at. Weighting:
            # tag(5) > heading(4) > keywords(3) > content(1) — a hit in an entry's
            # `[type] Title` outranks a hit buried in prose.
            score_expr = _score_expr(_fts_cols(db))
            sql = (
                f"SELECT c.path, c.heading, c.content, c.tag, {kw_sel}, "
                f"{score_expr} AS score "
                "FROM chunks_fts JOIN chunks c ON chunks_fts.rowid = c.id "
                "WHERE chunks_fts MATCH ?"
                + _extra_where(params)
                + " ORDER BY score LIMIT ?"
            )
            params.append(fetch)
            for path, heading, content, chunk_tag, kw, score in db.execute(sql, params):
                results.append({"path": path, "heading": heading or "", "line_no": 0,
                                "content": content, "tag": chunk_tag,
                                "keywords": kw, "bm25_score": score})
        else:
            params = []
            sql = (
                f"SELECT c.path, c.heading, c.content, c.tag, {kw_sel} "
                "FROM chunks c WHERE 1=1"
                + _extra_where(params)
                + " ORDER BY c.id DESC LIMIT ?"
            )
            params.append(fetch)
            for path, heading, content, chunk_tag, kw in db.execute(sql, params):
                results.append({"path": path, "heading": heading or "", "line_no": 0,
                                "content": content, "tag": chunk_tag,
                                "keywords": kw, "bm25_score": 0.0})
        if collapse:
            results = _collapse_per_path(results, limit)
        return results[:limit]
    finally:
        db.close()


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
    ap.add_argument("--archive", action="store_true",
                    help="search archived (forgotten) memory under .archive/ instead "
                         "of live content")
    ap.add_argument("query_pos", nargs="*", help="Query terms (joined; same as --query)")
    args = ap.parse_args()

    query = args.query or " ".join(args.query_pos)
    res = query_ex(ws=args.ws, tag=args.tag, query=query, limit=args.limit,
                   keyword=args.keyword, topic=args.topic, days=args.days,
                   include_archive=args.archive)
    hits = res["hits"]
    if res["error"]:
        # v4.3: an invalid or unsearchable query is NOT "no results" — say so, or the
        # user silently concludes the vault is empty.
        print(f"(query problem: {res['error']})")
        sys.exit(0)
    if not hits:
        print("(no results)")
        sys.exit(0)
    for hit in hits:
        score_str = f"  bm25={hit['bm25_score']:.4f}" if hit["bm25_score"] else ""
        tag_str = f"[{hit['tag']}]" if hit["tag"] else "[untagged]"
        kw = hit.get("keywords") or ""
        kw_str = f"  kw={kw}" if kw else ""
        print(f"{hit['path']}  {tag_str}{score_str}{kw_str}")
        # The heading carries the `[type] Title` marker (curated entries) or the
        # `turn N — HH:MM` anchor (session logs) — the most locating line available.
        heading = (hit.get("heading") or "").strip()
        if heading:
            print(f"  § {heading[:110]}")
        # Compact content preview (first 120 chars, single line)
        preview = hit["content"].replace("\n", " ")[:120]
        print(f"  {preview}")
