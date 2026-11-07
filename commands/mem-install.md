---
description: "First-time install wizard for ~/.gowth-mem (shared + workspace layout)"
---

Run the v3.0 install wizard. Detects existing installs and offers upgrade path; never destroys data.

## Step 0 — Upgrade detection (run BEFORE anything else)

If `~/.gowth-mem/shared/AGENTS.md` already exists, this is a re-run or an upgrade. Read `~/.gowth-mem/settings.json` and check `layout_version`:

```bash
python3 - <<'PYEOF'
import json, sys
from pathlib import Path
home = Path.home() / ".gowth-mem"
settings_p = home / "settings.json"
if not settings_p.is_file():
    print("status=no-settings")
    sys.exit(0)
try:
    layout = int(json.loads(settings_p.read_text(errors="ignore")).get("layout_version", 0) or 0)
except Exception:
    layout = 0
print(f"status=installed layout_version={layout}")
PYEOF
```

Branch logic:
- `status=no-settings`: corrupt install, refuse and suggest `/mem-doctor`.
- `status=installed layout_version=3`: nothing to scaffold. Deliver the shipped canon the sync-safe way — pull FIRST, then deliver and commit what was delivered, all under one sync lock (a vault with no remote gets it without a pull):
  ```bash
  python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/auto-sync.py" --pull-only
  ```
  Idempotent: it only installs missing canon files and updates ones that still match a shipped version. Do not use Step 4b's bare CLI here — on a synced machine that has not pulled a peer's canon yet it would deliver BEFORE the pull. A non-zero exit means the pull did not complete: offline (the canon waits for the next good pull — nothing to do) or a conflict (run `/mem-sync resolve`). With `GOWTH_MEM_OFF` set auto-sync does nothing: run `/mem-sync` first, then Step 4b. Then print `[mem-install] already on v3.0`, and stop.
- `status=installed layout_version<3`: this is a **v2 → v3 upgrade**. Run a **dry-run** of `/mem-ops migrate-v3` first so the user sees what would change, then prompt:
  ```
  Detected layout_version=<N> (< 3). v3.0 uses topic-FOLDER + dated-aspect layout.
  Migration dry-run summary above. Proceed with migration? [y/N]:
  ```
  On `y`: run `/mem-ops migrate-v3` (real run). On anything else: abort with `[mem-install] upgrade declined — re-run when ready`.

## Step 1 — Fresh install (`~/.gowth-mem/` missing)

Scaffold the shared + workspaces v3 layout:
- `mkdir -p ~/.gowth-mem/shared/skills ~/.gowth-mem/shared/research`
- Copy `${CLAUDE_PLUGIN_ROOT}/templates/AGENTS.md` → `~/.gowth-mem/shared/AGENTS.md`
- Copy `${CLAUDE_PLUGIN_ROOT}/templates/dot-gowth-mem/settings.example.v3.json` → `~/.gowth-mem/settings.json` (carries `layout_version: 3`)
- Copy `${CLAUDE_PLUGIN_ROOT}/templates/docs/secrets.md` → `~/.gowth-mem/shared/secrets.md`
- Copy `${CLAUDE_PLUGIN_ROOT}/templates/docs/tools.md` → `~/.gowth-mem/shared/tools.md`
- Do NOT copy the canon files (`shared/memory-rules.md`, `shared/research/*.md`) by hand — Step 4b delivers them after the first sync
- Run `python3 ${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_workspace.py create default --title "Default Fallback"` (creates v3 scaffold: `docs/`, `journal/`, `skills/`, `research/`, `misc/00-README.md`)

## Step 2 — Git config (ask three questions)

- **Git remote URL** (HTTPS or SSH, e.g. `https://github.com/USER/gowth-mem-data.git`).
- **Branch** (default: `main`).
- **Token strategy**: env var `GOWTH_MEM_GIT_TOKEN` (recommended) or stored in `config.json` (warn: plaintext).

## Step 3 — Write `~/.gowth-mem/config.json`

```json
{
  "remote": "<URL>",
  "branch": "<branch>",
  "host_id": "<machine hostname>",
  "active_workspace": "default",
  "workspace_map": {},
  "token": "<value>"   // only if user explicitly chose this
}
```

**Token security**: never embed in remote URL. `_sync.py` always uses `git -c http.<url>.extraHeader=AUTHORIZATION: basic <b64>` per-command.

## Step 4 — Init the local repo and push

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_sync.py --init
```

(Skip this step for a local-only vault with no remote.)

## Step 4b — Deliver the shipped canon (v4.9)

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_canon.py"
```

`_canon.py` copies the plugin's rules + reference canon into the vault — `shared/memory-rules.md`
(lookup ladder, evidence labels, corrections, gap rules) and `shared/research/{data-quality,
extraction-reuse,provenance}-2026.md` — and adds `workspaces/*/docs/questions.md merge=union` to
the vault's `.gitattributes` (two machines appending to a gap ledger merge line-wise). One line
per file: `installed` (was missing), `current`, `updated` (still matched a version an earlier
release shipped) or `user-modified` (you edited it — never overwritten). It never deletes. In a
git vault it then commits exactly the files it installed or updated (gowth-mem identity, nothing
else staged goes in), so they are never left untracked for the next pull's stash; the next full
sync pushes that commit.

It runs AFTER Step 4 on purpose: on a second machine the pull brings the first machine's copy,
which `_canon.py` then updates in place. Copying first would collide with that copy (add/add).
Afterwards nothing needs re-running: auto-sync delivers (and commits) newer canon right after each
successful pull, and SessionStart does it for a vault without a remote.

## Step 5 — Suggest next steps

- `memx` to build the search index.
- `/mem-ops migrate-global` if v1.0 per-workspace `.gowth-mem/` dirs exist on disk.

The wizard is idempotent: re-running it after a successful install with `layout_version: 3` does nothing destructive (Step 0 short-circuits).
