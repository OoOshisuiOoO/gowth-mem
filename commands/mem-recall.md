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
- `--ws=<name>` — workspace name. Default: active workspace from `state.json`.
- `--limit=<N>` — top-N hits. Default 10.
- Positional arg(s) — joined as the query string.

## Output

One line per hit:

```
<rank>  bm25=<score>  tag=<tag>  <path>:<line>  <truncated snippet>
```

Lines are deterministic — same query + index = same output. No LLM in the path.

## Notes

- Requires the v3.4 `tag TEXT` column on the `chunks` table. `_index.py` migrates idempotently on first read; pre-v3.4 DBs are auto-upgraded.
- Empty result set returns exit 0 with no output (not an error).
- For raw BM25 without tag filter and across the legacy schema, use `/mem-doctor --query` instead.

## Data-quality canon

Scoring formula in `shared/research/data-quality-2026.md` §4:
`R = 0.30·BM25 + 0.30·layer_score + 0.15·recency + 0.15·diversity + 0.10·log(1+recall_count)`.
Deterministic only — no LLM in the recall path (gowth-mem hard rule).
