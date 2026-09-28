#!/bin/bash
# UserPromptSubmit pre-check (v4.8): decide in bash whether per-prompt recall
# is worth a Python start. Silent exit 0 when:
#   - settings.json has "on_prompt_enabled": false
#   - the prompt starts with "/" (slash command) or "!" (shell), or is < 40 bytes
#   - the event is a subagent / loop (agent_type, in_loop, CLAUDE_SUBAGENT)
# Otherwise hand the event to _recall_prompt.py (which may still print nothing).

INPUT=$(cat)
[ -z "$INPUT" ] && exit 0

if [ -n "$GOWTH_MEM_HOME" ]; then
    GOWTH_HOME="$GOWTH_MEM_HOME"
else
    GOWTH_HOME="$HOME/.gowth-mem"
fi
[ -d "$GOWTH_HOME" ] || exit 0

# opt-out knob (flat key under "recall" so a nested key never fools grep)
if grep -q '"on_prompt_enabled"[[:space:]]*:[[:space:]]*false' "$GOWTH_HOME/settings.json" 2>/dev/null; then
    exit 0
fi

# subagent / loop signals (same as the Stop hook's skip)
[ -n "${CLAUDE_SUBAGENT:-}" ] && exit 0
if printf '%s' "$INPUT" | grep -q '"agent_type"[[:space:]]*:[[:space:]]*"subagent"'; then exit 0; fi
if printf '%s' "$INPUT" | grep -q '"in_loop"[[:space:]]*:[[:space:]]*true'; then exit 0; fi

# the prompt's JSON string (escaped quotes \" and other \x pairs stay inside it —
# review M2: stopping at the first quote made every quoted prompt look < 40 bytes).
# Pure BRE (BSD sed has no \| ): runs of non-quote/non-backslash chars, each
# backslash consumed with the char it escapes. Then the first 200 chars.
HEAD=$(printf '%s' "$INPUT" | sed -n 's/.*"prompt"[[:space:]]*:[[:space:]]*"\([^"\\]*\(\\.[^"\\]*\)*\)".*/\1/p' | head -n 1)
HEAD=${HEAD:0:200}
[ -z "$HEAD" ] && exit 0
case "$HEAD" in
    /*|!*) exit 0 ;;
esac
[ "${#HEAD}" -lt 40 ] && exit 0

exec python3 "$(dirname "$0")/_recall_prompt.py" <<<"$INPUT"
