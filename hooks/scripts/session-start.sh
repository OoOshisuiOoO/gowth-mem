#!/bin/bash
# SessionStart: merged bootstrap-load + auto-sync --pull-only.
# Always pulls (background). Runs bootstrap on startup/compact sources.

INPUT=$(cat)

SCRIPTS_DIR="$(dirname "$0")"

# Parse source field without jq dependency
SOURCE=$(python3 -c "import sys,json; print(json.load(sys.stdin).get('source',''))" <<<"$INPUT" 2>/dev/null || echo "")

# Always pull in background (cheap, non-blocking)
python3 "$SCRIPTS_DIR/auto-sync.py" --pull-only --quiet &
SYNC_PID=$!

# Only run bootstrap on startup or compact sources
if [ "$SOURCE" = "startup" ] || [ "$SOURCE" = "compact" ] || [ -z "$SOURCE" ]; then
    python3 "$SCRIPTS_DIR/bootstrap-load.py" <<<"$INPUT"
fi

wait $SYNC_PID
