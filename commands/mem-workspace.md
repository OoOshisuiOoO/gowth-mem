---
description: Workspace management — list, create, archive, map
argument-hint: "[<verb> [args]]"
---

# /mem-workspace

Unified workspace commands. No args shows active workspace and lists all workspaces.

## Subcommands

| Command | Purpose |
|---------|---------|
| `/mem-workspace` | Show active workspace (default) |
| `/mem-workspace <name>` | Switch active workspace for this session |
| `/mem-workspace --clear` | Clear session override and use config default |
| `/mem-workspace create <name>` | Scaffold new workspace from v3 skeleton |
| `/mem-workspace archive <name>` | Archive a workspace (move to `_archive/`) |
| `/mem-workspace list` | List all active workspaces (default, same as no args) |
| `/mem-workspace map <glob> <name>` | Map cwd glob to workspace (auto-detect on SessionStart) |
| `/mem-workspace map --remove <glob>` | Remove a workspace mapping |

## Resolution order (read-only)

When determining the active workspace at session start:

1. Env `GOWTH_WORKSPACE=<name>`
2. Session-scoped `.session-workspace` file (set by `/mem-workspace <name>`)
3. `config.json.workspace_map` glob match against `$PWD`
4. `config.json.active_workspace`
5. `"default"`

## Notes

- Switching is **session-scoped**, persisted via `~/.gowth-mem/.session-workspace`. Survives `/compact` until cleared via `--clear`.
- To set a permanent default, edit `~/.gowth-mem/config.json` `active_workspace` field.
- For detailed subcommand documentation, see `/mem-workspace-create`, `/mem-workspace-archive`, `/mem-workspace-list`, `/mem-workspace-map`.
