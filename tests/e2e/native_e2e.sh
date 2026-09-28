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
# query (model echo + state.json) and stays silent for a generic prompt
# (checked MECHANICALLY in state.json — review M5: the model's obedience is not
# evidence); 4 compact delta (hook replay) < 1,500 chars with the handoff;
# 5 Stop directive replay carries the judge contract; 6 the production wiring
# path: `_native.py wire` writes `~/…` into .claude/settings.local.json and a
# session started in a SUBDIRECTORY of the repo with --setting-sources
# project,local gets the block (review I5 + the "declined to judge" gap).
# The scratch lives under $HOME so the value is a `~/` path (removed at exit
# unless GOWTH_E2E_KEEP=1 or a step failed). Prints PASS/FAIL per step; exit 1
# on any FAIL.
set -u
if ! command -v claude >/dev/null 2>&1; then
  echo "SKIP: claude binary not on PATH"; exit 0
fi
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPTS="$REPO/hooks/scripts"
E="${GOWTH_E2E_DIR:-$HOME/.gowth-e2e-$$}"
rm -rf "$E"; mkdir -p "$E/proj/.claude" "$E/proj/sub" "$E/vault/shared" "$E/vault/workspaces/demo/docs" \
  "$E/vault/workspaces/demo/journal" "$E/vault/workspaces/demo/t" "$E/vault/workspaces/demo/memory"
V="$E/vault"; P="$E/proj"
fails=0
cleanup() { if [ "${GOWTH_E2E_KEEP:-0}" != "1" ] && [ "$fails" -eq 0 ]; then rm -rf "$E"; else echo "e2e scratch kept: $E"; fi; }
trap cleanup EXIT
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
ask() { claude -p --setting-sources project --output-format text "$1" 2>/dev/null </dev/null | tail -n 3; }
# same, with a fixed session id so the hook's state.json rows can be inspected
ask_sid() { claude -p --setting-sources "${3:-project}" --session-id "$2" --output-format text "$1" 2>/dev/null </dev/null | tail -n 3; }
state_q() { python3 - "$V/state.json" "$1" <<'PY'
import json, sys, datetime
st = json.load(open(sys.argv[1])) if __import__("os").path.isfile(sys.argv[1]) else {}
sid = sys.argv[2]; today = datetime.date.today().isoformat()
rec = (st.get("session") or {}).get(sid, {}).get("recall")
day = (st.get("recall_daily") or {}).get(today) or {}
print(f"injected={rec.get('injected', 0) if isinstance(rec, dict) else 0} prompts={day.get('prompts', 0)} day_injected={day.get('injected', 0)}")
PY
}

# 1. MEMORY.md block visible
r=$(ask 'Look at the auto-memory MEMORY.md content in your context. Quote verbatim the line that starts with "[gowth-mem:bootstrap workspace=" and then, on a second line, the exact heading of the LAST "## " section inside the gowth-mem block. Nothing else.')
if printf '%s' "$r" | grep -q 'gowth-mem:bootstrap workspace=demo' && printf '%s' "$r" | grep -q 'Using memory'; then echo "PASS 1 MEMORY.md block delivered"; else echo "FAIL 1 MEMORY.md block: $r"; fails=$((fails+1)); fi

# 2. SessionStart header visible, small, not persisted
r=$(ask 'At the start of this session a SessionStart hook added context that begins with "[gowth-mem:bootstrap". Reply with ONLY that context, copied verbatim, no commentary. If instead you only see a notice like "Output too large … saved to", reply with the single word PERSISTED.')
if printf '%s' "$r" | grep -q 'gowth-mem:bootstrap workspace=demo' && ! printf '%s' "$r" | grep -qi 'PERSISTED\|Output too large' && printf '%s' "$r" | grep -q 'MEMORY.md'; then echo "PASS 2 SessionStart header visible"; else echo "FAIL 2 header: $r"; fails=$((fails+1)); fi
hdr=$(printf '{"source":"startup","cwd":"%s","session_id":"e2e"}' "$P" | python3 "$SCRIPTS/bootstrap-load.py" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["hookSpecificOutput"]["additionalContext"]))')
if [ "$hdr" -lt 9000 ]; then echo "PASS 2b header ${hdr} chars < 9000"; else echo "FAIL 2b header ${hdr} chars"; fails=$((fails+1)); fi

