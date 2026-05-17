---
description: Heuristic contradiction lint across [ref]/[decision]/[tool] entries — flags polarity mismatches (enabled vs disabled, true vs false, etc.) sharing >=3 keywords. No LLM. Outputs candidate pairs; never auto-mutates files.
---

Scan the active workspace for candidate contradictions across `[ref]`, `[decision]`, and `[tool]` lines.

Run with the Bash tool:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_contradict.py" "$@"
```

## What it does

For every pair of lines that share >= `--min-overlap` content keywords AND differ on a polarity marker:
- `enabled` vs `disabled`
- `true` vs `false`
- `added` vs `removed`
- `allowed` vs `forbidden`
- `recommended` vs `deprecated`
- `supported` vs `unsupported`
- `works` vs `broken`
- etc.

…it emits a warning. Also detects negation-aware opposites (`X is enabled` vs `X is not enabled`).

## Output

```
[contradict] 2 candidate pair(s):
  #1  shared=fts5,index,sqlite
    A  workspaces/ws/topic/2026-05-15-fts.md:12  fts5 enabled in index ...
    B  workspaces/ws/topic/2026-05-17-fts.md:5   fts5 disabled in index ...
       polarity_a=['enabled']  polarity_b=['disabled']
```

Pass `--json` for machine-readable output. Pass `--min-overlap 2` to widen the net (noisier).

## What to do with hits

A human (or you, the AI) decides:
- Delete the older entry
- Add `[contradicts: <other>]` cross-link to both
- Mark one with `valid_until: <date>`

This command is intentionally read-only.

## When to run

- Before `/mem-distill` (catches conflicts before promotion)
- Weekly hygiene pass
- After bulk imports / migrations
