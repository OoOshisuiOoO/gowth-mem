#!/bin/bash
# UserPromptSubmit pre-check: only invoke Python if SYNC-CONFLICT.md exists.
# Win: zero Python startup cost on 99% of prompts.

# v4.9: GOWTH_MEM_OFF=1|true|yes|on (any case) disables every gowth-mem hook.
# Checked FIRST: drain the event, no output, no vault access, exit 0.
case "${GOWTH_MEM_OFF:-}" in
    1|[Tt][Rr][Uu][Ee]|[Yy][Ee][Ss]|[Oo][Nn]) [ -t 0 ] || cat >/dev/null 2>&1; exit 0 ;;
esac

INPUT=$(cat)

# Resolve gowth-mem home: env override else ~/.gowth-mem
if [ -n "$GOWTH_MEM_HOME" ]; then
    GOWTH_HOME="$GOWTH_MEM_HOME"
else
    GOWTH_HOME="$HOME/.gowth-mem"
fi

if [ ! -f "$GOWTH_HOME/SYNC-CONFLICT.md" ]; then
    exit 0
fi

exec python3 "$(dirname "$0")/conflict-detect.py" <<<"$INPUT"
