---
description: Configure git remote + branch for .gowth-mem/ sync. Writes .gowth-mem/config.json (gitignored). Token preferably via env GOWTH_MEM_GIT_TOKEN.
argument-hint: "<remote-url> [branch]"
---

Set up `.gowth-mem/config.json` for git sync.

Steps the skill will execute:

1. Ask user for the git remote URL (HTTPS or SSH). Examples:
   - `https://github.com/USER/REPO.git` (use with token via env)
   - `git@github.com:USER/REPO.git` (SSH, no token needed if SSH key set up)
2. Ask for branch (default: `main`).
3. Token: STRONGLY recommend setting `export GOWTH_MEM_GIT_TOKEN=ghp_xxxxx` in shell.
   Optional fallback: prompt user if they want to embed token in `config.json`
   (warn: file is gitignored but readable on disk).
4. Write `.gowth-mem/config.json`:
   ```json
   {"remote": "<URL>", "branch": "<branch>"}
   ```
5. Ensure `.gowth-mem/.gitignore` exists with proper entries (the `_sync.py` writes one if missing).
6. Suggest user run `/mem-sync --init` next to bootstrap the remote.

## After config

```bash
# First time on machine A:
/mem-config              → set remote
/mem-sync --init         → create .git, push initial state

# Subsequent on machine A:
/mem-sync                → push local changes, pull remote

# First time on machine B (fresh clone):
git clone <REMOTE-URL> .gowth-mem
/mem-config              → set remote (again, locally; doesn't sync this file)
memx                     → rebuild local index
```
