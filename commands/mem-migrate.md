---
description: (Legacy) v0.9 → v1.0 per-workspace migration. For v1.0 per-workspace → v2.0 global, use /mem-migrate-global.
---

This command migrated the **v0.9** layout (workspace-rooted `AGENTS.md` + `docs/`) into **v1.0** centralized `<workspace>/.gowth-mem/`.

In v2.0, memory is global at `~/.gowth-mem/` instead of per-workspace. Most users never need this command.

**If you have v1.0 per-workspace `<ws>/.gowth-mem/` directories**, run `/mem-migrate-global` instead — it walks them all and merges content into `~/.gowth-mem/topics/` by topic.

## v0.9 → v1.0 (legacy, kept for completeness)

Run with the Bash tool:

```bash
WS="${CLAUDE_PROJECT_DIR:-$PWD}"
GM="$WS/.gowth-mem"

mkdir -p "$GM/docs/journal" "$GM/docs/skills"

if [ -f "$WS/AGENTS.md" ] && [ ! -f "$GM/AGENTS.md" ]; then
  mv "$WS/AGENTS.md" "$GM/AGENTS.md"
fi

if [ -d "$WS/docs" ] && [ ! -f "$GM/docs/handoff.md" ]; then
  for f in handoff exp ref tools secrets files; do
    [ -f "$WS/docs/$f.md" ] && mv "$WS/docs/$f.md" "$GM/docs/$f.md"
  done
  if [ -d "$WS/docs/journal" ] && [ -n "$(ls -A "$WS/docs/journal" 2>/dev/null)" ]; then
    mv "$WS/docs/journal/"* "$GM/docs/journal/" 2>/dev/null
    rmdir "$WS/docs/journal" 2>/dev/null
  fi
  if [ -d "$WS/docs/skills" ] && [ -n "$(ls -A "$WS/docs/skills" 2>/dev/null)" ]; then
    mv "$WS/docs/skills/"* "$GM/docs/skills/" 2>/dev/null
    rmdir "$WS/docs/skills" 2>/dev/null
  fi
  rmdir "$WS/docs" 2>/dev/null
fi

echo "v0.9 → v1.0 migration complete. To go further (v1.0 → v2.0 global), run /mem-migrate-global."
```

## Recommended flow

```
v0.9 (root)        →  v1.0 (per-workspace)  →  v2.0 (global)
AGENTS.md             <ws>/.gowth-mem/         ~/.gowth-mem/
docs/*                                         topics/<slug>.md
                                               docs/{handoff,secrets,tools}.md

/mem-migrate          /mem-migrate-global
```
