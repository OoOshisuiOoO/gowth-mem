---
name: mem-sync
description: Use to sync .gowth-mem/ across machines via a user-owned git remote. Auto-commits, pull-rebase, push. Conflict → SYNC-CONFLICT.md with manual resolution steps.
---

# mem-sync

Multi-machine sync of `.gowth-mem/` (AGENTS.md + docs/* + settings.json + skills/*).

## Prerequisites

- `.gowth-mem/` exists (run `/mem-init` if not)
- `.gowth-mem/config.json` configured with `remote` + `branch` (run `/mem-config`)
- Token via env `GOWTH_MEM_GIT_TOKEN` OR in `config.json` (gitignored)

## Steps

1. Run: `python3 ${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_sync.py --workspace $WS [--init|--pull-only|--push-only]`
2. Read the output:
   - `init: ...` lines mean first-time setup.
   - `sync: pulled origin/main` and `sync: pushed to origin/main` = success.
   - `sync: pull failed: CONFLICT ...` → resolve via `.gowth-mem/SYNC-CONFLICT.md`.

## What gets synced (vs gitignored)

Synced (cross-machine):
- `AGENTS.md`
- `docs/handoff.md`, `exp.md`, `ref.md`, `tools.md`, `secrets.md` (POINTER only — never values), `files.md`
- `docs/journal/<date>.md` — daily logs
- `docs/skills/<name>.md` — Voyager skills
- `settings.json` — plugin behavior

Gitignored (per-machine):
- `config.json` — git remote + token
- `state.json` — SRS tracker (turn count, last_seen)
- `index.db` — FTS5/vector search index (regenerate with `memx`)

## Conflict handling

Strategy: `git pull --rebase`. Conflicts are line-level; markdown often auto-merges fine. If conflict markers appear:

1. Read `.gowth-mem/SYNC-CONFLICT.md` for affected files.
2. Open each, decide which version to keep, remove `<<<<<<<` `=======` `>>>>>>>` markers.
3. `git -C .gowth-mem add <file>`
4. `git -C .gowth-mem rebase --continue`
5. Re-run `/mem-sync`.

To abort: `git -C .gowth-mem rebase --abort` — local changes preserved.

## Hard rules

- NEVER commit `config.json` (token leak risk). Already gitignored.
- NEVER commit real secret values into `docs/secrets.md` — pointers only.
- After clone on a new machine, run `memx` (`/mem-reindex`) to rebuild local index.
