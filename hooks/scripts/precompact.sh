#!/bin/bash
# PreCompact: raw-dump the recent human turns into <ws>/journal/<today>.md
# (precompact-flush.py, v3.5.1: NEVER blocks /compact — exits 0 with empty
# stdout on every path), then a commit-only sync so the dump is in git before
# the context is gone. v4.8: the dump keeps the user's own turns only
# (_capture classification) and caps assistant chunks; it is no longer part of
# any bootstrap — MEMORY.md carries the working set.

INPUT=$(cat)

SCRIPTS_DIR="$(dirname "$0")"

python3 "$SCRIPTS_DIR/precompact-flush.py" <<<"$INPUT"
FLUSH_EXIT=$?

# Always attempt commit-only sync regardless of flush result
python3 "$SCRIPTS_DIR/auto-sync.py" --commit-only --quiet

exit $FLUSH_EXIT
