#!/usr/bin/env bash
# Rollback ~/.gowth-mem/ from v3.x to v2.x using a backup created by
# hooks/scripts/_migrate_v3.py. Non-destructive: the current workspaces tree
# is first staged under .backup/rolled-back-<utc>/ so a rollback can itself
# be rolled back.
#
# Usage:
#   bin/rollback-v3.sh                              # restore newest backup
#   bin/rollback-v3.sh v2-pre-v3-<YYYYMMDDTHHMMSSZffffff>   # explicit
#   bin/rollback-v3.sh --list                       # list available backups
#   bin/rollback-v3.sh --dry-run                    # show what would change
#
# 6-step pipeline (mirrors _migrate_v3.py STEPs in reverse):
#   1. Acquire migrate-v3 lock (same lock as forward migration).
#   2. Validate target backup (MANIFEST.json exists, sha256 manifest readable).
#   3. Stage current workspaces/* under .backup/rolled-back-<utc>/.
#   4. Restore workspaces/* from the target backup (rsync-like copy).
#   5. Reset settings.layout_version to 2.
#   6. Print next-step instructions (no auto-commit — user reviews first).
#
# Always exits non-zero on failure. Idempotent across re-runs only if the
# target backup is still present.

set -uo pipefail

GOWTH_HOME="${GOWTH_MEM_HOME:-$HOME/.gowth-mem}"
BACKUP_ROOT="$GOWTH_HOME/.backup"
LOCK_DIR="$GOWTH_HOME/.locks"
DRY_RUN=0
LIST=0
TARGET=""

usage() {
  sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

log() { printf '%s\n' "$*" >&2; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --list)    LIST=1; shift ;;
    -h|--help) usage 0 ;;
    -*)        log "unknown flag: $1"; usage 2 ;;
    *)         TARGET="$1"; shift ;;
  esac
done

[[ -d "$GOWTH_HOME" ]] || { log "no GOWTH_MEM_HOME at $GOWTH_HOME"; exit 2; }

# --list short-circuit (no lock needed).
if [[ $LIST -eq 1 ]]; then
  if [[ ! -d "$BACKUP_ROOT" ]]; then
    log "no backups: $BACKUP_ROOT does not exist"
    exit 0
  fi
  find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name 'v2-pre-v3-*' \
    | sort -r \
    | while read -r d; do
        manifest="$d/MANIFEST.json"
        ts=$(basename "$d" | sed 's/^v2-pre-v3-//')
        if [[ -f "$manifest" ]]; then
          printf '%s\t%s\n' "$ts" "$(du -sh "$d" 2>/dev/null | cut -f1)"
        else
          printf '%s\t(no MANIFEST)\n' "$ts"
        fi
      done
  exit 0
fi

# Resolve target backup.
if [[ -z "$TARGET" ]]; then
  TARGET=$(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name 'v2-pre-v3-*' \
    2>/dev/null | sort -r | head -n1)
  [[ -n "$TARGET" ]] || { log "no v2-pre-v3-* backup found in $BACKUP_ROOT"; exit 2; }
fi

