---
description: First-time install wizard for ~/.gowth-mem/. Scaffolds shared + workspace layout, asks for git remote+branch+token, writes settings.json + config.json, runs initial sync.
---

Run the current install wizard.

Behavior:
1. If `~/.gowth-mem/shared/AGENTS.md` already exists, refuse and suggest `/mem-config` or `/mem-sync`.
2. Otherwise scaffold the shared + workspaces layout:
   - `mkdir -p ~/.gowth-mem/shared/skills`
   - Copy `${CLAUDE_PLUGIN_ROOT}/templates/AGENTS.md` → `~/.gowth-mem/shared/AGENTS.md`
   - Copy `${CLAUDE_PLUGIN_ROOT}/templates/dot-gowth-mem/settings.example.v2.json` → `~/.gowth-mem/settings.json`
   - Copy `${CLAUDE_PLUGIN_ROOT}/templates/docs/secrets.md` → `~/.gowth-mem/shared/secrets.md`
   - Copy `${CLAUDE_PLUGIN_ROOT}/templates/docs/tools.md` → `~/.gowth-mem/shared/tools.md`
   - Run `python3 ${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_workspace.py create default --title "Default Fallback"`
3. Ask the user three questions:
   - **Git remote URL** (HTTPS or SSH, e.g. `https://github.com/USER/gowth-mem-data.git`).
   - **Branch** (default: `main`).
   - **Token strategy**: env var `GOWTH_MEM_GIT_TOKEN` (recommended) or stored in `config.json` (warn: plaintext).
4. Write `~/.gowth-mem/config.json`:
   ```json
   {
     "remote": "<URL>",
     "branch": "<branch>",
     "host_id": "<machine hostname>",
     "active_workspace": "default",
     "workspace_map": {},
     "token": "<value>"   // only if user explicitly chose this
   }
   ```
5. Run `python3 ${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_sync.py --init` to create the local repo and push the initial state.
6. Suggest next steps: `memx` (build search index), `/mem-migrate-global` (if v1.0 per-workspace dirs exist).

The wizard is idempotent: re-running it after a successful install does nothing destructive.
