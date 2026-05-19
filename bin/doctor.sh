#!/usr/bin/env bash
# Self-heal gowth-mem registration in installed_plugins.json.
#
# Detects two failure modes that both manifest as "hooks silently skipped":
#   1. Claude Code bug #52218 — autoUpdate bumps version metadata in
#      installed_plugins.json but leaves installPath pointing at a missing
#      cache dir (e.g. cache/<m>/<p>/<old>/ already pruned).
#   2. Dev-link override — installPath set to an absolute path outside
#      $HOME/.claude/plugins/cache/<m>/<p>/<v>/ (e.g. a local git clone),
#      which breaks portability across machines.
#
# Action: materialize the canonical cache dir from the marketplace clone
# (~/.claude/plugins/marketplaces/<m>/) and atomically rewrite the registry
# entry to point there.
#
# Idempotent. Always exits 0 — must NOT break hook chains. All chatter goes
# to stderr; stdout stays empty so this is safe to call from a hook.
#
# Usage:
#   bin/doctor.sh                              # default: gowth-mem/gowth-mem
#   bin/doctor.sh --quiet                      # silent unless heal applied
#   bin/doctor.sh --dry-run                    # show what would change
#   bin/doctor.sh --pull                       # git fetch + ff-only pull marketplace clone first
#   bin/doctor.sh --market <m> --plugin <p>    # heal a different plugin

set -uo pipefail

MARKET="gowth-mem"
PLUGIN="gowth-mem"
QUIET=0
DRY=0
PULL=0
# Exit code semantics for env-validation failures (accumulated, highest wins):
#   1 = gowth-mem home missing (needs /mem-install)
#   2 = python3 missing or too old
#   3 = git missing
ENV_EXIT=0

while [ $# -gt 0 ]; do
  case "$1" in
    --quiet)   QUIET=1; shift ;;
    --dry-run) DRY=1;   shift ;;
    --pull)    PULL=1;  shift ;;
    --market)  MARKET="${2:-}"; shift 2 ;;
    --plugin)  PLUGIN="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "[doctor] unknown arg: $1" >&2; exit 0 ;;
  esac
done

CC_ROOT="${HOME}/.claude/plugins"
MARKET_DIR="${CC_ROOT}/marketplaces/${MARKET}"
CACHE_BASE="${CC_ROOT}/cache/${MARKET}/${PLUGIN}"
INSTALLED="${CC_ROOT}/installed_plugins.json"
KEY="${PLUGIN}@${MARKET}"

log()  { [ "$QUIET" -eq 0 ] && echo "[doctor:${KEY}] $*" >&2 || true; }
say()  { echo "[doctor:${KEY}] $*" >&2; }
warn() { echo "[doctor:${KEY}] WARN: $*" >&2; }

# ─── env validation ──────────────────────────────────────────────────────
# 1. gowth-mem home directory
GOWTH_HOME="${GOWTH_MEM_HOME:-${HOME}/.gowth-mem}"
if [ ! -d "$GOWTH_HOME" ]; then
  warn "gowth-mem home not found: $GOWTH_HOME"
  warn "  → Run /mem-install to create it."
  ENV_EXIT=1
else
  log "gowth-mem home: $GOWTH_HOME ✓"
fi

# 2. Python 3.9+
if ! command -v python3 >/dev/null 2>&1; then
  warn "python3 not found on PATH"
  warn "  → Install Python 3.9+ (https://python.org/downloads) and ensure it is on PATH."
  [ "$ENV_EXIT" -lt 2 ] && ENV_EXIT=2
else
  PY_VER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "unknown")"
  PY_MAJOR="$(python3 -c 'import sys; print(sys.version_info[0])' 2>/dev/null || echo "0")"
  PY_MINOR="$(python3 -c 'import sys; print(sys.version_info[1])' 2>/dev/null || echo "0")"
  if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 9 ]; }; then
    warn "python3 found but version $PY_VER is below the required 3.9"
    warn "  → Upgrade to Python 3.9+ (https://python.org/downloads)."
    [ "$ENV_EXIT" -lt 2 ] && ENV_EXIT=2
  else
    log "python3: $PY_VER ✓"
  fi
fi

# 3. git
if ! command -v git >/dev/null 2>&1; then
  warn "git not found on PATH"
  warn "  → Install git (https://git-scm.com/downloads)."
  [ "$ENV_EXIT" -lt 3 ] && ENV_EXIT=3
