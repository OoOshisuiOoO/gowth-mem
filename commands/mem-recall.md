---
description: "Search workspace memory with optional tag filter (v3.4). Pre-filters chunks by [decision]/[exp]/[ref]/[tool]/[reflection]/[skill-ref]/[secret-ref]/[goal]/[hypothesis] before BM25 ranking. Deterministic, no LLM."
---

Recall high-signal memory entries from the active workspace (or a named workspace) using FTS5 BM25 ranking. With `--type=<tag>` the search is pre-filtered to one of the nine schema tags, so `[decision]` queries never return `[exp]` noise.

## Usage

```bash
# Default: BM25 across all tags in the active workspace
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_query.py" "DTC client OOM"

# Type-filtered (only [decision] chunks)
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_query.py" --type decision "rate limit"

# Explicit workspace, top-20
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_query.py" --ws gowth-mem --type ref --limit 20 "FTS5 migration"

# Find all active goals
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_query.py" --type goal "active"

# Find unverified hypotheses about a topic
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_query.py" --type hypothesis "ema cross"
```

## Flags

- `--type=<tag>` — filter to one of: `decision`, `exp`, `ref`, `tool`, `reflection`, `skill-ref`, `secret-ref`, `goal`, `hypothesis`. Empty / omitted = no filter.
- `--keyword=<kw>` — (v4.0) filter to chunks whose auto-tag / frontmatter-tag `keywords` column contains `<kw>`. Substring, case-insensitive.
- `--topic=<slug>` — (v4.0) filter to a topic folder (path contains `/<slug>/`).
- `--days=<N>` — (v4.0) only chunks modified within the last N days.
- `--ws=<name>` — workspace name. Default: active workspace.
- `--query=<text>` — FTS5 query string (or pass query terms positionally).
- `--limit=<N>` — top-N hits. Default 20.

```bash
# Keyword-filtered (the v4.0 auto-tag layer) — find decisions tagged "fts5"
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_query.py" --type decision --keyword fts5 --query recall

# Topic + recency scoping
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_query.py" --topic ema-cross --days 30 --query signal
```

## Output

One line per hit, plus a compact snippet line:

```
<path>  [<tag>]  bm25=<score>  kw=<keywords>
  <truncated snippet>
```

Lines are deterministic — same query + index = same output. No LLM in the path.

## Ranking (what is actually implemented)

Non-empty queries are ranked by a **column-weighted FTS5 BM25**:
`bm25(chunks_fts, 5.0, 3.0, 1.0)` over `(tag, keywords, content)` — so tag and
keyword hits outrank plain body hits (lower BM25 = better). Empty queries return
most-recent-first (`chunks.id DESC`). `--keyword` / `--topic` / `--days` are
applied as SQL predicates before ranking. That is the whole formula — there is no
multi-signal blend in this path.

The richer 4-tier weighted context plan (layer score × recency decay × Jaccard)
lives in `_budget.py` (see `/mem-budget`), not here.

**Tags boost by default; `--keyword` is an opt-in filter.** Every auto-tag lands
in the weighted `keywords` FTS column, so a normal query already ranks tag matches
above plain body matches — you get the benefit without doing anything. Reach for
`--keyword <kw>` only when you want a hard filter for a direct lookup (return *just*
the chunks carrying that tag), not the default boosted ranking.

## Notes

- Requires the v3.4 `tag` column and the v4.0 `keywords` column on `chunks`. `_index.py` migrates both idempotently on first read; older DBs are auto-upgraded (queries fall back to `(tag, content)` weighting until the keywords column exists).
- Empty result set returns exit 0 with no output (not an error).

## Related

- `/mem-retag` — backfill the frontmatter `tags:` that feed the `--keyword` filter
- `/mem-budget` — the 4-tier weighted context planner (the real multi-signal scorer)
