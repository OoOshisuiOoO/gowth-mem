#!/bin/bash
# v4.8 real-binary E2E: does the model actually receive what gowth-mem sends?
#
# Runs `claude -p --setting-sources project` inside a scratch project whose
# .claude/settings.json registers THIS checkout's hooks and points
# autoMemoryDirectory at a scratch vault. The user's own plugins never run
# (--setting-sources project) and the real ~/.gowth-mem is never touched
# (GOWTH_MEM_HOME). Skips with exit 0 when `claude` is not on PATH.
#
# Steps (spec §7.2): 1 MEMORY.md block visible; 2 SessionStart header visible,
# < 9,000 chars, no persist notice; 3 per-prompt recall injects for a seeded
# query and stays silent for a generic prompt; 4 compact delta (hook replay)
# < 1,500 chars with the handoff; 5 Stop directive replay carries the judge
# contract. Prints PASS/FAIL per step; exit 1 on any FAIL.
set -u
if ! command -v claude >/dev/null 2>&1; then
  echo "SKIP: claude binary not on PATH"; exit 0
fi
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPTS="$REPO/hooks/scripts"
E="${GOWTH_E2E_DIR:-${TMPDIR:-/tmp}/gowth-e2e-$$}"
rm -rf "$E"; mkdir -p "$E/proj/.claude" "$E/vault/shared" "$E/vault/workspaces/demo/docs" \
  "$E/vault/workspaces/demo/journal" "$E/vault/workspaces/demo/t" "$E/vault/workspaces/demo/memory"
V="$E/vault"; P="$E/proj"
export GOWTH_MEM_HOME="$V" GOWTH_MEM_NO_AUTOHEAL=1 AI_AGENT="claude-code_2-1-283_harness"
unset GOWTH_WORKSPACE CLAUDE_SUBAGENT

# --- scratch vault -----------------------------------------------------------
echo '{"name": "demo"}' > "$V/workspaces/demo/workspace.json"
printf '## 2026-09-13 snapshot\n- host:e2e 2026-09-13 [blocker] waiting on the vault token\n- host:e2e 2026-09-13 [done] shipped v4\n' > "$V/workspaces/demo/docs/handoff.md"
printf -- '---\nslug: t\ntitle: T\ntype: topic\nlast_touched: 2026-09-12\n---\n# T\n\nIngress topic.\n' > "$V/workspaces/demo/t/00-README.md"
printf -- '---\nslug: t-a\n---\n## [decision] Use pelican-router for the ingress gateway\n\nBecause pelican-router terminates gRPC at the edge. Rationale: fewer hops.\n' > "$V/workspaces/demo/t/2026-09-10-a.md"
printf -- '- `FAKE_KEY`\n' > "$V/shared/secrets.md"
printf '{"layout_version": 3, "recall": {"on_prompt_score_threshold": 0.0}}\n' > "$V/settings.json"
printf '{"workspace_map": {"%s/**": "demo"}}\n' "$P" > "$V/config.json"
python3 "$SCRIPTS/_index.py" >/dev/null || { echo "FAIL: index build"; exit 1; }
python3 "$SCRIPTS/_memfile.py" --ws demo >/dev/null || { echo "FAIL: memfile write"; exit 1; }

# --- scratch project: this checkout's hooks + autoMemoryDirectory ----------
cat > "$P/.claude/settings.json" <<JSON
{
  "autoMemoryDirectory": "$V/workspaces/demo/memory",
  "hooks": {
    "SessionStart": [{"matcher": "*", "hooks": [{"type": "command", "command": "bash $SCRIPTS/session-start.sh"}]}],
    "UserPromptSubmit": [{"matcher": "*", "hooks": [
      {"type": "command", "command": "bash $SCRIPTS/conflict-detect.sh"},
      {"type": "command", "command": "bash $SCRIPTS/recall-on-prompt.sh"}]}]
  }
}
JSON
cd "$P" || exit 1
fails=0
ask() { claude -p --setting-sources project --output-format text "$1" 2>/dev/null </dev/null | tail -n 3; }

# 1. MEMORY.md block visible
r=$(ask 'Look at the auto-memory MEMORY.md content in your context. Quote verbatim the line that starts with "[gowth-mem:bootstrap workspace=" and then, on a second line, the exact heading of the LAST "## " section inside the gowth-mem block. Nothing else.')
if printf '%s' "$r" | grep -q 'gowth-mem:bootstrap workspace=demo' && printf '%s' "$r" | grep -q 'Using memory'; then echo "PASS 1 MEMORY.md block delivered"; else echo "FAIL 1 MEMORY.md block: $r"; fails=$((fails+1)); fi

