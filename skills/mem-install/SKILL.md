---
name: mem-install
description: First-time install wizard for ~/.gowth-mem/. Scaffolds shared + workspace v3 layout (topic-folder + dated aspect), gathers git remote+branch+token, writes settings.json + config.json, runs initial sync. Upgrade-aware — detects v2/v3 mismatch and prompts for /mem-migrate-v3.
---

# mem-install

The wizard for a fresh v3.0 install. Run when the user has the plugin installed but no `~/.gowth-mem/` directory yet. Never destroys data.

## Pre-flight — upgrade detection (run BEFORE anything else)

If `~/.gowth-mem/shared/AGENTS.md` already exists, this is a re-run or an upgrade — do NOT scaffold or copy anything. Read `~/.gowth-mem/settings.json` `layout_version`:

- `layout_version = 3`: already installed. Print `[mem-install] already on v3.0` and stop. Suggest `/mem-config` to change remote, `/mem-sync` to sync.
- `layout_version < 3`: **v2 → v3 upgrade**. Dry-run `/mem-migrate-v3` so the user sees what would change, then ask to proceed. On yes run `/mem-migrate-v3`; otherwise abort with `[mem-install] upgrade declined — re-run when ready`.
- `settings.json` missing but vault exists: corrupt install — refuse and suggest `/mem-doctor`.

## Step 1 — scaffold layout (fresh install only)

```bash
# v3 layout: shared/ + workspaces/<ws>/{docs,journal,skills,research,<slug>/...}
mkdir -p ~/.gowth-mem/shared/skills ~/.gowth-mem/shared/research
cp "${CLAUDE_PLUGIN_ROOT}/templates/AGENTS.md" ~/.gowth-mem/shared/AGENTS.md
cp "${CLAUDE_PLUGIN_ROOT}/templates/dot-gowth-mem/settings.example.v3.json" ~/.gowth-mem/settings.json
cp "${CLAUDE_PLUGIN_ROOT}/templates/docs/secrets.md" ~/.gowth-mem/shared/secrets.md
cp "${CLAUDE_PLUGIN_ROOT}/templates/docs/tools.md" ~/.gowth-mem/shared/tools.md
cp "${CLAUDE_PLUGIN_ROOT}/templates/dot-gowth-mem/shared/research/data-quality-2026.md" ~/.gowth-mem/shared/research/data-quality-2026.md
# Default workspace scaffolded by _workspace.py (creates docs/, journal/, skills/, research/, misc/00-README.md):
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_workspace.py" create default --title "Default Fallback"
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_moc.py" --all
```

`settings.example.v3.json` carries `layout_version: 3`. `data-quality-2026.md` is the canonical write-quality canon referenced from `shared/AGENTS.md` §7.

## Step 2 — gather config

Ask the user (one question at a time; accept defaults via blank input):

1. **Git remote URL?** Example `https://github.com/USER/gowth-mem-data.git`. HTTPS recommended for token auth.
2. **Branch?** Default `main`.
3. **Token strategy?**
   - `env` (recommended): user later runs `export GOWTH_MEM_GIT_TOKEN=ghp_...` in their shell rc. Store nothing in config.
   - `config`: paste a token; we save it in `~/.gowth-mem/config.json` (warn: plaintext on disk; ignored by git).

Auto-detect `host_id` from `socket.gethostname()`.

## Step 3 — write config.json

```json
{
  "remote": "<URL>",
  "branch": "<branch>",
  "host_id": "<hostname>",
  "token": "<value>"   // only if user chose 'config'
}
```

Use `_atomic.atomic_write` so a crash mid-write doesn't leave a corrupt file.

## Step 4 — initial sync

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_sync.py" --init
```

This creates `.git`, sets up `origin`, commits, attempts pull (allowing unrelated histories), pushes. Reports each step.

## Step 5 — confirm + suggest next

Tell the user:
- `~/.gowth-mem/` is now active.
- Suggest `memx` to build the search index.
- If `~/Git/<some-workspace>/.gowth-mem/` exists (v1.0 layout), suggest `/mem-migrate-global`.

## Idempotence

Each step skips if the target already exists. Re-running after a successful install does nothing destructive.
