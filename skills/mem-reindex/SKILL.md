---
name: mem-reindex
description: Use to (re)build the gowth-mem SQLite search index over docs/** and wiki/**. Enables BM25 + (optional) vector hybrid recall. Run after bulk imports or when recall feels stale.
---

# mem-reindex

Build or refresh the search index used by recall-active.py.

## Pre-requisites

- `python3` (any 3.11+ recommended for FTS5)
- For vector hybrid (optional): `pip install sqlite-vec` + one of:
  - `OPENAI_API_KEY` — OpenAI text-embedding-3-small (default 512d cut)
  - `VOYAGE_API_KEY` — voyage-multilingual-2 (best for Vietnamese)
  - `GEMINI_API_KEY` / `GOOGLE_API_KEY` — gemini-embedding-001

## Steps

1. Run `python3 ${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_index.py` from the workspace root.
   - Add `--full` to rebuild from scratch (rare; only if the index is corrupted).
2. The script reports: number of files / chunks indexed, vector embeddings made (if any), and which fallback (if any) was used.
3. Add `.gowth-mem/` to the workspace `.gitignore` if not already.
4. Confirm the index works by running a recall: a quick `/mem-bootstrap` or any user prompt should now show snippets via the index.

## Verification

```bash
sqlite3 .gowth-mem/index.db "SELECT COUNT(*) FROM chunks; SELECT COUNT(*) FROM chunks_vec;"
```

(`chunks_vec` count = 0 if vector indexing was skipped; that's normal in FTS5-only mode.)

## When to NOT use

- Vault is tiny (<10 markdown files) — grep is fast enough.
- No write permission on workspace.
- Want zero deps and zero API costs — keep using v0.5 grep path (it still works).

## Hard rules

- Never embed `docs/secrets.md` if the file accidentally contains real values — the script does index it, but secrets in your file = secrets in your DB. Keep `docs/secrets.md` to env-var pointers only.
- Index file `.gowth-mem/index.db` should be gitignored (already documented).
