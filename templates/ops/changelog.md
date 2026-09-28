---
description: "Themed memory changelog (learned from supremor). Rolls up the memory repo's recent descriptive commits into a digest grouped by workspace and change type (add/update/prune/archive) with topics + entry deltas. Deterministic — reads the v3.6 commit trailers, no LLM. A higher tier than per-commit messages for 'what changed in my memory lately'."
---

Show a themed digest of recent memory changes — grouped by workspace and change type, not a flat commit list. Works because v3.6 descriptive auto-commits carry structured `Workspace:`/`Topics:`/`Entries:` trailers, so the rollup is deterministic (supremor does the equivalent via a daily LLM job; gowth-mem doesn't need one).

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_changelog.py" --days 7
```

`--days N` sets the window (default 7). `--json` for machine output.

## What it shows

- Total commits in the window + a breakdown by type (`add/update/prune/archive/consolidate/sync`).
- Per workspace: commit count by type, topics touched, and net entry deltas (`+12 decision +9 ref …`).

Use it for a weekly "what did my memory learn / forget this week" review, or before `/compact` to see what's accumulated. Commits made before v3.6 (no trailers) show as `other` — going forward every auto-sync commit is structured.

Background: `.claude/research/v3.7-supremor-comparison.md` §3 (themed changelog learning).
