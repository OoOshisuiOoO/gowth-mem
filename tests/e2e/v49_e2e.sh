#!/bin/bash
# v4.9 real-binary E2E: the daily curator, topic cards, GOWTH_MEM_OFF and pipeline prompts
# on the REAL `claude` binary, with THIS checkout loaded as a plugin (`--plugin-dir`).
#
# Isolation: a scratch project and a scratch vault under $HOME (so `~/` permission rules
# work); `--setting-sources project` keeps the user's own plugins and settings out;
# GOWTH_MEM_HOME points every hook at the scratch vault; GOWTH_MEM_NO_AUTOHEAL=1 keeps
# bin/doctor.sh away from the real plugin registry. The scratch vault is a git repo WITHOUT
# a remote, so no sync ever leaves the machine. Skips with exit 0 when `claude` is missing.
#
# Steps
#   1  plugin agent discovery: the init event lists `gowth-mem:curator`
#   2  the curator end to end in DEFAULT permission mode, allowed exactly the rules that
#      templates/ops/curate.md documents: a phase-2 worklog with `## Curator actions`, the
#      seeded session turns processed, handoff.md untouched, nothing written outside the
#      workspace / the canon, and the relayed summary line
#   3  a topic card reaches the model: a naming prompt (40+ bytes) and a short naming prompt
#      (8-39 bytes) each inject the card (state.json), and the answer quotes the card
#   4  GOWTH_MEM_OFF=1: a whole session leaves the vault byte-identical
#   5  a pipeline prompt (STAGE=VERIFY) is not counted, captured or profiled
# Prints PASS/FAIL per step; exit 1 on any FAIL. Keeps the scratch dir on failure or with
# GOWTH_E2E_KEEP=1. Cost: one sonnet curator run plus ~6 short sessions.
set -u
if ! command -v claude >/dev/null 2>&1; then
  echo "SKIP: claude binary not on PATH"; exit 0
fi
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPTS="$REPO/hooks/scripts"
E="${GOWTH_E2E_DIR:-$HOME/.gowth-e2e49-$$}"
rm -rf "$E"; mkdir -p "$E/proj" "$E/vault"
V="$E/vault"; P="$E/proj"; W="$V/workspaces/demo"
fails=0
cleanup() { if [ "${GOWTH_E2E_KEEP:-0}" != "1" ] && [ "$fails" -eq 0 ]; then rm -rf "$E"; else echo "e2e scratch kept: $E"; fi; }
trap cleanup EXIT
export GOWTH_MEM_HOME="$V" GOWTH_MEM_NO_AUTOHEAL=1
unset GOWTH_WORKSPACE CLAUDE_SUBAGENT GOWTH_MEM_OFF
TODAY=$(date +%Y-%m-%d)
OLD=$(python3 -c 'import datetime; print((datetime.date.today()-datetime.timedelta(days=30)).isoformat())')

# --- scratch vault ------------------------------------------------------------
mkdir -p "$W/docs" "$W/journal/sessions" "$W/memory" "$V/shared"
echo '{"name": "demo"}' > "$W/workspace.json"
printf '{"layout_version": 3, "recall": {"on_prompt_score_threshold": 0.0}, "curator": {"min_new_turns": 1}}\n' > "$V/settings.json"
printf '{"workspace_map": {"%s/**": "demo"}, "host_id": "e2ehost"}\n' "$P" > "$V/config.json"
printf -- '- `FAKE_TOKEN`\n' > "$V/shared/secrets.md"
cat > "$W/docs/handoff.md" <<EOF
## $OLD snapshot
- host:e2ehost $OLD [next] migrate the ingress gateway to pelican-router v2
- host:e2ehost $OLD [blocker] waiting on the TLS certificate for staging
EOF
python3 "$SCRIPTS/_topic.py" --ws demo --ensure pelican-router >/dev/null 2>&1
python3 "$SCRIPTS/_topic.py" --ws demo --topic pelican-router --append \
  "[decision] pelican-router upstream timeout for the batch API is 60s because the batch endpoint answers slowly under load" >/dev/null
python3 "$SCRIPTS/_gaps.py" --ws demo add research "Does pelican-router v2 support gRPC health checks on the admin port?" \
  --why "the v2 migration depends on it; the v1 docs only mention HTTP probes" >/dev/null
cat > "$W/journal/sessions/$TODAY-e2e00001.md" <<'EOF'
# Session log — e2e — e2e00001

## turn 1 — 09:10
**User:** what timeout does pelican-router use for the batch API upstream right now?
**Claude:** The recorded decision says 60s for the batch upstream because the endpoint is slow under load.
**Actions:** Read(workspaces/demo/pelican-router)

## turn 2 — 09:14
**User:** no, that's wrong — we changed it to 30s last week in deploy/pelican.yaml, commit 3f2a9c81d0e4, because 60s hid stuck workers
**Claude:** Understood: the batch upstream timeout is now 30s (deploy/pelican.yaml, commit 3f2a9c81d0e4).
**Actions:** Read(deploy/pelican.yaml) → Edit(deploy/pelican.yaml)

