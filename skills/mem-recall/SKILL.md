---
name: mem-recall
description: "Search curated memory when asked what was decided or learned before"
---

# mem-recall — read the vault before answering from memory

When the user asks what was decided, tried, or learned earlier — or you need a fact the vault
likely holds — run the deterministic BM25 recall and READ the file behind the best hit before
answering. Automatic per-prompt recall (v4.8) already injects up to 3 related entries; use this
for deeper or filtered searches.

```bash
WS=$(python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_workspace.py" active)
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_query.py" --ws "$WS" --limit 8 "<query terms>"
```

Filters: `--type decision|exp|ref|tool|reflection|skill-ref|secret-ref|goal|hypothesis`,
`--keyword <kw>`, `--topic <slug>`, `--days N`, `--include-research` (workspace research
scratch and handoff-archive are excluded by default), `--archive` (forgotten material).
Full reference: `/mem-recall`.
