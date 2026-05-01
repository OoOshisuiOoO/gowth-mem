---
description: List all gowth-mem workspaces with topic count and last-touched date
---

# /mem-workspace-list

Print every workspace under `~/.gowth-mem/workspaces/` with metadata.

## Steps

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_workspace.py" list
```

## Columns

```
<name>     <topic_count> topics  last=YYYY-MM-DD  <title>
```

Workspaces under `_archive/` are not shown — view them under `~/.gowth-mem/workspaces/_archive/` directly.
