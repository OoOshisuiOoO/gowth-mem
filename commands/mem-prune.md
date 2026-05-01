---
description: Actively DELETE outdated, superseded, deprecated, or duplicate entries from docs/*.md (skips docs/journal). Per-line surgery, not just marking. Use after /mem-distill or weekly to keep working memory lean.
---

Run an active prune over docs/*.md. Removes entries that match strict outdated criteria.

Run with the Bash tool:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_prune.py" --workspace "${CLAUDE_PROJECT_DIR:-$PWD}" "$@"
```

Pass `--dry-run` to preview without writing.

## Pruning rules (in order)

1. `valid_until: YYYY-MM-DD` past today → DELETE
2. `(superseded)` / `(deprecated)` / `(obsolete)` markers → DELETE
3. Within-file near-duplicate (Jaccard ≥ 0.85) → keep the LONGER entry, DELETE the rest

## Scope

- Operates on `docs/**/*.md`
- **Skips `docs/journal/**`** — raw journal is a permanent log; never auto-prunes
- Treats an "entry" as a line starting with `- [type]` or `* [type]` plus its indented continuation lines

## When to run

- After `/mem-distill` (cleans up newly-promoted clutter)
- Weekly (catches accumulated stale entries)
- Before `/compact` (reduces bootstrap token cost)
- After explicit version bump (e.g. moved to claude-opus-4-7) — flag old `version: claude-3.5` entries with `(superseded)` first, then run prune

## Why this differs from mempalace

mempalace's `invalidate()` keeps rows and just sets `valid_to`. Per user direction, gowth-mem **actively deletes** outdated content because:
- Audit trail isn't needed for working memory
- Stale text wastes bootstrap tokens on every session
- Disk archival can be done via git history if needed

To preserve audit trail, commit `docs/*` to git and let `git log` serve as the timeline.
