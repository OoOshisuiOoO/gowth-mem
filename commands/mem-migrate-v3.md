---
description: Migrate ~/.gowth-mem/ from v2.4 single-file-per-topic to v3.0 topic-folder + dated-aspect layout. Atomic (rollback-safe via shared/backup-v3/), idempotent (already-v3 short-circuits), git-aware (aborts on stale remote).
argument-hint: "[--dry-run | --force | --report | --json]"
---

# /mem-migrate-v3

Run the 7-step v2 → v3 migration pipeline (`hooks/scripts/_migrate_v3.py`). Default mode is **JSON output** for tool composition; pass `--report` for human-readable text.

## What changes

| v2.4 layout | v3.0 layout |
|---|---|
| `workspaces/<ws>/<slug>/<slug>.md` (single landing) | `workspaces/<ws>/<slug>/00-README.md` (MOC) + `YYYY-MM-DD-<aspect>.md` (dated aspect files) |
| `workspaces/<ws>/<slug>/<sub>.md` (sub-aspect) | `workspaces/<ws>/<slug>/<MIGRATION-DATE>-<sub>.md` |
| `workspaces/<ws>/<slug>.md` (v2.3 flat) | `workspaces/<ws>/<slug>/00-README.md` (promoted to folder) |
| `<slug>/lessons.md` | unchanged — stays at `<slug>/lessons.md` |

Reserved subdirs (`docs`, `journal`, `skills`, `research`) are never reshaped.

## Flags

- `--dry-run` — classify + verify only. NO writes. Prints planned moves and exits 0.
- `--force` — run even if `settings.layout_version` already reads 3 (recover from partial state).
- `--report` — human-readable text output (counts + per-workspace breakdown + warnings).
- `--json` (default) — machine-parseable report (see schema below).

## Steps run (per plan §3.2)

1. **Snapshot backup** → `shared/backup-v3/v2-pre-v3-<YYYYMMDDTHHMMSSZffffff>/` (microsecond UTC stamp; manifest with sha256+size).
2. **Classify moves** → 8 action types (v23_flat_promote, v24_landing_to_readme, v24_subaspect_to_dated, lazy_nest_*, lessons_keep, already_*, domain_map_delete).
3. **Execute moves** atomically via `_atomic.atomic_write`. Conflict detection: any destination path that would be written twice aborts the run with `dst_conflict`.
4. **Verify** by re-reading body sha256 (excluding frontmatter, which gets patched) against the original.
5. **Cleanup originals** bottom-up; remove empty dirs.
6. **Rebuild metadata** — `_moc.rebuild_all()` + `_index.py --full`.
7. **Bump `layout_version: 3`** in `settings.json`, then `git fetch + merge --ff-only origin/<branch>` (F9: abort `stale_remote_abort` on conflict — leave backup untouched). Commit `v3 migration <UTC stamp>`. Outside the sync lock per §3.7.

After STEP 7: prune backups to rolling-2 window (demote oldest if ≥24h old; keep newest 2).

## Locks (per plan §3.7)

- Outer: `file_lock("migrate-v3", timeout=60)` for the whole pipeline.
- Inner: `file_lock("sync", timeout=30)` wraps STEPS 1-6 (filesystem mutations).
- STEP 7 (git fetch/merge/commit) runs OUTSIDE the sync lock so a parallel session's `auto-sync.py` doesn't deadlock.

## Idempotency (F2)

Before doing anything, the script runs `git log -1 --format=%s origin/<branch>`. If the subject matches `^v3 migration `, return immediately with `{"status":"already_v3_on_remote"}` (unless `--force`).

## Output — JSON schema (default)

```json
{
  "status": "ok" | "dry_run" | "already_v3_on_remote" | "stale_remote_abort" | "dst_conflict" | "verify_fail" | "lock_busy",
  "layout_version_before": 2,
  "layout_version_after": 3,
  "backup_dir": "shared/backup-v3/v2-pre-v3-20260517T103045Z123456",
  "workspaces": {
    "<ws>": {
      "moves": 42,
      "actions": {"v24_landing_to_readme": 12, "v24_subaspect_to_dated": 28, "lessons_keep": 2},
      "warnings": []
    }
  },
  "git": {
    "branch": "main",
    "commit_sha": "abc1234",
    "commit_subject": "v3 migration 20260517T103045Z"
  }
}
```

## Output — human report (`--report`)

```
[gowth-mem v3 migration] 2026-05-17T10:30:45Z
Backup: shared/backup-v3/v2-pre-v3-20260517T103045Z123456 (kept; rolling-2 window)

Workspaces:
  default       42 moves   (12 landing→readme, 28 subaspect→dated, 2 lessons kept)
  trade         18 moves   (7  landing→readme, 11 subaspect→dated)

Git:
  branch=main  commit=abc1234  "v3 migration 20260517T103045Z"

Settings: layout_version 2 → 3

Run /mem-doctor to verify or roll back via bin/rollback-v3.sh.
```

## Run

```bash
# Default: JSON to stdout
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_migrate_v3.py" "$@"
```

## Rollback

If something looks wrong after migration:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/bin/rollback-v3.sh"
```

This restores the most recent `shared/backup-v3/v2-pre-v3-*/` snapshot, resets `layout_version` to its prior value, and rebuilds the index. Backup retention (rolling-2) means you have at most 2 chances; commit a recovery branch first if you want longer history.

## Hard rules

- Pure-stdlib Python 3.9+. No pip deps.
- All writes via `_atomic.atomic_write` (tempfile + `os.replace`).
- Concurrent ops protected by `_lock.file_lock` with explicit timeout.
- No `~/.gowth-mem/` paths hardcoded — uses `_home.py` resolver.
- On any verification failure: leave the backup, do NOT touch originals, exit with `verify_fail`.
- Never reshape `docs/`, `journal/`, `skills/`, `research/` (reserved).
- Never rewrite topic slugs (would break wikilinks across machines).