## turn 3 — 09:20
**User:** we decided to keep pelican-router v1 until the gRPC health check question is settled, because v2 drops the legacy probe
**Claude:** Recorded: stay on v1 until gRPC health checks on v2 are confirmed.

## turn 4 — 09:31
**User:** how do I restart pelican-router safely on the edge cluster?
**Claude:** kubectl rollout restart deploy/pelican-router -n edge, then watch kubectl rollout status until all pods are ready.
**Actions:** Bash(kubectl rollout restart deploy/pelican-router -n edge) → Bash(kubectl rollout status deploy/pelican-router -n edge)

## turn 5 — 09:40
**User:** the staging TLS certificate arrived and is installed, so that blocker is gone
**Claude:** Noted: staging TLS certificate installed (cert-manager secret edge/staging-tls).
**Actions:** Bash(kubectl get secret staging-tls -n edge)

## turn 6 — 09:52
**User:** thanks — make sure the restart procedure and the 30s timeout are in memory
**Claude:** I'll leave that to the curator's daily pass.
EOF
python3 "$SCRIPTS/_index.py" --full >/dev/null 2>&1
python3 -c 'import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); import _sync; _sync.write_default_gitignore(Path(sys.argv[2]))' "$SCRIPTS" "$V"
git -C "$V" init -q -b main && git -C "$V" add -A && \
  git -C "$V" -c user.name=e2e -c user.email=e2e@x commit -q -m seed
SEED=$(git -C "$V" rev-parse HEAD)

cd "$P" || exit 1
CL=(claude -p --setting-sources project --plugin-dir "$REPO")
state_q() { python3 - "$V/state.json" "$1" <<'PY'
import json, sys, os, datetime
st = json.load(open(sys.argv[1])) if os.path.isfile(sys.argv[1]) else {}
sid = sys.argv[2]; today = datetime.date.today().isoformat()
s = (st.get("session") or {}).get(sid, {})
rec = s.get("recall") if isinstance(s.get("recall"), dict) else {}
day = (st.get("recall_daily") or {}).get(today) or {}
print(f"turns={s.get('total_turns', 0)} cards={len(rec.get('cards') or [])} injected={rec.get('injected', 0)} "
      f"day_prompts={day.get('prompts', 0)} day_cards={day.get('cards', 0)}")
PY
}

