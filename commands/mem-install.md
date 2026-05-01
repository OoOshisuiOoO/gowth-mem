---
description: First-time install wizard for ~/.gowth-mem/. Scaffolds layout, asks for git remote+branch+token, writes settings.json + config.json, runs initial sync.
---

Run the v2.0 install wizard.

Behavior:
1. If `~/.gowth-mem/AGENTS.md` already exists, refuse and suggest `/mem-config` or `/mem-sync`.
2. Otherwise:
   - `mkdir -p ~/.gowth-mem/{topics,docs,journal,skills}`
   - Copy `${CLAUDE_PLUGIN_ROOT}/templates/AGENTS.md` → `~/.gowth-mem/AGENTS.md`
   - Copy `${CLAUDE_PLUGIN_ROOT}/templates/dot-gowth-mem/settings.example.v2.json` → `~/.gowth-mem/settings.json`
   - Copy `${CLAUDE_PLUGIN_ROOT}/templates/topics/_index.md` → `~/.gowth-mem/topics/_index.md`
   - Copy `${CLAUDE_PLUGIN_ROOT}/templates/topics/misc.md` → `~/.gowth-mem/topics/misc.md`
   - Copy `${CLAUDE_PLUGIN_ROOT}/templates/docs/handoff.md` → `~/.gowth-mem/docs/handoff.md`
   - Copy `${CLAUDE_PLUGIN_ROOT}/templates/docs/secrets.md` → `~/.gowth-mem/docs/secrets.md`
   - Copy `${CLAUDE_PLUGIN_ROOT}/templates/docs/tools.md` → `~/.gowth-mem/docs/tools.md`
3. Ask the user three questions:
   - **Git remote URL** (HTTPS preferred for token auth, e.g. `https://github.com/USER/gowth-mem-data.git`).
   - **Branch** (default: `main`).
   - **Token strategy**: env var `GOWTH_MEM_GIT_TOKEN` (recommended) or stored in `config.json` (warn: plaintext).
4. Write `~/.gowth-mem/config.json`:
   ```json
   {
     "remote": "<URL>",
     "branch": "<branch>",
     "host_id": "<machine hostname>",
     "token": "<value>"   // only if user explicitly chose this
   }
   ```
5. Run `python3 ${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_sync.py --init` to create the local repo and push the initial state.
6. Suggest next steps: `memx` (build search index), `/mem-migrate-global` (if v1.0 per-workspace dirs exist).

The wizard is idempotent: re-running it after a successful install does nothing destructive.
