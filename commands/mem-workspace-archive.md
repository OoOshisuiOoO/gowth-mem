---
description: Archive a gowth-mem workspace (move to workspaces/_archive/<name>-<date>/)
argument-hint: "<name>"
---

# /mem-workspace-archive

Stop using a workspace without deleting it. Moves `workspaces/<name>/` to `workspaces/_archive/<name>-<today>/` and rebuilds the registry MOC.

## Steps

```bash
NAME="$1"
if [ -z "$NAME" ]; then
  echo "Usage: /mem-workspace-archive <name>" >&2
  exit 1
fi

ACTIVE=$(python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_workspace.py" active)
if [ "$NAME" = "$ACTIVE" ]; then
  echo "Cannot archive the currently active workspace ($ACTIVE). Switch first via /mem-workspace <other>." >&2
  exit 1
fi

python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_workspace.py" archive "$NAME"
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_moc.py" --all
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_index.py" --full
```

## Recovery

To restore: move the archive folder back manually:

```bash
mv ~/.gowth-mem/workspaces/_archive/<name>-<date> ~/.gowth-mem/workspaces/<name>
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_moc.py" --all
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_index.py" --full
```
