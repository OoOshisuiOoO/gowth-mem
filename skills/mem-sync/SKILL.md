---
name: mem-sync
description: "Sync the vault with its git remote (commit, pull --rebase, push)"
---

# mem-sync

Multi-machine sync of the vault (`$GOWTH_MEM_HOME`, default `~/.gowth-mem`): AGENTS.md + docs/* + settings.json + skills/*.

## Prerequisites

- The vault exists (run `/mem-install` if not)
- `config.json` in the vault configured with `remote` + `branch` (run `/mem-ops config`)
- Token via env `GOWTH_MEM_GIT_TOKEN` OR in `config.json` (gitignored)

## Steps

1. Run: `python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_sync.py" [--init|--pull-only|--push-only]`
2. Read the output:
   - `init: ...` lines mean first-time setup.
   - `sync: pulled origin/main` and `sync: pushed to origin/main` = success.
   - `sync: conflict — wrote SYNC-CONFLICT.md, run /mem-sync resolve` (exit 2) → resolve it (below).

## What gets synced (vs gitignored)

Synced (cross-machine):
- `AGENTS.md`
- `docs/handoff.md`, `exp.md`, `ref.md`, `tools.md`, `secrets.md` (POINTER only — never values), `files.md`
- `journal/<date>.md` — daily logs
- `docs/skills/<name>.md` — Voyager skills
- `settings.json` — plugin behavior

Every changed markdown file under `workspaces/` and `shared/` is privacy-sanitized before each commit.

Gitignored (per-machine):
- `config.json` — git remote + token
- `state.json` — SRS tracker (turn count, last_seen)
- `index.db` — FTS5/vector search index (regenerate with `memx`)

## Conflict handling

Strategy: `git pull --rebase`. Conflicts are line-level; markdown often auto-merges fine. A real conflict is
packaged into `SYNC-CONFLICT.md` at the vault root (the files are reset to the local side — no raw markers):

1. Run `/mem-sync resolve` — it walks each file with the user and applies the choices.
2. It finishes with `python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_conflict.py" --continue` (exit 0 = done,
   2 = a new conflict, 1 = see its message). Never a bare git continue: it fails on a machine with no git identity.
3. Delete `SYNC-CONFLICT.md`, then re-run `/mem-sync`.

To abort: `git -C "${GOWTH_MEM_HOME:-$HOME/.gowth-mem}" rebase --abort` and delete `SYNC-CONFLICT.md` — local
commits are preserved; the next sync replays them again.

## Hard rules

- NEVER commit `config.json` (token leak risk). Already gitignored.
- NEVER commit real secret values into `docs/secrets.md` — pointers only.
- After clone on a new machine, run `memx` (`/mem-ops reindex`) to rebuild local index.
