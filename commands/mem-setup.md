---
description: "Backup/restore machine setup; /mem-setup native wires auto-memory to the vault"
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

## `/mem-setup native` — load memory through Claude Code's auto memory (v4.8)

Claude Code attaches `MEMORY.md` from the project's `autoMemoryDirectory` at session start,
on resume and after every compaction (200 lines / 25 KB), and — unlike hook output — never
truncates it to a preview. gowth-mem keeps the vault's working set there.

1. Dry-run: list the projects (every `config.json` `workspace_map` glob present on this
   machine, plus the current one) and what would be written:
   ```bash
   python3 "$CLAUDE_PLUGIN_ROOT/hooks/scripts/_native.py" wire --dry-run
   ```
2. Show the list to the user and wait for an explicit go-ahead — this writes
   `<project>/.claude/settings.local.json` (merged; a different existing value is reported as
   `conflict` and needs `--force`; a git-tracked file is never touched).
   ```bash
   python3 "$CLAUDE_PLUGIN_ROOT/hooks/scripts/_native.py" wire
   ```
3. Optional import of the machine-local auto memory Claude Code wrote before wiring
   (`~/.claude/projects/<slug>/memory/*.md` → `<ws>/memory/`, privacy-sanitized; a name clash
   keeps the vault copy and stores the incoming file as `<name>.from-<host>.md`):
   ```bash
   python3 "$CLAUDE_PLUGIN_ROOT/hooks/scripts/_native.py" import          # dry-run
   python3 "$CLAUDE_PLUGIN_ROOT/hooks/scripts/_native.py" import --apply  # after the user agrees
   ```
4. New sessions in the wired projects load the block; verify with `/mem-cost` (`mode=native`).