# 3. per-prompt recall: seeded query injects (model echo + state.json), generic prompt stays silent (state.json)
# The seeded query is a NATURAL prompt (the gate needs >= 50% term coverage, so an
# instruction-laden prompt is rightly silent); the echo comes from a --resume turn.
sid_a=$(uuidgen | tr 'A-Z' 'a-z'); sid_b=$(uuidgen | tr 'A-Z' 'a-z')
ask_sid 'Why did we choose pelican-router for the ingress gateway, and what was the rationale?' "$sid_a" >/dev/null
sa=$(state_q "$sid_a")
r=$(claude -p --setting-sources project --resume "$sid_a" --output-format text 'Do not answer anything new. Quote verbatim the hook-injected context in my previous message that starts with "[gowth-mem:recall" (all of its lines); if there was none, reply with the single word NONE.' 2>/dev/null </dev/null | tail -n 3)
if printf '%s' "$r" | grep -q 'gowth-mem:recall' && printf '%s' "$r" | grep -q '2026-09-10-a.md' && printf '%s' "$sa" | grep -q 'injected=1'; then echo "PASS 3a recall injected for the seeded query and reached the model (${sa})"; else echo "FAIL 3a recall: ${r} | ${sa}"; fails=$((fails+1)); fi
before=$(state_q "$sid_b" | sed 's/.*prompts=\([0-9]*\).*/\1/')
ask_sid 'Please continue with the next step of the plan and keep going until done, then summarize what is left for tomorrow.' "$sid_b" >/dev/null
sb=$(state_q "$sid_b")
after=$(printf '%s' "$sb" | sed 's/.*prompts=\([0-9]*\).*/\1/')
if printf '%s' "$sb" | grep -q 'injected=0' && [ "${after}" -gt "${before}" ]; then echo "PASS 3b generic prompt got no recall (${sb}, profiled prompts ${before} -> ${after})"; else echo "FAIL 3b generic: ${sb} (prompts ${before} -> ${after})"; fails=$((fails+1)); fi

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

# 6. production wiring: settings.local.json with a `~/` value, session in a SUBDIRECTORY of the repo
python3 - "$P/.claude/settings.json" <<'PY'
import json, sys
p = sys.argv[1]; d = json.load(open(p)); d.pop("autoMemoryDirectory", None); open(p, "w").write(json.dumps(d, indent=2))
PY
git -C "$P" init -q 2>/dev/null
w=$(python3 "$SCRIPTS/_native.py" wire --project "$P/sub" --ws demo | head -n 1)
val=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("autoMemoryDirectory",""))' "$P/.claude/settings.local.json" 2>/dev/null)
st=$(cd "$P/sub" && python3 "$SCRIPTS/_native.py" status | python3 -c 'import json,sys; d=json.load(sys.stdin); print("wired" if d.get("wired") else "unwired", d.get("project_root",""))')
case "$val" in "~/"*) tilde=yes ;; *) tilde=no ;; esac
if printf '%s' "$w" | grep -q '^wired' && [ "$tilde" = yes ] && printf '%s' "$st" | grep -q "^wired $P"; then echo "PASS 6a wire from a subdirectory wrote $val at the git root; status from the subdirectory: wired"; else echo "FAIL 6a wire: $w | value=$val | status=$st"; fails=$((fails+1)); fi
r=$(cd "$P/sub" && claude -p --setting-sources project,local --output-format text 'Look at the auto-memory MEMORY.md content in your context. Quote verbatim the line that starts with "[gowth-mem:bootstrap workspace=" and then, on a second line, the exact heading of the LAST "## " section inside the gowth-mem block. Nothing else.' 2>/dev/null </dev/null | tail -n 3)
hdr6=$(cd "$P/sub" && printf '{"source":"startup","cwd":"%s","session_id":"e2e6"}' "$P/sub" | python3 "$SCRIPTS/bootstrap-load.py" | python3 -c 'import json,sys; print(json.load(sys.stdin)["hookSpecificOutput"]["additionalContext"].splitlines()[-1][:60])')
if printf '%s' "$r" | grep -q 'gowth-mem:bootstrap workspace=demo' && printf '%s' "$r" | grep -q 'Using memory' && printf '%s' "$hdr6" | grep -q 'MEMORY.md is attached'; then echo "PASS 6b MEMORY.md delivered through settings.local.json (~/ value) from a subdirectory; hook header in native mode"; else echo "FAIL 6b local wiring: $r | header: $hdr6"; fails=$((fails+1)); fi

if [ "$fails" -eq 0 ]; then echo "ALL PASS"; exit 0; fi
echo "FAILURES: $fails"; exit 1
