---
description: Configure git remote + branch + token for ~/.gowth-mem/ sync. Writes ~/.gowth-mem/config.json (gitignored). Token preferably via env GOWTH_MEM_GIT_TOKEN.
argument-hint: "<remote-url> [branch]"
---

Set up `~/.gowth-mem/config.json` for git sync.

Pre-condition: `~/.gowth-mem/AGENTS.md` exists (run `/mem-install` first if not).

Steps:

1. Ask for the git remote URL (HTTPS or SSH). Examples:
   - `https://github.com/USER/gowth-mem-data.git` (use with token via env)
   - `git@github.com:USER/gowth-mem-data.git` (SSH key required)
2. Ask for branch (default: `main`).
3. Auto-detect `host_id` from `socket.gethostname()`.
4. Token strategy: STRONGLY recommend `export GOWTH_MEM_GIT_TOKEN=ghp_xxxxx` in your shell rc.
   - Optional fallback: prompt user if they want to embed token in `config.json`.
   - Warn: token-in-config is plaintext on disk (gitignored, but still local-readable).
5. Write `~/.gowth-mem/config.json`:
   ```json
   {
     "remote": "<URL>",
     "branch": "<branch>",
     "host_id": "<hostname>",
     "token": "<value>"   // only if user explicitly chose this
   }
   ```
   Use `_atomic.atomic_write` so a crash mid-write doesn't corrupt the file.

6. The `_sync.py` script auto-creates `~/.gowth-mem/.gitignore` on first run with the right exclusions.

7. Suggest next: `/mem-sync --init` to push initial state.

## After config

```bash
# First time on machine A:
/mem-install              → set up + initial push (calls /mem-config internally)
/mem-config               → only if you want to change remote later

# Subsequent on machine A: nothing — auto-sync runs on PostCompact.
# Manual: /mem-sync (or memy)

# First time on machine B (fresh clone alt path):
git clone <REMOTE-URL> ~/.gowth-mem
/mem-config               → set token (config.json is gitignored, so not in clone)
memx                      → rebuild local index
```
