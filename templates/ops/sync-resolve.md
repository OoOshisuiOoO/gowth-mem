---
description: AI-mediated conflict resolver. Walks the vault's SYNC-CONFLICT.md, asks user keep-local / keep-remote / merge per file, applies via atomic write, finishes the rebase with the gowth-mem identity, pushes.
---

Resolve a pending git sync conflict in the vault (`$GOWTH_MEM_HOME`, default `~/.gowth-mem`).

Pre-condition: `SYNC-CONFLICT.md` exists at the vault root. If absent, tell the user "no conflict pending" and exit.

Steps:

1. **Read** `SYNC-CONFLICT.md`. It contains, per conflicted file: path, local version, remote version, common ancestor (if any).

2. **For each conflicted file**:
   - Show the user a concise diff (local vs remote, max 40 lines each side).
   - Ask: **keep-local** | **keep-remote** | **merge** | **skip** | **abort**.
   - If `merge`: propose a merged version (preserve both sides' intent), ask user to confirm or refine.
   - If `skip`: leave file at the local version (already restored by `_conflict.py`); user resolves later.
   - If `abort`: run `git -C "${GOWTH_MEM_HOME:-$HOME/.gowth-mem}" rebase --abort`, delete `SYNC-CONFLICT.md`, exit
     (the next sync replays the same commits and may stop on the same conflict).
   - Apply chosen content via:
     ```python
     import os, sys
     from pathlib import Path
     sys.path.insert(0, os.path.expandvars("${CLAUDE_PLUGIN_ROOT}/hooks/scripts"))
     from _atomic import atomic_write
     from _home import gowth_home
     atomic_write(gowth_home() / "<rel-path>", chosen_text)
     ```

3. **After all files** resolved (and not aborted), finish the rebase with ONE command — never a bare git
   continue: it fails on a machine with no git identity. This command takes the sync lock, redacts secrets
   (it refuses while a file holding one cannot be rewritten), stages everything and continues the rebase as
   `gowth-mem@<host>`:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_conflict.py" --continue
   ```
   Read its output (stdout and stderr) and its exit code:
   - **0** — rebase finished. Go to step 4.
   - **2** — a later replayed commit stopped on a NEW conflict (git left unmerged files); `SYNC-CONFLICT.md` was
     rewritten. Repeat from step 1.
   - **1** — nothing was continued and nothing new conflicted; show the user the printed message. A held lock or
     git naming local changes → run the same command again; anything else → stop (never re-walk the same report).

4. **Cleanup**: `rm -f "${GOWTH_MEM_HOME:-$HOME/.gowth-mem}/SYNC-CONFLICT.md"` (sync refuses to run while it exists).

5. **Push**: `python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_sync.py"` — commit, pull --rebase, push; it prints
   `sync: pushed to origin/<branch>`. A new conflict here writes a fresh `SYNC-CONFLICT.md` (exit 2): start again.

6. **Confirm**: report to user "resolved N files, pushed to origin/<branch>".

The user remains in control of every keep/merge decision; AI only does the diff presentation, merge proposal, and mechanical git steps.
