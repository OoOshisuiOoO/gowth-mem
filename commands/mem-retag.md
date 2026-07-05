---
description: Backfill deterministic #tags into aspect-file frontmatter (v4.0). Extracts content keywords from every entry line and unions them into each aspect file's frontmatter tags, then reindexes. Never rewrites entry lines. Dry-run by default; --apply writes. No LLM.
---

# /mem-retag

Backfill the v4.0 deterministic tag layer over existing memory. New entries get
inline `#tags` automatically at write time; this command retro-fits the
*frontmatter* `tags:` of every dated aspect file so old memory becomes
keyword-searchable too (`/mem-recall --keyword`).

Deterministic — stdlib keyword extraction, no LLM. It reads the first line of
each `- [type]` / `## [type]` entry, extracts content keywords, and unions them
into the aspect file's frontmatter `tags:` (first-seen order, capped at
`settings.tags.max_frontmatter`, existing tags preserved). Entry lines are
**never rewritten** (that would churn historical SHA-1 dedup and git history).

## When to invoke

- Right after upgrading to v4.0 (one-time backfill of the dead `tags:` field).
- After a bulk import or migration that created aspect files without tags.
- Any time `/mem-recall --keyword` returns too few hits on older topics.

## Usage

Run the Bash tool with one of the following.

```bash
# Dry-run over ALL workspaces — prints per-file planned tags, writes nothing
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_tags.py" --backfill

# Dry-run for one workspace
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_tags.py" --backfill --ws gowth-mem

# Apply — write frontmatter tags + reindex the touched files
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_tags.py" --backfill --ws gowth-mem --apply

# Apply across every workspace
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_tags.py" --backfill --apply
```

Preview a single entry's tags without touching any file:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_tags.py" --extract '[decision] use FTS5 weighted BM25 for recall'
```

## Output

Dry-run prints one block per file that would change:

```
  workspaces/gowth-mem/ema-cross/2026-06-01-signal.md
    tags: [ema-cross, signal, atr, crossover]
retag [DRY-RUN]: 42 of 186 aspect file(s) would gain frontmatter tags  (re-run with --apply to write)
```

`--apply` writes each change through the atomic writer under a per-workspace lock,
then runs one incremental reindex so the new frontmatter tags populate the FTS5
`keywords` column.

## Notes

- Idempotent: re-running adds nothing once tags are present.
- Files with no extractable keywords are skipped (never padded).
- Aspect files without frontmatter gain a minimal `tags:` block; run
  `/mem-validate --fix` afterwards to complete the remaining required fields.

## Related

- `/mem-recall --keyword <kw>` — search the tag/keyword layer this command populates
- `/mem-validate --fix` — complete aspect frontmatter (required fields)
- `/mem-reindex` — rebuild the FTS5 index (also refreshes the keywords column)
