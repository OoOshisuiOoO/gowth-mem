---
description: AI-mediated conflict resolver. Walks ~/.gowth-mem/SYNC-CONFLICT.md, asks user keep-local / keep-remote / merge per file, applies via atomic write, commits + pushes.
---

Resolve a pending git sync conflict in `~/.gowth-mem/`.

Pre-condition: `~/.gowth-mem/SYNC-CONFLICT.md` exists. If absent, tell the user "no conflict pending" and exit.

Steps:

1. **Read** `~/.gowth-mem/SYNC-CONFLICT.md`. It contains, per conflicted file: path, local version, remote version, common ancestor (if any).

2. **For each conflicted file**:
   - Show the user a concise diff (local vs remote, max 40 lines each side).
   - Ask: **keep-local** | **keep-remote** | **merge** | **skip** | **abort**.
   - If `merge`: propose a merged version (preserve both sides' intent), ask user to confirm or refine.
   - If `skip`: leave file at the local version (already restored by `_conflict.py`); user resolves later.
   - If `abort`: run `git -C ~/.gowth-mem rebase --abort`, delete `SYNC-CONFLICT.md`, exit.
   - Apply chosen content via:
     ```python
     from _atomic import atomic_write
     atomic_write(Path.home() / ".gowth-mem" / "<rel-path>", chosen_text)
     ```

3. **After all files** resolved (and not aborted):
   ```bash
   cd ~/.gowth-mem
   git add -A
   git rebase --continue   # may need --skip if a file was effectively unchanged
   ```
   This must run under `file_lock("sync")`. Use:
   ```python
   import sys
   sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}/hooks/scripts")
   from _lock import file_lock
   with file_lock("sync"):
       # subprocess git commands here
   ```

4. **Push**: `git -C ~/.gowth-mem push origin <branch>` (still under lock).

5. **Cleanup**: delete `~/.gowth-mem/SYNC-CONFLICT.md`.

6. **Confirm**: report to user "resolved N files, pushed to origin/<branch>".

If `git rebase --continue` fails (e.g. another conflict surfaces), repeat from step 1.

The user remains in control of every keep/merge decision; AI only does the diff presentation, merge proposal, and mechanical git steps.
