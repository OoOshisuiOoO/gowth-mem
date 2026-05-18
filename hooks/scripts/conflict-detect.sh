#!/bin/bash
# UserPromptSubmit pre-check: only invoke Python if SYNC-CONFLICT.md exists.
# Win: zero Python startup cost on 99% of prompts.

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
