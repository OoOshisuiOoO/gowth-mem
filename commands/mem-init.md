---
description: (v2.0 stub) Redirect to /mem-install for first-time setup of the global ~/.gowth-mem/. The old per-workspace .gowth-mem/ is replaced.
---

In v2.0+, memory lives at `~/.gowth-mem/` (global) instead of `<workspace>/.gowth-mem/` (per-workspace), split into `shared/` (cross-workspace) and `workspaces/<ws>/` (per-workspace).

If `~/.gowth-mem/shared/AGENTS.md` already exists, this command exits silently — you're already initialized.

Otherwise, this command **redirects to `/mem-install`** which:

1. Scaffolds `~/.gowth-mem/shared/` + default workspace via `_workspace.py create default`
2. Copies templates (shared/AGENTS.md, settings.json, shared/secrets.md, shared/tools.md)
3. Asks for git remote + branch + token preference
4. Writes `~/.gowth-mem/config.json`
5. Runs initial `_sync.py --init`

```bash
# Detect existing
if [ -f "$HOME/.gowth-mem/shared/AGENTS.md" ]; then
  echo "~/.gowth-mem/ already initialized."
  echo "  - /mem-config to change remote"
  echo "  - /mem-sync   to sync"
  echo "  - /mem-migrate-global to import v1.0 per-workspace data"
  exit 0
fi

# Detect v1.0 per-workspace
if [ -d "${CLAUDE_PROJECT_DIR:-$PWD}/.gowth-mem" ]; then
  echo "Detected v1.0 per-workspace .gowth-mem/. Run /mem-migrate-global to import it after /mem-install."
fi

echo "Run /mem-install to set up the global ~/.gowth-mem/."
```

## Current layout (v2.7+)

```
~/.gowth-mem/
├── shared/                   cross-workspace knowledge
│   ├── AGENTS.md             global operating rules
│   ├── secrets.md            pointers only; never real secret values
│   ├── tools.md
│   └── skills/<slug>.md
├── workspaces/<ws>/          active workspace-scoped knowledge
│   ├── AGENTS.md             workspace rules
│   ├── docs/{handoff,exp,ref,tools,files}.md
│   ├── journal/<date>.md
│   ├── skills/<slug>.md
│   └── <slug>/<slug>.md      topic folder note
├── settings.json             synced behavior settings
├── config.json               remote/branch/token (gitignored)
├── state.json                SRS data (gitignored)
├── index.db                  FTS5 + optional sqlite-vec (gitignored)
├── .locks/                   fcntl lock files (gitignored)
└── .git/                     sync repository
```
