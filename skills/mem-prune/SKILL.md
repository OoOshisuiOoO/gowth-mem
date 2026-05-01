---
name: mem-prune
description: Use periodically (weekly, after distill, before /compact) or whenever knowledge feels stale. Actively DELETES outdated, superseded, deprecated, and duplicate entries from docs/*.md. Skips docs/journal (permanent log). Per-line surgery, no rewrite.
---

# mem-prune

Active deletion pass over `docs/*.md`.

## Inputs

- Workspace root (`$CLAUDE_PROJECT_DIR` or `$PWD`)
- Optional `--dry-run` flag

## Pruning rules (applied in order, line-level)

| Rule | Action |
|---|---|
| Entry contains `valid_until: YYYY-MM-DD` where date < today | DELETE |
| Entry contains `(superseded)`, `(deprecated)`, or `(obsolete)` (case-insensitive) | DELETE |
| Within same file, two entries with Jaccard word-overlap ≥ 0.85 | DELETE shorter (keep longer/richer) |

An "entry" = one line starting with `- [type]` (or `* [type]`) plus any indented continuation lines underneath.

## Steps

1. Run: `python3 ${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_prune.py --workspace $WS`
2. Read the report:
   ```
   prune: deleted N entries across M files. K entries kept.
     docs/exp.md: -3 entries (now 12)
     docs/ref.md: -1 entries (now 8)
   ```
3. If unexpected entries were deleted, restore via `git checkout -- docs/<file>` and adjust the source markers.

## Scope

- Files included: `docs/**/*.md`
- Files excluded: `docs/journal/**` (raw log, never prune)
- Other markdown (wiki/, AGENTS.md, README.md): not touched (out of scope for this plugin)

## When NOT to run

- Mid-session before you've saved current decisions to docs/exp.md
- Without recent git commit (no rollback path if prune over-deletes)

## Hard rules

- DO NOT prune `docs/journal/`. Journal is the immutable raw log.
- DO NOT prune entries that lack any of the trigger markers (no false-positive rule).
- Always commit before pruning so `git diff` shows what was removed.

## Why this is strict

User direction: outdated knowledge = noise; bootstrap layer should stay lean. Audit trail lives in `git log`.
