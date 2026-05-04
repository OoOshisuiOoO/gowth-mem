#!/usr/bin/env bash
# Auto-upgrade gowth-mem on this machine.
# Pulls the marketplace clone; if remote has new commits, ask Claude Code to update.
#
# Usage:
#   bin/auto-upgrade.sh         # check + pull + report
#   bin/auto-upgrade.sh --quiet # silent unless update applied (good for cron)
#
# Suggested cron (daily at 9am):
#   0 9 * * * /Volumes/Data/Git/bot/openclaw-bridge/bin/auto-upgrade.sh --quiet >> ~/.gowth-mem/upgrade.log 2>&1

set -euo pipefail

QUIET=0
[ "${1:-}" = "--quiet" ] && QUIET=1

MARKET="${HOME}/.claude/plugins/marketplaces/gowth-mem"
log() { [ "$QUIET" -eq 0 ] && echo "[gowth-mem-upgrade] $*" || true; }
say() { echo "[gowth-mem-upgrade] $*"; }

if [ ! -d "$MARKET/.git" ]; then
  say "ERROR: $MARKET not a git clone. Reinstall plugin via /plugin install gowth-mem@gowth-mem"
  exit 1
fi

cd "$MARKET"

# Fetch + compare HEADs
git fetch --quiet origin main
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" = "$REMOTE" ]; then
  log "already at latest ($LOCAL)"
  exit 0
fi

# Show what changed
say "update available: $LOCAL → $REMOTE"
git log --oneline "$LOCAL..$REMOTE" | head -10

# Pull
git pull --ff-only --quiet origin main
say "pulled to $(git rev-parse HEAD)"

# Read new version
NEW_VER=$(python3 -c "import json; print(json.load(open('.claude-plugin/plugin.json'))['version'])")
say "new plugin version: $NEW_VER"

# Tell user to restart Claude Code (or run /plugin update)
say ""
say "ACTION REQUIRED:"
say "  Restart Claude Code session, OR run inside session:"
say "  /plugin marketplace update gowth-mem"
