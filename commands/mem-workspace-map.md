---
description: Add or remove a cwd-glob → workspace mapping in config.json
argument-hint: "<glob> <workspace-name>   |   --remove <glob>"
---

# /mem-workspace-map

Persist a directory-glob → workspace mapping so SessionStart auto-detects the workspace based on `$PWD`.

Stored under `~/.gowth-mem/config.json` (per-machine, gitignored).

## Steps

```bash
if [ "$1" = "--remove" ]; then
  python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_workspace.py" unmap "$2"
  exit 0
fi

GLOB="$1"
WS="$2"

if [ -z "$GLOB" ] || [ -z "$WS" ]; then
  echo "Usage: /mem-workspace-map <glob> <workspace-name>" >&2
  echo "       /mem-workspace-map --remove <glob>" >&2
  exit 1
fi

python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_workspace.py" map "$GLOB" "$WS"
```

## Examples

```
/mem-workspace-map "/Volumes/Data/Git/fg/trueprofit/**" devops
/mem-workspace-map "/Volumes/Data/Git/bot/AI-trade/**" trade
/mem-workspace-map --remove "/Volumes/Data/Git/old-project/**"
```

The next time `cd` lands inside one of these globs, the SessionStart hook will resolve `active_workspace = <name>` automatically — no need for `GOWTH_WORKSPACE=` env var or manual `/mem-workspace` switch.
