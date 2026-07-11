---
description: Backup/restore the machine's Claude Code setup (plugins, marketplaces, global MCP servers, personal skills, settings.json, global CLAUDE.md) into the synced vault at shared/setup/. New machine = clone vault → bash restore.sh → paste one /plugin block. All secret values redacted to <env:NAME> pointers — the vault never stores real secrets.
---

Backup this machine's Claude Code setup into `~/.gowth-mem/shared/setup/` (synced via the vault's git remote), or show the current backup status.

Run with the Bash tool:

```bash
# backup (default action for /mem-setup)
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_setup.py" --backup

# preview without writing
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_setup.py" --backup --dry-run

# what is backed up, from which machine, when
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_setup.py" --status
```

What gets captured:

| File | Content |
|---|---|
| `plugins.json` | marketplaces (name → git URL) + installed plugins with versions |
| `mcp.global.json` | global MCP servers, env values redacted to `<env:NAME>` pointers |
| `settings.json` | `~/.claude/settings.json` (sanitized) |
| `CLAUDE.global.md` | `~/.claude/CLAUDE.md` (sanitized) |
| `keybindings.json` | if present |
| `skills/` | `~/.claude/skills/` tree, text files sanitized |
| `RESTORE.md` | restore steps + one-paste `/plugin` block + required env vars |
| `restore.sh` | copies files back + merges MCP servers into `~/.claude.json` |

**New machine flow** (report this to the user after backup):

1. Install gowth-mem + `/mem-install` (or clone the vault repo).
2. `bash ~/.gowth-mem/shared/setup/restore.sh`
3. Paste the `/plugin` block from `RESTORE.md` into Claude Code, restart.
4. Export the env vars listed in `RESTORE.md` §3 (values live in your secret store, never in the vault).

After a backup, run `/mem-sync` (or let auto-sync push) so the new machine can pull it.
