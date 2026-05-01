---
description: Show or switch the active gowth-mem workspace for this session
argument-hint: "[workspace-name]"
---

# /mem-workspace

Without args: show the currently active workspace.
With `<name>`: switch the active workspace for this session (writes a session-scoped override file).

## Resolution order (read-only)

The active workspace is computed at every hook invocation as:

1. Env `GOWTH_WORKSPACE=<name>`
2. Session-scoped `.session-workspace` file (set by this command)
3. `config.json.workspace_map` glob match against `$PWD`
4. `config.json.active_workspace`
5. `"default"`

## Steps

```bash
ARG="$1"

if [ -z "$ARG" ]; then
  python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_workspace.py" active
  python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_workspace.py" list
  exit 0
fi

if [ "$ARG" = "--clear" ]; then
  python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_workspace.py" clear
  exit 0
fi

# Switch
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_workspace.py" switch "$ARG"
```

## Notes

- Switching is **session-scoped**, persisted via `~/.gowth-mem/.session-workspace`. Survives across `/compact` and continues until you run `/mem-workspace --clear` or remove the file.
- To set a permanent default, edit `~/.gowth-mem/config.json` `active_workspace` field instead.
- To map a directory glob to a workspace (e.g. `cd /Volumes/Data/Git/bot/AI-trade` → `trade`), use `/mem-workspace-map`.
