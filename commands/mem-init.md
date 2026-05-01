---
description: (v2.0 stub) Redirect to /mem-install for first-time setup of the global ~/.gowth-mem/. The old per-workspace .gowth-mem/ is replaced.
---

In v2.0, memory lives at `~/.gowth-mem/` (global) instead of `<workspace>/.gowth-mem/` (per-workspace).

If `~/.gowth-mem/AGENTS.md` already exists, this command exits silently — you're already initialized.

Otherwise, this command **redirects to `/mem-install`** which:

1. Scaffolds `~/.gowth-mem/{topics,docs,journal,skills}/`
2. Copies templates (AGENTS.md, settings.json, topics/_index.md, topics/misc.md, docs/handoff.md, docs/secrets.md, docs/tools.md)
3. Asks for git remote + branch + token preference
4. Writes `~/.gowth-mem/config.json`
5. Runs initial `_sync.py --init`

```bash
# Detect existing
if [ -f "$HOME/.gowth-mem/AGENTS.md" ]; then
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

## v2.0 layout

```
~/.gowth-mem/
├── AGENTS.md              # operating rules (synced)
├── settings.json          # plugin behavior (synced)
├── config.json            # remote+token (gitignored, per-machine)
├── state.json             # SRS data (gitignored, per-machine)
├── index.db               # FTS5+vec search (gitignored, per-machine)
├── .git/                  # synced repo
├── .locks/                # flock files (gitignored)
├── topics/                # ★ topic-organized knowledge
│   ├── _index.md
│   └── <slug>.md          # one file per topic, 7-type [exp]/[ref]/[tool]/...
├── docs/                  # cross-topic registries
│   ├── handoff.md         # session state (host:<name> prefix per line)
│   ├── secrets.md         # POINTER only (env-var names)
│   └── tools.md           # cross-topic tool registry
├── journal/<date>.md      # raw daily logs (synced; small, append-only)
└── skills/<slug>.md       # Voyager workflows (synced)
```
