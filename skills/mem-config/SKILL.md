---
name: mem-config
description: Use to set up .gowth-mem/config.json with git remote URL and branch for /mem-sync. Token preferably via env var GOWTH_MEM_GIT_TOKEN.
---

# mem-config

Write `.gowth-mem/config.json` with git sync settings.

## Inputs

- `remote` URL (HTTPS or SSH). User supplies.
- `branch` (default `main`).
- `token` (optional — strongly prefer env var instead).

## Steps

1. Ensure `.gowth-mem/` exists; if not, run `/mem-init` first.
2. Ask user for remote URL. Validate it looks like a git URL.
3. Ask for branch (default `main`).
4. Ask whether to set token in `config.json` or via env (recommend env).
5. Write `.gowth-mem/config.json`:
   ```json
   {
     "remote": "<URL>",
     "branch": "<branch>"
   }
   ```
   Add `"token": "<value>"` ONLY if user explicitly asked to embed.
6. Ensure `.gowth-mem/.gitignore` exists and contains `config.json`. The `_sync.py` writes one if missing; otherwise verify.
7. Tell user: `export GOWTH_MEM_GIT_TOKEN=ghp_...` in shell, then run `/mem-sync --init`.

## Hard rules

- `config.json` MUST be in `.gowth-mem/.gitignore` — token leak otherwise.
- If user provides token, warn: it's plaintext on disk; use a fine-scoped GitHub PAT (only `repo` scope).
- NEVER write `config.json` to a path outside `.gowth-mem/`.