else
  GIT_VER="$(git --version 2>/dev/null | awk '{print $3}')"
  log "git: ${GIT_VER:-unknown} ✓"
fi

# 4. Stale user-level PreCompact hook (pre-v3.5 artifact)
#
# Some users installed `~/.claude/hooks/precompact-force-memsave.sh` while
# following old docs. Plugin v3.5+ does NOT need (or want) it — the plugin's
# own precompact-flush.py now handles compaction without blocking. The stale
# hook chains in front and HARD-BLOCKs every /compact until a `mem-save`
# happens. Detect + advise (we don't auto-delete user files).
STALE_HOOK="${HOME}/.claude/hooks/precompact-force-memsave.sh"
STALE_SETTINGS="${HOME}/.claude/settings.json"
SETTINGS_HAS_STALE=0
if [ -f "$STALE_SETTINGS" ] && grep -q "precompact-force-memsave" "$STALE_SETTINGS" 2>/dev/null; then
  SETTINGS_HAS_STALE=1
fi
if [ -f "$STALE_HOOK" ] || [ "$SETTINGS_HAS_STALE" -eq 1 ]; then
  warn "stale pre-v3.5 PreCompact hook detected — blocks /compact and auto-compact"
  warn "  Plugin v3.5+ handles pre-compact memory dump itself; no global hook needed."
  if [ -f "$STALE_HOOK" ]; then
    warn "  Step 1 — delete the script:"
    warn "    rm \"$STALE_HOOK\""
  fi
  if [ "$SETTINGS_HAS_STALE" -eq 1 ]; then
    warn "  Step 2 — surgically remove just the stale hook entry from $STALE_SETTINGS:"
    warn "    python3 - <<'PY'"
    warn "    import json, pathlib"
    warn "    p = pathlib.Path(\"$STALE_SETTINGS\")"
    warn "    d = json.loads(p.read_text())"
    warn "    hooks = d.get('hooks', {})"
    warn "    pc = hooks.get('PreCompact', [])"
    warn "    new_pc = []"
    warn "    for group in pc:"
    warn "        kept = [h for h in group.get('hooks', []) if 'precompact-force-memsave' not in h.get('command','')]"
    warn "        if kept:"
    warn "            group['hooks'] = kept"
    warn "            new_pc.append(group)"
    warn "    if new_pc:"
    warn "        hooks['PreCompact'] = new_pc"
    warn "    else:"
    warn "        hooks.pop('PreCompact', None)"
    warn "    p.write_text(json.dumps(d, indent=2) + '\\n')"
    warn "    PY"
  fi
fi

# Surface env failures before plugin-registry checks
if [ "$ENV_EXIT" -ne 0 ]; then
  exit "$ENV_EXIT"
fi

# ─── preflight (silent unless something is genuinely wrong) ─────────────
[ -f "$INSTALLED" ] || exit 0
[ -d "$MARKET_DIR/.claude-plugin" ] || { log "marketplace clone missing → skip"; exit 0; }
command -v python3 >/dev/null 2>&1 || { warn "python3 not on PATH"; exit 0; }

# ─── optional: pull marketplace clone to latest ─────────────────────────
# Lets `doctor.sh` work even on machines whose Claude Code autoUpdate hasn't
# fired yet. Network errors / dirty tree / non-ff branch → fall back silently
# to whatever the local marketplace clone currently has.
if [ "$PULL" -eq 1 ] && [ -d "$MARKET_DIR/.git" ]; then
  if git -C "$MARKET_DIR" fetch --quiet origin 2>/dev/null; then
    BRANCH="$(git -C "$MARKET_DIR" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's@^origin/@@')"
    [ -n "$BRANCH" ] || BRANCH=main
    if ! git -C "$MARKET_DIR" pull --ff-only --quiet origin "$BRANCH" 2>/dev/null; then
      log "fetch OK but ff-only pull skipped (working tree dirty or non-ff)"
    fi
  else
    log "fetch failed (network?), continuing with local marketplace state"
  fi
fi

# ─── read marketplace version ───────────────────────────────────────────
NEW_VER="$(python3 - "$MARKET_DIR/.claude-plugin/plugin.json" <<'PY' 2>/dev/null
import json, sys
try: print(json.load(open(sys.argv[1]))["version"])
except Exception: pass
PY
)"
[ -n "$NEW_VER" ] || { warn "cannot read marketplace plugin.json version"; exit 0; }

