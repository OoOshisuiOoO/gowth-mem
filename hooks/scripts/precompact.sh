#!/bin/bash
# PreCompact: run precompact-flush (may HARD-BLOCK with exit 2), then commit-only sync.
# Exit code from flush is preserved so HARD-BLOCK semantics work correctly.

INPUT=$(cat)

SCRIPTS_DIR="$(dirname "$0")"

# Run flush; capture exit code (exit 2 = HARD-BLOCK, exit 0 = pass-through)
python3 "$SCRIPTS_DIR/precompact-flush.py" <<<"$INPUT"
FLUSH_EXIT=$?

# Always attempt commit-only sync regardless of flush result
python3 "$SCRIPTS_DIR/auto-sync.py" --commit-only --quiet

# Preserve flush exit code so HARD-BLOCK propagates to Claude Code
exit $FLUSH_EXIT
