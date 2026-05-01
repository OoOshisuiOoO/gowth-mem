---
name: mem-install
description: First-time install wizard for ~/.gowth-mem/. Scaffolds layout, gathers git remote+branch+token, writes settings.json + config.json, runs initial sync.
---

# mem-install

The wizard for a fresh v2.0 install. Run when the user has the plugin installed but no `~/.gowth-mem/` directory yet.

## Pre-flight

If `~/.gowth-mem/AGENTS.md` already exists, abort with: "Already installed. Use `/mem-config` to change remote, `/mem-sync` to sync, or `/mem-migrate-global` to import v1.0 data."

## Step 1 — scaffold layout

```bash
mkdir -p ~/.gowth-mem/{topics,docs,journal,skills}
cp "${CLAUDE_PLUGIN_ROOT}/templates/AGENTS.md" ~/.gowth-mem/AGENTS.md
cp "${CLAUDE_PLUGIN_ROOT}/templates/dot-gowth-mem/settings.example.v2.json" ~/.gowth-mem/settings.json
cp "${CLAUDE_PLUGIN_ROOT}/templates/topics/_index.md" ~/.gowth-mem/topics/_index.md
cp "${CLAUDE_PLUGIN_ROOT}/templates/topics/misc.md" ~/.gowth-mem/topics/misc.md
cp "${CLAUDE_PLUGIN_ROOT}/templates/docs/handoff.md" ~/.gowth-mem/docs/handoff.md
cp "${CLAUDE_PLUGIN_ROOT}/templates/docs/secrets.md" ~/.gowth-mem/docs/secrets.md
cp "${CLAUDE_PLUGIN_ROOT}/templates/docs/tools.md" ~/.gowth-mem/docs/tools.md
```

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
