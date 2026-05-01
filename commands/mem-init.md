---
description: Scaffold .gowth-mem/ centralized layout (v1.0): AGENTS.md + 6 docs/* + docs/journal/ + docs/skills/ + settings.json + .gitignore. All gowth-mem state in one folder ready for git sync.
---

Scaffold the v1.0 centralized layout in the current workspace.

Run with the Bash tool:

```bash
WS="${CLAUDE_PROJECT_DIR:-$PWD}"
GM="$WS/.gowth-mem"

# 1. Create centralized dir tree
mkdir -p "$GM/docs/journal" "$GM/docs/skills"

# 2. Copy AGENTS.md
[ -f "$GM/AGENTS.md" ] || cp "${CLAUDE_PLUGIN_ROOT}/templates/AGENTS.md" "$GM/AGENTS.md"

# 3. Copy 6 doc files
for f in handoff exp ref tools secrets files; do
  if [ ! -f "$GM/docs/$f.md" ]; then
    cp "${CLAUDE_PLUGIN_ROOT}/templates/docs/$f.md" "$GM/docs/$f.md"
  fi
done

# 4. Today's journal from template
TODAY=$(date +%Y-%m-%d)
JOURNAL="$GM/docs/journal/$TODAY.md"
if [ ! -f "$JOURNAL" ]; then
  cp "${CLAUDE_PLUGIN_ROOT}/templates/journal-day.md" "$JOURNAL"
  python3 -c "
p='$JOURNAL'; t='$TODAY'
content = open(p).read().replace('YYYY-MM-DD', t)
open(p, 'w').write(content)
"
fi

# 5. settings.json from template
[ -f "$GM/settings.json" ] || cp "${CLAUDE_PLUGIN_ROOT}/templates/dot-gowth-mem/settings.example.json" "$GM/settings.json"

# 6. .gitignore (excludes per-machine state + token)
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
fi

# 7. Show result
ls -la "$GM" "$GM/docs" "$GM/docs/journal" "$GM/docs/skills"
echo
echo "✅ Centralized layout ready at .gowth-mem/"
echo "Optional next steps:"
echo "  /mem-config        configure git remote for sync"
echo "  /mem-sync --init   push initial state to remote"
echo "  memx               build search index"
```

Do not overwrite existing files. Each step skips if target exists.

## v1.0 layout

```
.gowth-mem/
├── AGENTS.md              # operating rules
├── docs/
│   ├── handoff.md         # session state
│   ├── exp.md             # episodic + reflections
│   ├── ref.md             # verified facts (Source REQUIRED)
│   ├── tools.md           # tool registry
│   ├── secrets.md         # POINTER only (never values)
│   ├── files.md           # project structure
│   ├── journal/           # raw daily logs
│   └── skills/            # Voyager reusable workflows
├── settings.json          # plugin behavior (synced)
├── .gitignore             # excludes config + state + index
├── config.json            # NOT created here — run /mem-config
├── state.json             # auto-created on first recall
└── index.db               # auto-created on first /mem-reindex
```

For long-term knowledge (wiki/), use claude-obsidian's `/wiki` separately. The two layers cooperate without conflict.

## Migrating from v0.9?

If you have an older workspace with `docs/` + `AGENTS.md` at the root, run `/mem-migrate` instead — it moves them into `.gowth-mem/` preserving content.
