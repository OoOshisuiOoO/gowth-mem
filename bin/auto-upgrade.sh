#!/usr/bin/env bash
# Auto-upgrade gowth-mem plugin, bypassing Claude Code bug #52218.
#
# What this script does (in order):
#   1. git pull the marketplace clone (~/.claude/plugins/marketplaces/<MARKET>)
#   2. Read new version from marketplace plugin.json
#   3. If installed_plugins.json shows older version:
#       a. Create cache dir at ~/.claude/plugins/cache/<MARKET>/<PLUGIN>/<NEW_VER>/
#       b. Copy marketplace clone (excluding .git, __pycache__) into cache dir
#       c. Atomically update installed_plugins.json:
#            - version, installPath, lastUpdated, gitCommitSha
#       d. (Optional) prune the old cache dir
#   4. Print restart hint
#
# Why this is needed:
#   Claude Code issue #52218 — autoUpdate field bumps runtime version but does
#   NOT update installed_plugins.json.installPath, so bundled hooks keep firing
#   from the old cache dir. This script closes that gap.
#
# Usage:
#   bin/auto-upgrade.sh                    # default: gowth-mem plugin in gowth-mem marketplace
#   bin/auto-upgrade.sh <market> <plugin>  # e.g. omc oh-my-claudecode
#   bin/auto-upgrade.sh --quiet            # silent unless an update applied
#   bin/auto-upgrade.sh --keep-old         # don't prune old cache dir
#   bin/auto-upgrade.sh --dry-run          # show what would change
#
# Suggested cron (daily at 9am):
#   0 9 * * * /path/to/bin/auto-upgrade.sh --quiet >> ~/.gowth-mem/upgrade.log 2>&1

set -euo pipefail

MARKET="gowth-mem"
PLUGIN="gowth-mem"
QUIET=0
KEEP_OLD=0
DRY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --quiet) QUIET=1; shift ;;
    --keep-old) KEEP_OLD=1; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '2,30p' "$0" | sed 's/^# //; s/^#$//'; exit 0 ;;
    *)
      if [ -z "${MARKET_OVERRIDE:-}" ]; then MARKET_OVERRIDE="$1"; MARKET="$1"
      elif [ -z "${PLUGIN_OVERRIDE:-}" ]; then PLUGIN_OVERRIDE="$1"; PLUGIN="$1"
      else echo "unexpected arg: $1" >&2; exit 2
      fi
      shift ;;
  esac
done

CC_ROOT="${HOME}/.claude/plugins"
MARKET_DIR="${CC_ROOT}/marketplaces/${MARKET}"
CACHE_BASE="${CC_ROOT}/cache/${MARKET}/${PLUGIN}"
INSTALLED="${CC_ROOT}/installed_plugins.json"

log()  { [ "$QUIET" -eq 0 ] && echo "[$(date +%H:%M:%S)] $*" || true; }
say()  { echo "[$(date +%H:%M:%S)] $*"; }

# ─── preflight ──────────────────────────────────────────────────────────

[ -d "$MARKET_DIR/.git" ] || { say "ERROR: $MARKET_DIR not a git clone — install plugin first"; exit 1; }
[ -f "$INSTALLED" ]       || { say "ERROR: $INSTALLED missing"; exit 1; }
[ -f "$MARKET_DIR/.claude-plugin/plugin.json" ] || { say "ERROR: marketplace clone missing plugin.json"; exit 1; }
command -v python3 >/dev/null || { say "ERROR: python3 not on PATH"; exit 1; }

# ─── pull marketplace ───────────────────────────────────────────────────

cd "$MARKET_DIR"
OLD_HEAD=$(git rev-parse HEAD)
git fetch --quiet origin 2>/dev/null || { say "ERROR: git fetch failed for $MARKET_DIR"; exit 1; }
DEFAULT_BRANCH=$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's@^origin/@@' || echo main)
NEW_HEAD=$(git rev-parse "origin/${DEFAULT_BRANCH}")

if [ "$OLD_HEAD" = "$NEW_HEAD" ]; then
  log "${MARKET}: already at latest ($OLD_HEAD)"
