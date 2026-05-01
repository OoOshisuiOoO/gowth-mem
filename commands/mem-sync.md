---
description: Manual sync of ~/.gowth-mem/ with the configured git remote. Auto-commits local changes, pulls with rebase, pushes. On conflict, writes ~/.gowth-mem/SYNC-CONFLICT.md so /mem-sync-resolve can walk the user through resolution.
---

Sync `~/.gowth-mem/` with the configured git remote.

Run with the Bash tool:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_sync.py" "$@"
```

(In v2.0, sync also runs **automatically** after every `/compact` via the PostCompact hook. Manual `/mem-sync` is for explicit on-demand cycles.)

## Pre-requisite

`~/.gowth-mem/config.json` must contain `remote` + `branch` (use `/mem-install` or `/mem-config`).

Token: env var `GOWTH_MEM_GIT_TOKEN` preferred. Fallback: `config.json["token"]` (plaintext-on-disk; gitignored).

## What gets synced

| Path | Synced? | Reason |
|---|---|---|
| `AGENTS.md` | ✅ | shared operating rules |
| `settings.json` | ✅ | plugin behavior |
| `topics/**` | ✅ | topic-organized knowledge |
| `docs/{handoff,secrets,tools}.md` | ✅ | cross-topic registries |
| `journal/**` | ✅ | raw daily logs (small, append-only) |
| `skills/**` | ✅ | Voyager workflows |
| `config.json` | ❌ gitignored | token + per-machine remote |
| `state.json` | ❌ gitignored | per-machine SRS tracker |
| `index.db` | ❌ gitignored | per-machine FTS5/vector — rebuild via `memx` |
| `.locks/` | ❌ gitignored | runtime flocks |
| `SYNC-CONFLICT.md` | ❌ gitignored | conflict report |

## Flags

- `--init` — initialize `.git/` if missing, set remote, allow unrelated histories on first pull, push.
- `--pull-only` — fetch + rebase, no push.
- `--push-only` — commit + push, no pull.
- (no flag) — full cycle under `file_lock("sync")`: commit local → pull rebase → push.

## Multi-session safety

All sync operations acquire `~/.gowth-mem/.locks/sync.lock` first via `fcntl.flock`. Parallel Claude sessions queue rather than racing on git operations. The hook variant (`auto-sync.py`) skips silently if the lock is held; the CLI variant (`_sync.py`) waits up to 30s.

## Conflict resolution

If `git pull --rebase` hits a conflict, `_sync.py` invokes `_conflict.py` which:

1. Writes a structured `~/.gowth-mem/SYNC-CONFLICT.md` (local + remote + ancestor versions per file).
2. Resets the working copy to the local side so files stay parseable (no raw `<<<<<<<` markers in topics).
3. Exits with code 2.

Then run `/mem-sync-resolve` (shortcut: `memC`). The skill walks each file with you, applies your choice, and finishes the rebase + push under lock.

## After sync on a fresh machine

Index is per-machine; rebuild it:

```
memx        (or /mem-reindex)
```

## Token security

- Best: `export GOWTH_MEM_GIT_TOKEN=ghp_xxxx` in shell rc.
- OK: `config.json["token"]` (gitignored, plaintext on disk; use a fine-scoped GitHub PAT).
- Never: commit token into a tracked file or paste it into a topic file.