# 2. SessionStart header visible, small, not persisted
r=$(ask 'At the start of this session a SessionStart hook added context that begins with "[gowth-mem:bootstrap". Reply with ONLY that context, copied verbatim, no commentary. If instead you only see a notice like "Output too large … saved to", reply with the single word PERSISTED.')
if printf '%s' "$r" | grep -q 'gowth-mem:bootstrap workspace=demo' && ! printf '%s' "$r" | grep -qi 'PERSISTED\|Output too large' && printf '%s' "$r" | grep -q 'MEMORY.md'; then echo "PASS 2 SessionStart header visible"; else echo "FAIL 2 header: $r"; fails=$((fails+1)); fi
hdr=$(printf '{"source":"startup","cwd":"%s","session_id":"e2e"}' "$P" | python3 "$SCRIPTS/bootstrap-load.py" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["hookSpecificOutput"]["additionalContext"]))')
if [ "$hdr" -lt 9000 ]; then echo "PASS 2b header ${hdr} chars < 9000"; else echo "FAIL 2b header ${hdr} chars"; fails=$((fails+1)); fi

# 3. per-prompt recall: seeded query injects, generic prompt stays silent
r=$(ask 'Why did we choose pelican-router for the ingress gateway, and what was the rationale? Do not answer the question. Instead reply with ONLY the hook-injected context that starts with "[gowth-mem:recall", copied verbatim (all of its lines); if no such context was injected for this prompt, reply with the single word NONE.')
if printf '%s' "$r" | grep -q 'gowth-mem:recall' && printf '%s' "$r" | grep -q '2026-09-10-a.md'; then echo "PASS 3a recall injected for the seeded query"; else echo "FAIL 3a recall: $r"; fails=$((fails+1)); fi
r=$(ask 'Please continue with the next step of the plan and keep going until done. Before doing anything: if a hook injected context whose first line starts with "[gowth-mem:recall", quote it; otherwise reply exactly NONE.')
if printf '%s' "$r" | grep -q 'NONE' && ! printf '%s' "$r" | grep -q 'gowth-mem:recall'; then echo "PASS 3b generic prompt got no recall"; else echo "FAIL 3b generic: $r"; fails=$((fails+1)); fi

# 4. compact delta (hook replay)
comp=$(printf '{"source":"compact","cwd":"%s","session_id":"e2e"}' "$P" | python3 "$SCRIPTS/bootstrap-load.py" | python3 -c 'import json,sys; c=json.load(sys.stdin)["hookSpecificOutput"]["additionalContext"]; print(len(c)); print("HANDOFF" if "## Handoff" in c and "[blocker]" in c else "NOHANDOFF")')
n=$(printf '%s' "$comp" | sed -n 1p); h=$(printf '%s' "$comp" | sed -n 2p)
if [ "$n" -lt 1500 ] && [ "$h" = "HANDOFF" ]; then echo "PASS 4 compact delta ${n} chars with handoff"; else echo "FAIL 4 compact: $comp"; fails=$((fails+1)); fi

# 5. Stop directive replay carries the judge contract
mkdir -p "$V/workspaces/demo/journal/sessions"
log="$V/workspaces/demo/journal/sessions/$(date +%Y-%m-%d)-e2estop1.md"
{ printf '# Session log\n\n'; for i in $(seq 1 12); do printf '## turn %s — 10:0%s\n**User:** ask %s\n**Claude:** answer %s\n\n' "$i" "$((i%10))" "$i" "$i"; done; } > "$log"
python3 - "$V/state.json" <<'PY'
import json,sys,os
p=sys.argv[1]; st=json.load(open(p)) if os.path.isfile(p) else {}
st.setdefault("session",{})["e2estop1"]={"turn_count":1,"review_count":14,"total_turns":14}
open(p,"w").write(json.dumps(st))
PY
printf '{"session_id":"e2estop1","cwd":"%s","hook_event_name":"Stop","stop_hook_active":false}' "$P" > "$E/stop.json"
so=$(python3 "$SCRIPTS/auto-journal.py" < "$E/stop.json")
if printf '%s' "$so" | grep -q 'exactly 3 lines' && printf '%s' "$so" | grep -q 'dispatched gowth-mem judge'; then echo "PASS 5 judge directive carries the 3-line contract"; else echo "FAIL 5 stop: $so"; fails=$((fails+1)); fi

echo "e2e scratch: $E"
if [ "$fails" -eq 0 ]; then echo "ALL PASS"; exit 0; fi
echo "FAILURES: $fails"; exit 1
