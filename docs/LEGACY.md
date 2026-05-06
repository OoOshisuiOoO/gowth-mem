# Legacy Commands & Migrations

This file archives commands that were removed from the active surface but are kept for reference. The migration scripts may still be useful if you find a stale workspace at an older layout.

> **Active migration command**: `/mem-migrate-global` — for v1.0 per-workspace `.gowth-mem/` → v2.7+ global `~/.gowth-mem/`. **NOT** archived; still in `commands/`.

---

## `/mem-migrate` — v0.9 → v1.0 per-workspace migration

**Removed from active surface in 2026-05-06.** Original file: `commands/mem-migrate.md`.

The v0.9 layout had `AGENTS.md` + `docs/` rooted directly in each repo. v1.0 centralized those into a per-workspace `<ws>/.gowth-mem/` folder. Almost no users remain on v0.9, so the command was archived.

If you do have a v0.9 workspace, run this script (originally the body of `/mem-migrate`):

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

After running it, your workspace will be on v1.0; then run `/mem-migrate-global` to import into the v2.7+ global layout at `~/.gowth-mem/workspaces/<ws>/`.

## Migration ladder

```
v0.9 (root)        →  v1.0 (per-workspace)  →  v2.7+ (global)
AGENTS.md             <ws>/.gowth-mem/         ~/.gowth-mem/
docs/*                                         shared/{AGENTS,secrets,tools}.md
                                               workspaces/<ws>/docs/
                                               workspaces/<ws>/<slug>/<slug>.md

(archived here)       /mem-migrate-global      (current default)
```
