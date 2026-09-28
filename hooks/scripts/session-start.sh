#!/bin/bash
# SessionStart: merged bootstrap-load + auto-sync --pull-only + registry self-heal.
# Always pulls (background). Runs bootstrap on startup/compact sources.

INPUT=$(cat)

SCRIPTS_DIR="$(dirname "$0")"

# Parse source field without jq dependency
SOURCE=$(python3 -c "import sys,json; print(json.load(sys.stdin).get('source',''))" <<<"$INPUT" 2>/dev/null || echo "")

# Always pull in background (cheap, non-blocking)
python3 "$SCRIPTS_DIR/auto-sync.py" --pull-only --quiet &
SYNC_PID=$!

# ── v4.7.2: background registry self-heal ────────────────────────────────
# Claude Code can leave installed_plugins.json pinned at an old cache dir after
# an upgrade (issue #52218; autoUpdate is off by default for non-Anthropic
# marketplaces). Nothing breaks loudly — the OLD plugin version just keeps
# executing against the shared vault. One live machine ran v3.9.0 for months
# after v4.7.1 shipped. bin/doctor.sh already detected and healed exactly this,
# but only when a human typed /mem-doctor, so it never ran.
#
# Detached + no --pull: zero network, never on the blocking startup path. The
# heal only takes effect on the NEXT start, which is why bootstrap-load.py also
# prints the running version and a restart nudge.
#
# Opt out: GOWTH_MEM_NO_AUTOHEAL=1, or "auto_heal": false in settings.json.
# The gate is pure bash (grep, no python startup) — cheap pre-check, per the
# hook-efficiency canon.
if [ "$SOURCE" = "startup" ] || [ -z "$SOURCE" ]; then
    if [ "${GOWTH_MEM_NO_AUTOHEAL:-0}" != "1" ]; then
        _GOWTH_SETTINGS="${GOWTH_MEM_HOME:-$HOME/.gowth-mem}/settings.json"
        if ! grep -q '"auto_heal"[[:space:]]*:[[:space:]]*false' "$_GOWTH_SETTINGS" 2>/dev/null; then
            _DOCTOR="$SCRIPTS_DIR/../../bin/doctor.sh"
            [ -f "$_DOCTOR" ] && ( bash "$_DOCTOR" --quiet >/dev/null 2>&1 & )
        fi
    fi
fi

# Bootstrap on startup, clear and compact (v4.8: /clear used to get nothing —
# a cleared conversation has no gowth-mem context otherwise). resume keeps its
# transcript and gets nothing. bootstrap-load.py reads source + cwd from stdin
# and decides native header vs fallback bootstrap.
if [ "$SOURCE" = "startup" ] || [ "$SOURCE" = "compact" ] || [ "$SOURCE" = "clear" ] || [ -z "$SOURCE" ]; then
    python3 "$SCRIPTS_DIR/bootstrap-load.py" <<<"$INPUT"
fi

wait $SYNC_PID
