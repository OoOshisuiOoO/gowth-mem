#!/bin/bash
# UserPromptSubmit pre-check (v4.8): decide in bash whether per-prompt recall
# is worth a Python start. Silent exit 0 when:
#   - settings.json has "on_prompt_enabled": false
#   - the prompt starts with "/" (slash command) or "!" (shell), or is < 8 bytes
#   - the event is a subagent / loop (agent_type, in_loop, CLAUDE_SUBAGENT)
#   - v4.9: GOWTH_MEM_OFF=1|true|yes|on (checked first; the event is drained)
# Otherwise hand the event to _recall_prompt.py (which may still print nothing;
# it also skips bot/pipeline-marked prompts — the exact markers need Python).
#
# v4.9 short prompts (spec §6.1): the 40-byte floor applies to BM25 only. A
# prompt of 8-39 bytes starts `_recall_prompt.py --card-only` ONLY when it
# names a carded topic: `grep -qiwF -f <vault>/.curator/cards-terms-<ws>`
# (the machine-local list of every name a card can be matched by, written by
# `_card.py --set/--summary` and by the recall hook). No terms file, a
# `"card_enabled": false` knob, or no match → no Python.
# <ws> is resolved here for the two cheap, exact sources Python also checks
# first: GOWTH_WORKSPACE, then <vault>/.session-workspace (both trimmed and
# validated as `^[a-z0-9][a-z0-9._-]{0,63}$`). The remaining rules —
# config.json workspace_map globs against the event cwd, active_workspace,
# settings workspace.default — need a JSON parser and glob matching, which bash
# 3.2 cannot do cheaply or safely; then every cards-terms-* file is checked (a
# superset: Python resolves the workspace and matches exactly, so a false
# positive only costs a Python start, never an injection).

case "${GOWTH_MEM_OFF:-}" in
    1|[Tt][Rr][Uu][Ee]|[Yy][Ee][Ss]|[Oo][Nn]) [ -t 0 ] || cat >/dev/null 2>&1; exit 0 ;;
esac

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

if [ "${#HEAD}" -lt 40 ]; then
    [ "${#HEAD}" -lt 8 ] && exit 0
    if grep -q '"card_enabled"[[:space:]]*:[[:space:]]*false' "$GOWTH_HOME/settings.json" 2>/dev/null; then
        exit 0
    fi
    TERMS_DIR="$GOWTH_HOME/.curator"
    [ -d "$TERMS_DIR" ] || exit 0
    trim() {
        local s="$1"
        s="${s#"${s%%[![:space:]]*}"}"
        s="${s%"${s##*[![:space:]]}"}"
        printf '%s' "$s"
    }
    WS=$(trim "${GOWTH_WORKSPACE:-}")
    if [ -z "$WS" ] && [ -f "$GOWTH_HOME/.session-workspace" ]; then
        WS=$(trim "$(head -c 256 "$GOWTH_HOME/.session-workspace" 2>/dev/null)")
    fi
    if [ -n "$WS" ]; then
        # Python would use this name as is; one that is not a workspace name has no cards
        case "$WS" in
            *[!abcdefghijklmnopqrstuvwxyz0123456789._-]*|[!abcdefghijklmnopqrstuvwxyz0123456789]*) exit 0 ;;
        esac
        [ "${#WS}" -le 64 ] || exit 0
        set -- "$TERMS_DIR/cards-terms-$WS"
    else
        set -- "$TERMS_DIR"/cards-terms-*
    fi
    # the same single-spaced text Python matches: JSON escapes (\n \t \" \uXXXX …) → spaces
    TEXT=${HEAD//\\u[0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F]/ }
    TEXT=${TEXT//\\?/ }
    while [ "${TEXT#*  }" != "$TEXT" ]; do TEXT=${TEXT//  / }; done
    for TERMS in "$@"; do
        [ -f "$TERMS" ] && [ -s "$TERMS" ] || continue
        if grep -qiwF -f "$TERMS" <<<"$TEXT" 2>/dev/null; then
            exec python3 "$(dirname "$0")/_recall_prompt.py" --card-only <<<"$INPUT"
        fi
    done
    exit 0
fi

exec python3 "$(dirname "$0")/_recall_prompt.py" <<<"$INPUT"
