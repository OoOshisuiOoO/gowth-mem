---
description: "Sync the vault with its git remote; /mem-sync resolve handles conflicts"
---

Sync `~/.gowth-mem/` with the configured git remote.

Run with the Bash tool:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_sync.py" "$@"
```

(In v2.0, sync also runs **automatically** after every `/compact` via the PostCompact hook. Manual `/mem-sync` is for explicit on-demand cycles.)

## Pre-requisite

`~/.gowth-mem/config.json` must contain `remote` + `branch` (use `/mem-install` or `/mem-ops config`).

Token: env var `GOWTH_MEM_GIT_TOKEN` preferred. Fallback: `config.json["token"]` (plaintext-on-disk; gitignored).

## What gets synced

| Path | Synced? | Reason |
|---|---|---|
| `shared/AGENTS.md` | ✅ | shared operating rules |
| `settings.json` (includes `layout_version: 3`) | ✅ | plugin behavior |
| `shared/{secrets,tools,files}.md` | ✅ | cross-workspace registries (pointers only) |
| `shared/skills/**` | ✅ | cross-workspace skill library |
| `workspaces/<ws>/AGENTS.md` | ✅ | per-workspace delta rules |
| `workspaces/<ws>/_MAP.md` | ✅ | workspace MOC (auto via `_moc.py`) |
| `workspaces/<ws>/<slug>/00-README.md` | ✅ | topic MOC (auto via `_moc.py rebuild_topic_readme`) |
| `workspaces/<ws>/<slug>/YYYY-MM-DD-<aspect>.md` | ✅ | dated aspect content (v3) |
| `workspaces/<ws>/<slug>/lessons.md` | ✅ | per-topic 5-field ledger |
| `workspaces/<ws>/docs/{handoff,exp,ref,tools,files}.md` | ✅ | workspace cross-topic registries |
| `workspaces/<ws>/journal/<date>.md` | ✅ | raw daily logs (small, append-only) |
| `workspaces/<ws>/skills/**` | ✅ | workspace-specific Voyager workflows |
| `workspaces/<ws>/research/**` | ✅ | long-form research output (v3) |
| `shared/backup-v3/v2-pre-v3-*/` | ✅ | v3 migration backup (rolling-2 window) |
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

Then run `/mem-sync resolve` (shortcut: `memC`). The skill walks each file with you, applies your choice, and finishes the rebase + push under lock.

## After sync on a fresh machine

Index is per-machine; rebuild it:

```
memx        (or /mem-ops reindex)
```

## Token security

- Best: `export GOWTH_MEM_GIT_TOKEN=ghp_xxxx` in shell rc.
- OK: `config.json["token"]` (gitignored, plaintext on disk; use a fine-scoped GitHub PAT).
- Never: commit token into a tracked file or paste it into a topic file.

## resolve — `/mem-sync resolve`

When `~/.gowth-mem/SYNC-CONFLICT.md` exists, read `${CLAUDE_PLUGIN_ROOT}/templates/ops/sync-resolve.md`
and follow it: walk each conflicted file, apply the user's choice (keep-local / keep-remote /
merge / manual), `git rebase --continue`, then push. Conflicts on `workspaces/<ws>/memory/MEMORY.md`
merge automatically since v4.8 (both free zones kept, block regenerated) and never appear there. The ops file's bash blocks are written against the plugin root as a dollar-brace shell expansion of CLAUDE_PLUGIN_ROOT. Claude Code substitutes that only in THIS command, never inside the ops file, and your shell does not export it — so start every Bash call that runs one of its commands with `export CLAUDE_PLUGIN_ROOT='${CLAUDE_PLUGIN_ROOT}'; ` on the same line — a bare `VAR=… cmd` prefix does NOT apply to expansions in that same command — or write that literal path in place of the expansion.
