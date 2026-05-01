---
description: Sync .gowth-mem/ (AGENTS.md + docs/* + settings.json + skills/*) with a user-owned git remote. Auto-commits local changes, pulls with rebase, pushes. On conflict, writes SYNC-CONFLICT.md and exits with instructions.
---

Sync the `.gowth-mem/` directory with the configured git remote.

Run with the Bash tool:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_sync.py" --workspace "${CLAUDE_PROJECT_DIR:-$PWD}" "$@"
```

## Pre-requisite

Run `/mem-config` first to set up `.gowth-mem/config.json` with the remote URL + branch. Token is optional (recommended via env `GOWTH_MEM_GIT_TOKEN`).

## What gets synced

| Path | Synced? | Reason |
|---|---|---|
| `.gowth-mem/AGENTS.md` | ✅ | shared across machines |
| `.gowth-mem/docs/*` | ✅ | the actual knowledge |
| `.gowth-mem/settings.json` | ✅ | plugin behavior |
| `.gowth-mem/config.json` | ❌ gitignored | contains token |
| `.gowth-mem/state.json` | ❌ gitignored | per-machine SRS tracker |
| `.gowth-mem/index.db` | ❌ gitignored | per-machine FTS5 index — regenerate via `memx` |

## Flags

- `--init` — initialize `.git/` if missing, set remote, do initial pull/push.
- `--pull-only` — fetch + rebase, no push.
- `--push-only` — commit + push, no pull.
- (no flag) — full cycle: commit local → pull rebase → push.

## Conflict resolution

If `git pull --rebase` hits a conflict:

1. Script writes `.gowth-mem/SYNC-CONFLICT.md` listing affected files.
2. Open each file, resolve `<<<<<<<` markers manually.
3. `git -C .gowth-mem add <file>`
4. `git -C .gowth-mem rebase --continue`
5. Re-run `/mem-sync` (shortcut: `memy`).

To abort the rebase entirely: `git -C .gowth-mem rebase --abort`.

## After sync on a fresh machine

Index is per-machine; rebuild it:

```
memx        (or /mem-reindex)
```

## Token security

- Prefer `export GOWTH_MEM_GIT_TOKEN=ghp_xxxx` in your shell env.
- If you put `token` in `config.json`, it stays local (gitignored) — but be aware anyone with disk access reads it plaintext. Use a fine-scoped GitHub PAT (`repo` scope only).
