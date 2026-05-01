---
name: mem-sync-resolve
description: AI-mediated git conflict resolver. Walks ~/.gowth-mem/SYNC-CONFLICT.md, asks user keep-local / keep-remote / merge per file, applies via atomic write, then commits + pushes under lock.
---

# mem-sync-resolve

When `~/.gowth-mem/SYNC-CONFLICT.md` is present, the user's repo is mid-rebase. This skill walks each conflicted file with the user, applies their decisions, and finishes the rebase.

## Pre-flight

```python
from pathlib import Path
cm = Path.home() / ".gowth-mem" / "SYNC-CONFLICT.md"
if not cm.is_file():
    print("No conflict pending. Nothing to resolve.")
    return
```

## Step 1 — parse the conflict report

`SYNC-CONFLICT.md` is structured: each `### <path>` block has `**Local**`, `**Remote**`, optional `**Common ancestor**` fenced sections. Parse them.

## Step 2 — walk each file with the user

For each conflict block:

1. **Show concise diff**: print local (max 40 lines) + remote (max 40 lines) side by side or sequentially. Highlight the differing lines if practical.
2. **Ask** the user (one of):
   - `keep-local` — keep this machine's version
   - `keep-remote` — adopt the incoming version
   - `merge` — propose a merged version
   - `skip` — leave as local for now (still need to resolve later)
   - `abort` — abort the rebase entirely
3. **On `merge`**: propose a merged text that preserves intent of both sides. Show the proposal. Ask the user to confirm or paste their own edited version.
4. **On `abort`**:
   ```bash
   git -C ~/.gowth-mem rebase --abort
   rm ~/.gowth-mem/SYNC-CONFLICT.md
   ```
   Tell the user: "rebase aborted; local state restored to pre-pull commit." Exit.
5. **Apply chosen content**:
   ```python
   import sys
   sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}/hooks/scripts")
   from _atomic import atomic_write
   from pathlib import Path
   atomic_write(Path.home() / ".gowth-mem" / rel_path, chosen_text)
   ```

## Step 3 — finish the rebase under lock

```python
import subprocess, sys
from pathlib import Path
sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}/hooks/scripts")
from _lock import file_lock

gh = Path.home() / ".gowth-mem"
with file_lock("sync"):
    subprocess.run(["git", "-C", str(gh), "add", "-A"], check=True)
    r = subprocess.run(["git", "-C", str(gh), "rebase", "--continue"],
                       capture_output=True, text=True)
    if r.returncode != 0 and "No changes" in (r.stdout + r.stderr):
        # Empty patch — skip and continue
        subprocess.run(["git", "-C", str(gh), "rebase", "--skip"], check=True)
    elif r.returncode != 0:
        # Another conflict surfaced; restart the skill from Step 1
        # (write a fresh SYNC-CONFLICT.md)
        from _conflict import package_conflict
        package_conflict()
        print("New conflict surfaced; re-run /mem-sync-resolve.")
        return
    # Push
    subprocess.run(["git", "-C", str(gh), "push", "origin", "<branch>"], check=True)
```

## Step 4 — cleanup

```bash
rm ~/.gowth-mem/SYNC-CONFLICT.md
```

Confirm to the user: "resolved N files, pushed to origin/<branch>."

## Notes

- The user makes every keep/merge decision; AI only handles the mechanical work.
- Locking prevents another session from racing on git operations during resolution.
- If the user wants to resolve later, leaving `SYNC-CONFLICT.md` in place is safe — the conflict-detect hook will keep reminding them.
