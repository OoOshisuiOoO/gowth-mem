#!/bin/bash
# PreCompact: raw-dump the recent human turns into <ws>/journal/<today>.md
# (precompact-flush.py, v3.5.1: NEVER blocks /compact — exits 0 with empty
# stdout on every path), then a commit-only sync so the dump is in git before
# the context is gone. v4.8: the dump keeps the user's own turns only
# (_capture classification) and caps assistant chunks; it is no longer part of
# any bootstrap — MEMORY.md carries the working set.

# v4.9: GOWTH_MEM_OFF=1|true|yes|on (any case) disables every gowth-mem hook.
# Checked FIRST: drain the event, no output, no vault access, exit 0.
case "${GOWTH_MEM_OFF:-}" in
    1|[Tt][Rr][Uu][Ee]|[Yy][Ee][Ss]|[Oo][Nn]) [ -t 0 ] || cat >/dev/null 2>&1; exit 0 ;;
esac

INPUT=$(cat)

SCRIPTS_DIR="$(dirname "$0")"

python3 "$SCRIPTS_DIR/precompact-flush.py" <<<"$INPUT"

# Always attempt commit-only sync regardless of flush result
python3 "$SCRIPTS_DIR/auto-sync.py" --commit-only --quiet

# v4.9: a hook entrypoint always exits 0 — /compact must never be blockable
# (the flush's own status was passed through; a crashed interpreter would
# have surfaced as a failing PreCompact hook).
exit 0