# 1. plugin agent discovery (mechanical: the init event)
"${CL[@]}" --output-format stream-json --verbose 'Reply with the single word OK.' </dev/null > "$E/init.jsonl" 2>/dev/null
agents=$(python3 -c 'import json,sys
for l in open(sys.argv[1]):
    try: o=json.loads(l)
    except Exception: continue
    if o.get("type")=="system" and o.get("subtype")=="init": print(" ".join(o.get("agents") or [])); break' "$E/init.jsonl")
if printf '%s' "$agents" | grep -qw 'gowth-mem:curator'; then echo "PASS 1 plugin agent discovered: gowth-mem:curator"; else echo "FAIL 1 agents: $agents"; fails=$((fails+1)); fi

# 2. curator end to end, default permission mode + the documented allow rules only
DIRECTIVE=$(python3 "$SCRIPTS/_curate.py" directive --ws demo)
VREL="${V#"$HOME"/}"
ALLOW=()
for t in _curate.py _query.py _topic.py _gaps.py _lesson.py _card.py; do
  ALLOW+=("Bash(GOWTH_MEM_HOME=$V python3 $SCRIPTS/$t:*)")
done
ALLOW+=("Read(~/$VREL/**)" "Read(/$REPO/**)")
"${CL[@]}" --allowedTools "${ALLOW[@]}" --output-format stream-json --verbose "$DIRECTIVE" </dev/null > "$E/curator.jsonl" 2>"$E/curator.err"
relay=$(python3 -c 'import json,sys
last=""
for l in open(sys.argv[1]):
    try: o=json.loads(l)
    except Exception: continue
    if o.get("type")=="result": last=str(o.get("result") or "")
print(last)' "$E/curator.jsonl")
wl=$(ls "$W/docs/worklog/"*.md 2>/dev/null | head -n 1)
phase=""; actions=no; processed=""
if [ -n "$wl" ]; then
  phase=$(sed -n 's/^phase: *//p' "$wl" | head -n 1)
  grep -q '^## Curator actions' "$wl" && actions=yes
  processed=$(sed -n 's/^processed: *//p' "$wl" | head -n 1)
fi
changed=$( { git -C "$V" diff --name-only "$SEED" HEAD; git -C "$V" status --porcelain | cut -c4-; } | sort -u)
outside=$(printf '%s\n' "$changed" | grep -v -E '^(workspaces/demo/|shared/|\.gitattributes$|\.gitignore$)' | grep -v '^$' || true)
handoff_same=yes; git -C "$V" diff --quiet "$SEED" -- workspaces/demo/docs/handoff.md || handoff_same=no
if [ -n "$wl" ] && [ "$phase" = "2" ] && [ "$actions" = yes ] && printf '%s' "$processed" | grep -q 'e2e00001' \
   && [ -z "$outside" ] && [ "$handoff_same" = yes ] && printf '%s' "$relay" | grep -q 'curator ws=demo:'; then
  echo "PASS 2 curator: worklog phase 2 with actions, processed $processed; handoff untouched; relay: $(printf '%s' "$relay" | grep 'curator ws=demo:' | head -n 1)"
else
  echo "FAIL 2 curator: worklog=${wl:-none} phase=$phase actions=$actions processed=$processed handoff_same=$handoff_same outside=[$outside]"
  echo "     relay: $(printf '%s' "$relay" | head -c 600)"
  fails=$((fails+1))
fi
echo "     curator wrote: $(printf '%s\n' "$changed" | grep -E 'worklog|questions|pelican|lessons|README' | tr '\n' ' ')"

# 3. card injection (set a card when the curator did not write one)
if ! python3 "$SCRIPTS/_card.py" --ws demo --topic pelican-router --show 2>/dev/null | grep -q '### Core'; then
  printf '### Core\n- pelican-router is the ingress gateway on the edge cluster\n### State (as of %s)\n- %s: batch upstream timeout is 30s (deploy/pelican.yaml)\n### Routing\n| Question | Start | Then |\n|---|---|---|\n| timeouts | [[pelican-router]] | deploy/pelican.yaml |\n### Gotchas\n- restart with kubectl rollout restart, never delete pods by hand\n### Sources\n- deploy/pelican.yaml\n' "$TODAY" "$TODAY" \
    | python3 "$SCRIPTS/_card.py" --ws demo --topic pelican-router --set - >/dev/null
fi
sid3=$(uuidgen | tr 'A-Z' 'a-z'); sid4=$(uuidgen | tr 'A-Z' 'a-z')
a3=$("${CL[@]}" --session-id "$sid3" --output-format text 'What is the current pelican-router batch upstream timeout? Answer in one short line.' </dev/null 2>/dev/null | tail -n 2)
s3=$(state_q "$sid3")
"${CL[@]}" --session-id "$sid4" --output-format text 'pelican-router timeout?' </dev/null >/dev/null 2>&1
s4=$(state_q "$sid4")
if printf '%s' "$s3" | grep -q 'cards=1' && printf '%s' "$s4" | grep -q 'cards=1' && printf '%s' "$a3" | grep -q '30'; then
  echo "PASS 3 card injected for a naming prompt ($s3) and a short one ($s4); answer: $a3"
else
  echo "FAIL 3 cards: long[$s3] short[$s4] answer: $a3"; fails=$((fails+1))
fi

# 4. GOWTH_MEM_OFF=1: the whole session leaves the vault byte-identical
snap() { (cd "$V" && find . -path ./.git -prune -o -type f -print0 | sort -z | xargs -0 shasum 2>/dev/null) | shasum | cut -c1-12; }
before=$(snap)
GOWTH_MEM_OFF=1 "${CL[@]}" --output-format text 'What is the pelican-router restart procedure? One line.' </dev/null >/dev/null 2>&1
after=$(snap)
if [ "$before" = "$after" ]; then echo "PASS 4 GOWTH_MEM_OFF=1: vault unchanged ($before)"; else echo "FAIL 4 GOWTH_MEM_OFF: vault changed $before -> $after"; fails=$((fails+1)); fi

# 5. a pipeline prompt is not counted, captured or profiled
sid5=$(uuidgen | tr 'A-Z' 'a-z')
d0=$(state_q "none" | sed 's/.*day_prompts=\([0-9]*\).*/\1/')
"${CL[@]}" --session-id "$sid5" --output-format text "$(printf 'STAGE=VERIFY\nVerify the pelican-router note below. Reply with the single word OK.')" </dev/null >/dev/null 2>&1
s5=$(state_q "$sid5"); d1=$(printf '%s' "$s5" | sed 's/.*day_prompts=\([0-9]*\).*/\1/')
log5="$W/journal/sessions/$TODAY-${sid5:0:8}.md"
if printf '%s' "$s5" | grep -q 'turns=0' && [ ! -f "$log5" ] && [ "$d0" = "$d1" ]; then
  echo "PASS 5 pipeline prompt: not counted, no session log, not profiled ($s5)"
else
  echo "FAIL 5 pipeline: $s5 log=$( [ -f "$log5" ] && echo present || echo absent ) day_prompts $d0 -> $d1"; fails=$((fails+1))
fi

if [ "$fails" -eq 0 ]; then echo "ALL PASS"; exit 0; fi
echo "FAILURES: $fails"; exit 1
