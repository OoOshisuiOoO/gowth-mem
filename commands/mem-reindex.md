---
description: Build or refresh the SQLite index over workspaces/**. Primarily powers `[[wikilink]]` slug resolution inside topic files. (The on-prompt recall hook was removed in v3.2.)
---

Build / refresh the gowth-mem search index.

Run with the Bash tool:

```bash
cd "${CLAUDE_PROJECT_DIR:-$PWD}" && python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_index.py" "$@"
```

By default the script does **incremental** indexing — only re-indexes files whose mtime changed. Pass `--full` to drop and rebuild everything.

## What gets enabled

| Layer | Requires | Effect |
|---|---|---|
| **FTS5 (BM25)** | nothing — ships with stdlib `sqlite3` (Python 3.11+) | Fast keyword search over chunked content; better than grep for large vaults |
| **Vector hybrid** | `pip install sqlite-vec` + `OPENAI_API_KEY` (or `VOYAGE_API_KEY` / `GEMINI_API_KEY`) | Semantic retrieval — paraphrased queries find relevant chunks even with no keyword overlap |

Both layers are **opt-in**. If `sqlite-vec` is not installed or no embedding key is set, the script proceeds with FTS5 only. If FTS5 itself is unavailable, the script errors out and recall continues to use the v0.5 grep path.

## When to run

- After `/mem-install` (first time setup).
- After bulk-importing docs (e.g. ingesting external notes).
- Periodically (e.g. weekly) if you've added many entries.
- On-demand whenever recall feels stale.

The index is consumed by `_wikilink.resolve()` for `[[slug]]` resolution. The on-prompt recall hook was removed in v3.2; FTS5 / vector tables are still maintained for future opt-in retrieval skills but nothing reads them today.

## Cost

- FTS5: zero per-query cost.
- Vector embeddings: ~$0.000002 per chunk on OpenAI text-embedding-3-small. A 100-chunk vault costs ~$0.0002 to index, plus ~$0.000003 per query.

Add `.gowth-mem/` to your workspace `.gitignore`:

```bash
echo ".gowth-mem/" >> .gitignore
```
