---
name: mem-sync-resolve
description: "Resolve a pending SYNC-CONFLICT.md — walk each file, apply the choice, push"
---

# mem-sync-resolve

When `SYNC-CONFLICT.md` is present at the vault root (`$GOWTH_MEM_HOME`, default `~/.gowth-mem`), the vault is mid-rebase. This skill walks each conflicted file with the user, applies their decisions, and finishes the rebase.

## Pre-flight

```bash
VAULT="${GOWTH_MEM_HOME:-$HOME/.gowth-mem}"
test -f "$VAULT/SYNC-CONFLICT.md" || echo "No conflict pending. Nothing to resolve."
```

If there is no conflict pending, stop here.

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
   git -C "${GOWTH_MEM_HOME:-$HOME/.gowth-mem}" rebase --abort
   rm -f "${GOWTH_MEM_HOME:-$HOME/.gowth-mem}/SYNC-CONFLICT.md"
   ```
   Tell the user: "rebase aborted; local state restored to pre-pull commit. The next sync replays the same commits and may stop on the same conflict." Exit.
5. **Apply chosen content**:
   ```python
   import sys
   sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}/hooks/scripts")
   from _atomic import atomic_write
   from _home import gowth_home
   atomic_write(gowth_home() / rel_path, chosen_text)
   ```

## Step 3 — finish the rebase

One command, never a bare git continue (it fails on a machine with no git identity, and git's hint then asks you to change the user's global git config — don't). It takes the sync lock, redacts secrets (refusing while a file that holds one cannot be rewritten), stages everything and continues the rebase as `gowth-mem@<host>`:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_conflict.py" --continue
```

Show the user its output — stdout AND stderr — and branch on the exit code:

- **0** — the rebase finished. Go to Step 4.
- **2** — a later replayed commit stopped on a NEW conflict (git left unmerged files); `SYNC-CONFLICT.md` was rewritten. Restart from Step 1.
- **1** — nothing was continued and nothing new conflicted; the printed message says why. A held lock (sync lock, or a file holding a secret that could not be rewritten yet) or git naming local changes → run the same command again; anything else → relay git's message to the user and stop. Never re-run this skill on the same report.

## Step 4 — cleanup and push

```bash
rm -f "${GOWTH_MEM_HOME:-$HOME/.gowth-mem}/SYNC-CONFLICT.md"
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_sync.py"
```

`_sync.py` refuses while `SYNC-CONFLICT.md` exists, so the file goes first. It commits, pulls and pushes (`sync: pushed to origin/<branch>`); a new conflict there writes a fresh `SYNC-CONFLICT.md` (exit 2) — start again.

Confirm to the user: "resolved N files, pushed to origin/<branch>."

## Notes

- The user makes every keep/merge decision; AI only handles the mechanical work.
- Locking prevents another session from racing on git operations during resolution.
- If the user wants to resolve later, leaving `SYNC-CONFLICT.md` in place is safe — the conflict-detect hook will keep reminding them.