# ─── read current registry entry ────────────────────────────────────────
REG_OUT="$(python3 - "$INSTALLED" "$KEY" <<'PY' 2>/dev/null
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    e = d.get("plugins", {}).get(sys.argv[2], [])
    if e:
        print(f"{e[0].get('version','')}\t{e[0].get('installPath','')}")
except Exception: pass
PY
)"
if [ -z "$REG_OUT" ]; then
  log "not registered in $INSTALLED → skip"
  exit 0
fi
OLD_VER="$(printf '%s' "$REG_OUT" | cut -f1)"
OLD_PATH="$(printf '%s' "$REG_OUT" | cut -f2)"

EXPECTED_CACHE="${CACHE_BASE}/${NEW_VER}"

# ─── decide whether to heal ─────────────────────────────────────────────
NEEDS_HEAL=0
REASONS=""

case "$OLD_PATH" in
  "${CACHE_BASE}/"*) ;;
  *) NEEDS_HEAL=1; REASONS="installPath not under ${CACHE_BASE}/" ;;
esac

if [ ! -d "$OLD_PATH" ] || [ ! -f "$OLD_PATH/.claude-plugin/plugin.json" ]; then
  NEEDS_HEAL=1
  REASONS="${REASONS:+$REASONS; }installPath missing or incomplete"
fi

if [ "$OLD_VER" != "$NEW_VER" ] || [ "$OLD_PATH" != "$EXPECTED_CACHE" ]; then
  NEEDS_HEAL=1
  REASONS="${REASONS:+$REASONS; }registry stale (v${OLD_VER:-?} → v${NEW_VER})"
fi

if [ "$NEEDS_HEAL" -eq 0 ]; then
  exit 0   # healthy, silent
fi

# heal actions are always announced (even with --quiet) — they're rare and visible.
say "healing — ${REASONS}"
say "  from: v${OLD_VER:-?}@${OLD_PATH:-?}"
say "    to: v${NEW_VER}@${EXPECTED_CACHE}"

if [ "$DRY" -eq 1 ]; then
  say "[dry-run] would materialize cache + patch registry"
  exit 0
fi

# ─── materialize cache (skip if already populated) ──────────────────────
if [ ! -f "$EXPECTED_CACHE/.claude-plugin/plugin.json" ]; then
  if ! mkdir -p "$EXPECTED_CACHE" 2>/dev/null; then
    warn "mkdir failed: $EXPECTED_CACHE"
    exit 0
  fi
  # tar-pipe: portable copy with excludes (no rsync dep)
  # Exclude dev artifacts that confuse Claude Code plugin discovery
  if ! tar -c -C "$MARKET_DIR" \
        --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' \
        --exclude='.claude' --exclude='.omc' --exclude='.github' \
        . | tar -x -C "$EXPECTED_CACHE"; then
    warn "cache copy failed"
    exit 0
  fi
  say "  materialized $EXPECTED_CACHE"
fi

# ─── patch installed_plugins.json (atomic) ──────────────────────────────
NEW_HEAD="$(git -C "$MARKET_DIR" rev-parse HEAD 2>/dev/null || echo "")"

if ! python3 - "$INSTALLED" "$KEY" "$NEW_VER" "$EXPECTED_CACHE" "$NEW_HEAD" <<'PY'
import json, os, sys, tempfile
from datetime import datetime, timezone

path, key, ver, install_path, sha = sys.argv[1:6]
try:
    d = json.load(open(path))
except Exception as e:
    print(f"json load failed: {e}", file=sys.stderr)
    sys.exit(1)

entries = d.setdefault("plugins", {}).get(key, [])
if not entries:
    print("entry disappeared mid-heal", file=sys.stderr)
    sys.exit(1)

now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"
entries[0]["version"]     = ver
entries[0]["installPath"] = install_path
entries[0]["lastUpdated"] = now
if sha:
    entries[0]["gitCommitSha"] = sha

fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path),
                           prefix=".installed_plugins.", suffix=".tmp")
try:
    with os.fdopen(fd, "w") as f:
        json.dump(d, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
except Exception:
    try: os.unlink(tmp)
    except FileNotFoundError: pass
    raise
PY
then
  warn "registry patch failed"
  exit 0
fi

say "  patched $INSTALLED"
say "  restart Claude Code (or /reload-plugins) so the new installPath takes effect"
exit 0
