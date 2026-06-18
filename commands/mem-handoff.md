---
description: Rotate docs/handoff.md — keep the most recent N dated snapshots live, move older ones to docs/handoff-archive.md. handoff.md is loaded at EVERY bootstrap, so this caps its per-session token cost. Structural sections are kept; nothing is deleted (archive + git history).
---

Rotate `docs/handoff.md` so it stays small. handoff.md is read at **every** SessionStart, so accumulated dated snapshots (`## host:… 2026-06-14`, etc.) become a per-session token tax. The canon caps an always-loaded file at ~200 lines.

Run with the Bash tool:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_handoff.py" --all --keep 10 "$@"
```

`--keep N` sets how many of the most-recent dated snapshots stay live (default 10). `--ws X` for one workspace. `--dry-run` to preview.

## What it does

- KEEPS: the preamble + every **non-dated** (structural) H2 section (e.g. `## Entries`) + the `keep` most-recent **dated** snapshots.
- MOVES: older dated snapshots → `docs/handoff-archive.md` (tracked + searchable, but NOT loaded at SessionStart).
- Nothing is deleted — fully reversible (the archive file + memory-repo git history). Data-safety verified: every snapshot survives in `handoff.md` ∪ `handoff-archive.md`.

## When to run

- Weekly, or whenever `/mem-cost` shows handoff is large.
- After a burst of session handoffs (the snapshots pile up fast).

Canon: `shared/research/extraction-reuse-2026.md` §1 (working-memory tier stays small).