# Allow either basename or absolute path.
case "$TARGET" in
  /*) BACKUP_DIR="$TARGET" ;;
  *)  BACKUP_DIR="$BACKUP_ROOT/$TARGET" ;;
esac

[[ -d "$BACKUP_DIR" ]] || { log "backup not found: $BACKUP_DIR"; exit 2; }
[[ -f "$BACKUP_DIR/MANIFEST.json" ]] || {
  log "warning: $BACKUP_DIR/MANIFEST.json missing — restoring blindly";
}

# STEP 1: lock (advisory mkdir-mutex; portable across linux/macOS — no flock dep).
# mkdir is atomic on POSIX filesystems; ensures no concurrent migrate/rollback.
mkdir -p "$LOCK_DIR"
LOCK_MUTEX="$LOCK_DIR/migrate-v3.mutex"
if ! mkdir "$LOCK_MUTEX" 2>/dev/null; then
  # Stale lock if mtime > 5min old and the holding pid (if any) is dead.
  if [[ -f "$LOCK_MUTEX/pid" ]]; then
    held_pid=$(cat "$LOCK_MUTEX/pid" 2>/dev/null || echo "")
    if [[ -n "$held_pid" ]] && ! kill -0 "$held_pid" 2>/dev/null; then
      log "stale lock (pid $held_pid is dead); clearing"
      rm -rf "$LOCK_MUTEX"
      mkdir "$LOCK_MUTEX" || { log "could not acquire lock"; exit 3; }
    else
      log "another migrate/rollback is running (lock: $LOCK_MUTEX)"
      exit 3
    fi
  else
    log "another migrate/rollback is running (lock: $LOCK_MUTEX)"
    exit 3
  fi
fi
echo "$$" > "$LOCK_MUTEX/pid"
trap 'rm -rf "$LOCK_MUTEX"' EXIT

stamp_utc() {
  # GNU date supports %N (nanoseconds); BSD/macOS does not. Use python3 for
  # portability — already a hard dependency for hooks/scripts/*.py.
  python3 -c 'from datetime import datetime, timezone; print(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ%f"))'
}
STAGE_STAMP=$(stamp_utc)
STAGE_DIR="$BACKUP_ROOT/rolled-back-$STAGE_STAMP"

# STEP 2: validate target backup workspaces/ exists.
[[ -d "$BACKUP_DIR/workspaces" ]] || {
  log "backup has no workspaces/ tree: $BACKUP_DIR"; exit 4;
}

log "rollback target: $BACKUP_DIR"
log "stage current state: $STAGE_DIR"
log "dry-run: $DRY_RUN"

# STEP 3: stage current workspaces/ for safety.
if [[ -d "$GOWTH_HOME/workspaces" ]]; then
  if [[ $DRY_RUN -eq 1 ]]; then
    log "[dry-run] would stage workspaces/ -> $STAGE_DIR/workspaces/"
  else
    mkdir -p "$STAGE_DIR"
    cp -a "$GOWTH_HOME/workspaces" "$STAGE_DIR/workspaces"
  fi
fi

# STEP 4: restore from backup. Replace workspaces/ wholesale.
if [[ $DRY_RUN -eq 1 ]]; then
  log "[dry-run] would rm -rf $GOWTH_HOME/workspaces"
  log "[dry-run] would cp -a $BACKUP_DIR/workspaces $GOWTH_HOME/workspaces"
else
  rm -rf "$GOWTH_HOME/workspaces"
  cp -a "$BACKUP_DIR/workspaces" "$GOWTH_HOME/workspaces"
fi

# STEP 5: reset settings.layout_version to 2 (use python for atomic JSON edit).
SETTINGS="$GOWTH_HOME/settings.json"
if [[ -f "$SETTINGS" ]]; then
  if [[ $DRY_RUN -eq 1 ]]; then
    log "[dry-run] would set settings.layout_version = 2"
  else
    python3 - "$SETTINGS" <<'PY'
import json, sys, os, tempfile
p = sys.argv[1]
try:
    with open(p) as f:
        s = json.load(f)
except Exception:
    s = {}
s["layout_version"] = 2
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), prefix=".settings.", suffix=".json")
with os.fdopen(fd, "w") as f:
    json.dump(s, f, indent=2)
    f.write("\n")
os.replace(tmp, p)
PY
  fi
fi

# STEP 6: print next-step instructions.
cat >&2 <<EOF
rollback complete (target: $(basename "$BACKUP_DIR"))
  staged current state: $STAGE_DIR
  settings.layout_version reset to 2

next steps:
  1. inspect ~/.gowth-mem/ to confirm v2 layout restored as expected
  2. delete index.db (will be rebuilt by /mem-reindex): rm -f $GOWTH_HOME/index.db
  3. if happy: commit via /mem-sync, otherwise re-run forward /mem-migrate-v3

to undo this rollback:
  bin/rollback-v3.sh \$(basename "$STAGE_DIR" | sed 's/^rolled-back-/v2-pre-v3-/')
  (note: the staged state is stored as rolled-back-<utc>, not a v2-pre-v3-* backup,
   so manual recovery requires: cp -a $STAGE_DIR/workspaces $GOWTH_HOME/workspaces)
EOF

exit 0
