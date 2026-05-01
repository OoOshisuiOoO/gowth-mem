---
description: Migrate v0.9 layout (workspace AGENTS.md + docs/) to v1.0 centralized .gowth-mem/. Moves files, preserves git history if workspace is a git repo. Idempotent — safe to re-run.
---

Migrate from v0.9 (workspace-rooted) to v1.0 (centralized in `.gowth-mem/`).

Run with the Bash tool:

```bash
WS="${CLAUDE_PROJECT_DIR:-$PWD}"
GM="$WS/.gowth-mem"

# 1. Create centralized dir if missing
mkdir -p "$GM/docs/journal" "$GM/docs/skills"

# 2. Move AGENTS.md if it exists at workspace root
if [ -f "$WS/AGENTS.md" ] && [ ! -f "$GM/AGENTS.md" ]; then
  mv "$WS/AGENTS.md" "$GM/AGENTS.md"
  echo "moved: AGENTS.md → .gowth-mem/AGENTS.md"
fi

# 3. Move docs/* if present
if [ -d "$WS/docs" ] && [ ! -f "$GM/docs/handoff.md" ]; then
  for f in handoff exp ref tools secrets files; do
    if [ -f "$WS/docs/$f.md" ]; then
      mv "$WS/docs/$f.md" "$GM/docs/$f.md"
      echo "moved: docs/$f.md → .gowth-mem/docs/$f.md"
    fi
  done
  # Move journal & skills directories if non-empty
  if [ -d "$WS/docs/journal" ] && [ -n "$(ls -A "$WS/docs/journal" 2>/dev/null)" ]; then
    mv "$WS/docs/journal/"* "$GM/docs/journal/" 2>/dev/null
    rmdir "$WS/docs/journal" 2>/dev/null
    echo "moved: docs/journal/* → .gowth-mem/docs/journal/"
  fi
  if [ -d "$WS/docs/skills" ] && [ -n "$(ls -A "$WS/docs/skills" 2>/dev/null)" ]; then
    mv "$WS/docs/skills/"* "$GM/docs/skills/" 2>/dev/null
    rmdir "$WS/docs/skills" 2>/dev/null
    echo "moved: docs/skills/* → .gowth-mem/docs/skills/"
  fi
  rmdir "$WS/docs" 2>/dev/null
fi

# 4. Drop default settings & gitignore inside .gowth-mem/
[ ! -f "$GM/settings.json" ] && cp "${CLAUDE_PLUGIN_ROOT}/templates/dot-gowth-mem/settings.example.json" "$GM/settings.json" && echo "created: .gowth-mem/settings.json"
if [ ! -f "$GM/.gitignore" ]; then
  cat > "$GM/.gitignore" <<EOF
config.json
state.json
index.db
index.db-shm
index.db-wal
__pycache__/
*.pyc
SYNC-CONFLICT.md
EOF
  echo "created: .gowth-mem/.gitignore"
fi

# 5. Inform user
echo
echo "=== Migration done ==="
ls -la "$GM"
echo
echo "Next steps:"
echo "  /mem-config         set up git remote (or just edit .gowth-mem/config.json)"
echo "  /mem-sync --init    push initial state"
echo "  memx                rebuild search index for new layout"
```

## What this command does NOT do

- Doesn't delete workspace `docs/` if non-gowth-mem files are present (e.g. project docs). Only moves the 6 fixed files + journal/ + skills/.
- Doesn't auto-init `.gowth-mem/.git/` (`/mem-sync --init` does that).
- Doesn't rebuild the search index (run `memx` after migration).