else
  log "${MARKET}: ${OLD_HEAD:0:8} → ${NEW_HEAD:0:8}"
  if [ "$DRY" -eq 0 ]; then
    git pull --ff-only --quiet origin "$DEFAULT_BRANCH"
  fi
fi

# Read the new version even if no pull (cache could still be stale from a prior partial run)
NEW_VER=$(python3 -c "import json; print(json.load(open('.claude-plugin/plugin.json'))['version'])")

# ─── compare with installed_plugins.json ─────────────────────────────────

KEY="${PLUGIN}@${MARKET}"
read -r OLD_VER OLD_PATH < <(python3 - "$INSTALLED" "$KEY" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1]))
key = sys.argv[2]
entries = d.get("plugins", {}).get(key, [])
if not entries:
    print(" ")
else:
    e = entries[0]
    print(f"{e.get('version','?')} {e.get('installPath','?')}")
PYEOF
)

if [ -z "${OLD_VER:-}" ] || [ "$OLD_VER" = "?" ]; then
  say "${KEY}: not installed in installed_plugins.json — run /plugin install ${KEY} first"
  exit 1
fi

NEW_CACHE="${CACHE_BASE}/${NEW_VER}"

if [ "$OLD_VER" = "$NEW_VER" ] && [ "$OLD_PATH" = "$NEW_CACHE" ] && [ -d "$NEW_CACHE" ]; then
  log "${KEY}: already at v${NEW_VER} with installPath synced — nothing to do"
  exit 0
fi

say "${KEY}: v${OLD_VER} → v${NEW_VER}"
say "  old installPath: $OLD_PATH"
say "  new installPath: $NEW_CACHE"

if [ "$DRY" -eq 1 ]; then
  say "[dry-run] would copy marketplace → $NEW_CACHE"
  say "[dry-run] would patch $INSTALLED entry $KEY"
  [ "$KEEP_OLD" -eq 0 ] && say "[dry-run] would prune old cache: $OLD_PATH"
  exit 0
fi

# ─── populate new cache dir ──────────────────────────────────────────────

mkdir -p "$NEW_CACHE"
# tar pipe = portable copy with excludes (no rsync dep)
tar -c -C "$MARKET_DIR" \
    --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' \
    . | tar -x -C "$NEW_CACHE"
say "  copied marketplace → $NEW_CACHE"

# ─── patch installed_plugins.json (atomic) ───────────────────────────────

python3 - "$INSTALLED" "$KEY" "$NEW_VER" "$NEW_CACHE" "$NEW_HEAD" <<'PYEOF'
import json, os, sys, tempfile
from datetime import datetime, timezone

path, key, ver, install_path, sha = sys.argv[1:6]
d = json.load(open(path))
entries = d.setdefault("plugins", {}).get(key, [])
if not entries:
    print(f"ERROR: key {key} disappeared", file=sys.stderr)
    sys.exit(1)
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"
entries[0]["version"] = ver
entries[0]["installPath"] = install_path
entries[0]["lastUpdated"] = now
entries[0]["gitCommitSha"] = sha

# Atomic write
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".installed_plugins.", suffix=".tmp")
try:
    with os.fdopen(fd, "w") as f:
        json.dump(d, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
except Exception:
    try: os.unlink(tmp)
    except FileNotFoundError: pass
    raise
print(f"patched {path}: version={ver}, installPath={install_path}")
PYEOF

# ─── prune old cache dir ─────────────────────────────────────────────────

if [ "$KEEP_OLD" -eq 0 ] && [ -n "$OLD_PATH" ] && [ "$OLD_PATH" != "$NEW_CACHE" ] && [ -d "$OLD_PATH" ]; then
  # Safety: only prune dirs under our cache base
  case "$OLD_PATH" in
    "${CACHE_BASE}/"*)
      rm -rf "$OLD_PATH"
      say "  pruned old cache: $OLD_PATH"
      ;;
    *)
      say "  WARN: refusing to prune $OLD_PATH (not under $CACHE_BASE)"
      ;;
  esac
fi

# ─── done ────────────────────────────────────────────────────────────────

say ""
say "${KEY} upgraded to v${NEW_VER}"
say ""
say "ACTION REQUIRED:"
say "  In an active Claude Code session, run: /reload-plugins"
say "  Or restart Claude Code to fully reload bundled hooks."
