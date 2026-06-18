---
description: Run the full 3-phase dreaming consolidation pipeline (Light → REM → Deep) on one or all workspaces. Deduplicates episodic entries, clusters by keyword theme, and ranks files by 6 weighted signals. Manual command — does not auto-trigger.
---

# /mem-dream

Run the full dreaming consolidation pipeline on your gowth-mem workspace.

Modeled on sleep-dependent memory consolidation (SWS + REM cycles from neuroscience
research in `.claude/research/v3.4-brain-memory.md` §J). Like biological sleep, this
should be run after a long session or periodically — not on every prompt.

## What it does

**Light phase (SWS — slow-wave sleep analogy):** Scans `state.json` activity records
and gathers candidate files that have been recalled at least twice. This is the "replay"
step: files touched frequently across multiple sessions are surfaced as consolidation
targets. Files with fewer than 2 recall events are skipped — they haven't proved
themselves worth consolidating yet.

**REM phase (REM sleep analogy):** Groups candidate files by keyword theme using
Jaccard similarity clustering (threshold 0.3). Files that share significant vocabulary
get placed in the same theme cluster. This mirrors the associative, cross-domain linking
that REM sleep is thought to produce — disparate fragments connected by shared concepts.
Output: a map of theme labels to file lists.

**Deep phase (deep consolidation):** Ranks every candidate by 6 weighted signals —
frequency (0.24), relevance (0.30), diversity (0.15), recency (0.15), consolidation
(0.10), richness (0.06). Files scoring ≥ 0.6 are flagged for **promote** (key entries
should be lifted to workspace docs); 0.3–0.6 are **maintain**; below 0.3 are
**prune_candidates**. The deep phase does not delete anything — it surfaces candidates
for `/mem-prune` to act on.

## When to invoke

- After a long working session (1+ hours) before running `/compact`
- Weekly maintenance pass alongside `/mem-prune` and `/mem-lint`
- Before switching workspaces — dream the current one first to consolidate state
- When `/mem-recall` is returning noisy or redundant results
- Any time you notice topic files have drifted out of sync with `docs/ref.md`

## Usage

Run the Bash tool with one of the following:

```bash
# All workspaces (omit --ws)
python3 hooks/scripts/_dream.py

# Single workspace
python3 hooks/scripts/_dream.py --ws <workspace>

# Dry run — report what would happen, write nothing
python3 hooks/scripts/_dream.py --ws <workspace> --dry-run

# Skip individual phases
python3 hooks/scripts/_dream.py --ws <workspace> --no-light
python3 hooks/scripts/_dream.py --ws <workspace> --no-rem
python3 hooks/scripts/_dream.py --ws <workspace> --no-deep
```

Output is JSON on stdout. Progress lines go to stderr.

Example output:

```json
{
  "workspace": "my-project",
  "phases": {
    "light": { "skipped": false, "files_processed": 12, "duplicates_collapsed": 0, "duration_s": 0.003 },
    "rem":   { "skipped": false, "themes_found": 4, "files_processed": 12, "duration_s": 0.012 },
    "deep":  { "skipped": false, "promoted": 3, "maintained": 6, "prune_candidates": 3, "duration_s": 0.001 }
  },
  "summary": "Dream run on workspace 'my-project'. Light phase: 12 candidate files gathered. REM phase: 4 keyword themes across 12 files. Deep phase: 3 files promoted, 6 maintained, 3 flagged for pruning.",
  "dry_run": false
}
```

After reviewing the output, use `/mem-prune` to delete entries in `prune_candidates`
files, and manually promote key entries from `promote` files into `docs/ref.md` or
`docs/handoff.md`.

## Implementation

Orchestrated by `hooks/scripts/_dream.py`. That module imports `light_phase`,
`rem_phase`, and `deep_phase` from `hooks/scripts/_consolidate.py` (the v2.9
staged consolidation pipeline) and wraps each in try/except with
`time.perf_counter()` timing. A per-workspace `fcntl` lock (`_lock.py`) prevents
two concurrent dream runs on the same workspace. Progress is emitted to stderr so
stdout remains parseable JSON.

The orchestrator does not modify any files when `dry_run=True`. Even in live mode,
the deep phase only produces a ranking report — no files are written by the dream
pipeline itself. Actual pruning requires a separate `/mem-prune` invocation.

## Related

- `/mem-distill` — single-pass distillation of recent journal entries into curated docs (scoped to today/yesterday; lower-level than `/mem-dream`)
- `/mem-lint` — detect schema violations and contradictions in topic files
- `/mem-prune` — delete expired, superseded, and duplicate entries; acts on the `prune_candidates` list that `/mem-dream` surfaces

## Data-quality canon

All three phases honour `shared/research/data-quality-2026.md`:
- §4 multi-signal recall score = the 6 deep-phase weights (frequency / relevance / diversity / recency / consolidation / richness)
- §6 consolidation triggers — when REM/Deep should fire vs skip
- §8 anti-bloat invariant — Letta defragmentation target 15–25 focused files/topic; Deep flags overflow as prune_candidates
