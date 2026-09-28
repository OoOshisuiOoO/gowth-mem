# gowth-mem v4.7.6: what enters the main context, and how memory is read back

Audit run 2026-09-28, 14:21–14:45 (+07). Host: Claude Code **2.1.283**. Plugin: repo HEAD `3442bcf` (v4.7.6). `diff -rq` shows `hooks/ templates/ commands/ skills/ bin/` are byte-identical to the installed cache `~/.claude/plugins/cache/gowth-mem/gowth-mem/4.7.6`; the only difference is a stray `hooks/scripts/.omc` directory in the repo.

All plugin scripts ran with `GOWTH_MEM_HOME=$SCRATCH/vault-copy`. Nothing was written under `~/.gowth-mem`, the repo, or `~/.claude`. The live `~/.claude` files listed below were only read, never modified.

```sh
SCRATCH=/private/tmp/claude-501/-Volumes-Data-Git-bot-openclaw-bridge/1c16482b-7a87-4a12-9d1c-9b8be485cc9b/scratchpad/audit
mkdir -p "$SCRATCH"; rsync -a --exclude .locks /Users/duynguyen/.gowth-mem/ "$SCRATCH/vault-copy/"        # 211 MB
# Neutralise network and git in the COPY only. Otherwise auto-sync.py / _sync.maybe_autosync()
# (_sync.py:345-353) would pull from and PUSH to the real remote using the token in config.json.
python3 - "$SCRATCH/vault-copy/config.json" <<'EOF'
import json,sys; p=sys.argv[1]; c=json.load(open(p)); c.pop("remote",None); c.pop("token",None); open(p,"w").write(json.dumps(c,indent=2))
EOF
mv "$SCRATCH/vault-copy/.git" "$SCRATCH/vault-copy/.git.audit-disabled"
mkdir -p "$SCRATCH/claude-config-copy/plugins"; cp -p ~/.claude/plugins/installed_plugins.json "$SCRATCH/claude-config-copy/plugins/"
export GOWTH_MEM_HOME="$SCRATCH/vault-copy" CLAUDE_CONFIG_DIR="$SCRATCH/claude-config-copy" GOWTH_MEM_NO_AUTOHEAL=1
```

Pristine copies of `state.json`, `config.json` and `settings.json` are in `$SCRATCH/pristine/`. All raw outputs are in `$SCRATCH/out/`. The driver scripts are `stats.py`, `sample.py`, `probe.py` and `stop_driver.py`, all in `$SCRATCH`.

Some evidence comes from read-only analysis of real transcripts:
- the lead session `1c16482b…jsonl` (40 counted real turns, 2 manual compactions)
- this audit session `c5d38802…jsonl`
- the Claude Code binary: `~/.nvm/versions/node/v22.15.0/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe`

---

## Headline numbers

| What | Number |
|---|---|
| SessionStart bootstrap the hook emits (startup, compact, or no source) | **15,589 chars / 16,288 UTF-8 bytes** (≈3,897 tok) |
| What the model actually receives from it (CC 2.1.283) | **2,277 chars / 2,399 bytes** (≈569 tok): a `<persisted-output>` wrapper around the first 1,975 chars. That is the header plus the **first 1,889 chars of `shared/AGENTS.md` only**. |
| Why | CC saves any hook `additionalContext` over **10,000 chars** (`CLo=1e4`) to disk and keeps a **2,000-char preview** (`Vve=2000`) |
| Bootstrap files that reach the model automatically | handoff.md, secrets.md, tools.md, ws AGENTS.md and today's journal: **0 bytes each** |
| Persisted gowth-mem bootstrap files found on disk | **193**, across 14 projects, oldest 2026-09-02 (15,168–16,855 B each) |
| Stop hook with no directive | 43-byte stdout `{"continue": true, "suppressOutput": true}`, so **0 bytes** in context |
| Stop directives (real paths, single-day log) | journal 608 chars, self-review 608, both-cadences 1,464, review-paused 394, inline-journal fallback 360 |
| Per 100 real turns (1 start + 2 compactions, default cadences) | hook-injected **17,303 chars / 17,781 bytes ≈ 4,326 tok** |
| Follow-on cost per 100 turns (subagent dispatch + result relay) | 122k–203k chars ≈ 30.5k–50.7k tok (empirical, lead session). Dominated by the self-review judge. |
| Per-prompt (UserPromptSubmit) retrieval | **None.** 0 bytes unless `SYNC-CONFLICT.md` exists; then 6,868 chars on every prompt. |
| Recall probe (10 random [decision]/[ref] entries, seed 42) | hit@1 **5/10**, hit@3 **7/10**, MRR@20 **0.624**, with or without the workspace filter; about 55 ms per warm CLI call; top-3 block ≈ 925 B |
| index.db | 48,509 chunks, 69.9% of them archive rows; live tag fill 26.2%; **idol-ai has 0 rows**; 275 .md files are newer than their index rows |

---

## A. Injection points into the MAIN context

### A1. Hook wiring and what each event emits (run on the vault copy with synthetic stdin)

`hooks/hooks.json:3-53` registers:
- SessionStart → `session-start.sh`
- PreCompact → `precompact.sh`
- PostCompact → `auto-sync.py --pull-rebase-push --quiet`
- UserPromptSubmit → `conflict-detect.sh`
- Stop → `auto-journal.py`

```sh
R=/Volumes/Data/Git/bot/openclaw-bridge/hooks/scripts; cd /Volumes/Data/Git/bot/openclaw-bridge
for src in startup resume clear compact NONE; do
  J="{\"session_id\":\"audit0001\",\"cwd\":\"/Volumes/Data/Git/bot/openclaw-bridge\",\"hook_event_name\":\"SessionStart\",\"source\":\"$src\"}"
  [ "$src" = NONE ] && J='{"session_id":"audit0001","cwd":"/Volumes/Data/Git/bot/openclaw-bridge","hook_event_name":"SessionStart"}'
  printf '%s' "$J" | bash "$R/session-start.sh" > "$SCRATCH/out/ss-$src.stdout"; done
printf '%s' '{"session_id":"audit0001","cwd":"…","hook_event_name":"UserPromptSubmit","prompt":"hello"}' | bash "$R/conflict-detect.sh"
printf '%s' "{\"session_id\":\"audit0001\",\"transcript_path\":\"$SCRATCH/out/transcript-lead-copy.jsonl\",…,\"hook_event_name\":\"PreCompact\",\"trigger\":\"manual\"}" | bash "$R/precompact.sh"
printf '%s' '{…,"hook_event_name":"PostCompact","trigger":"manual","compact_summary":"x"}' | python3 "$R/auto-sync.py" --pull-rebase-push --quiet
```

| Event / source | Script path (file:line) | stdout bytes | Context payload (chars / UTF-8 bytes) | What the model receives on CC 2.1.283 |
|---|---|---|---|---|
| SessionStart `startup` | session-start.sh:42-44 → bootstrap-load.py | 18,126 | 15,589 / 16,288 | 2,277-char persisted preview |
| SessionStart `compact` | session-start.sh:42 (`compact` included) | 18,126 | 15,589 / 16,288 | same 2,277-char preview. **Yes, compact re-injects the full bootstrap.** Live: lead recs 5116 and 9234 (stdout 18,717 and 18,126 B), delivered recs 5117 and 9235 (2,277 chars each) |
| SessionStart missing `source` | session-start.sh:42 (`-z "$SOURCE"`) | 18,126 | 15,589 / 16,288 | 2,277 |
| SessionStart `resume` | session-start.sh:42 skips the bootstrap | **0** | 0 | 0 (the resumed transcript keeps the old preview) |
| SessionStart `clear` | session-start.sh:42 skips the bootstrap | **0** | 0 | 0 (**no gowth-mem context at all after /clear**) |
| All SessionStart sources | :13 background `auto-sync.py --pull-only --quiet`, :46 `wait` on it; :31-39 detached `doctor.sh` on startup/empty only, output to /dev/null | 0 | 0 | 0. Live hook duration 1,569–1,731 ms (lead recs 8, 5116, 9234 `durationMs`) |
| UserPromptSubmit, no conflict | conflict-detect.sh:14-16 (`exit 0` without output) | **0** | 0 | 0, on every prompt |
| UserPromptSubmit with `SYNC-CONFLICT.md` (synthetic 1-file conflict built in `_conflict.package_conflict()` format from personal handoff.md) | conflict-detect.py:39 (first **30 lines**, no char cap), :44-53 | 7,036 | 6,868 / 6,881 (192 fixed + preview) | inline (under 10k) **on every prompt** until resolved |
| PreCompact | precompact.sh → precompact-flush.py (returns 0 with no stdout on every path, :214-247) then `auto-sync.py --commit-only --quiet` | **0** (stderr 0) | 0 | 0. Side effect: appended **12,095 B** raw dump to `personal/journal/2026-09-28.md` (cap 20,000 chars, :45) |
| PostCompact | auto-sync.py `--quiet`: `log()` suppresses non-errors (auto-sync.py:37-40) | **0** (stderr 0) | 0 | 0. CC routes PostCompact output to `userDisplayMessage` only (binary fn `Urt` @185075375), so it is display-only even when non-empty |
| Stop | auto-journal.py (see A2) | 43 (no-op) to 1,745 | 0 (no-op) to 1,624 | inline (all well under 10k) |

The CC 2.1.283 mechanics come from string search of the binary (Python `mmap` over `claude.exe`):
- `var Vve=2000` @182533671
- `CLo=1e4` @177030625
- `async function cae(e,r,n,{threshold:o=CLo,…}){if(e.length<=o)return e; … aW(e,\`hook-${r}-${n}\`…); … return Lme(s)}` @~182543786, where `Lme` builds `Output too large (…). Full output saved to: … Preview (first 2KB): …`
- SessionStart path `cae(oo.additionalContext,\`${s}-${pr}\`,"additionalContext",{storageV5:We})` @185165257, using the default threshold
- PreCompact fn `Vfe`: successful non-empty stdout becomes `newCustomInstructions` for the summariser (@~185074600)

Live confirmation (lead session `1c16482b`):
- rec 9 (startup, 2026-09-27T10:39:41Z), rec 5117 and rec 9235 (compacts): each gowth-mem item is `<persisted-output>\nOutput too large (15.2KB|15.8KB). Full output saved to: …/tool-results/hook-<id>-7-additionalContext.txt …`, 2,277 chars.
- The model read the persisted file once (rec 75, 10:41:37Z) and never after either compaction.

The delivered 2,277 chars decompose as:
- 278-char wrapper head
- 1,975-char preview: 47-char header + 39-char `=== ~/.gowth-mem/shared/AGENTS.md ===` + 1,889 chars of AGENTS.md. This is the only file block inside the preview (regex on `\n=== … ===\n`).
- 24-char tail

### A2. Stop-hook directives (exact sizes)

How each scenario was triggered, by `$SCRATCH/stop_driver.py`:
- It writes `state.json.session["audit0001"] = {turn_count, review_count, total_turns}`.
- It optionally writes `workspaces/personal/journal/sessions/2026-09-28-audit000.md` with N `## turn k` blocks.
- It then runs `auto-journal.py` with stdin `{"session_id":"audit0001","cwd":"/Volumes/Data/Git/bot/openclaw-bridge","hook_event_name":"Stop","stop_hook_active":false}`, with no `transcript_path`. That means no record identity, so per-Stop counting applies (auto-journal.py:596-603, 615-635).
- `AI_AGENT=claude-code_2-1-283_harness`, so the `hookSpecificOutput.additionalContext` envelope is used (auto-journal.py:145-163, _version.py:150-178).
- "Normalised" sizes replace the scratch vault prefix with `/Users/duynguyen/.gowth-mem`, the repo templates dir with the 4.7.6 cache path, and `audit000` with `1c16482b`. They also append the real backlog suffix `" Backlog: 1064 past conversation(s) unreviewed — run /mem-review-backlog when idle."` (83 chars, auto-journal.py:351-364). That suffix is copied from live directive rec 4478. It could not be generated here because `_review_ledger.stats()` scans `$CLAUDE_CONFIG_DIR/projects` (_review_ledger.py:51-53, 161-171), which is empty in the scratch config.

```sh
python3 "$SCRATCH/stop_driver.py" "$SCRATCH"
```

| Scenario | state before → trigger | envelope | stdout B | raw reason chars | **normalised chars / bytes / ≈tok** | code |
|---|---|---|---|---|---|---|
| S0 nothing fires | tc 0, rc 0 | continue+suppressOutput | 43 | 0 | **0** | auto-journal.py:748 |
| S1 journal, inline fallback (no session log) | tc 9 → 10 ≥ journal_every | additionalContext | 408 | 333 | **360 / 360 / 90** | :302-307, :687-691 |
| S2 journal, delegate (log exists) | tc 9, log 3 blocks | additionalContext | 767 | 680 | **608 / 612 / 152** | :294-301 |
| S3 self-review | rc 14 → 15, log 12 blocks ≥ 10 | additionalContext | 796 | 696 | **608 / 619 / 152** (incl. backlog) | :381-388, :699-704 |
| S4 both cadences | tc 9, rc 14, log 12 blocks | additionalContext | 1,745 | 1,624 | **1,464 / 1,481 / 366** (246-char header + S2 + S3) | :733-744 |
| S5 review-paused (no log, not yet notified) | rc 14, no log | additionalContext | 391 | 311 | **394 / 398 / 98** (incl. backlog) | :712-729 |
| S6 review deferred (log 5 blocks < 10) | rc 14 | continue+suppressOutput | 43 | 0 | **0**, counter kept at 15 | :700-711 |
| S7 paused, already notified | rc 14, `review_paused_notified` | continue+suppressOutput | 43 | 0 | **0** | :712-713 |

Live sizes are larger because the session straddled midnight, so the directive names two log paths (:287-293, :374-376):

| Directive | Lead rec | Chars | Envelope |
|---|---|---|---|
| journal | 3651 | 750 | `hookErrors` |
| self-review | 4478 | 761 | `hookErrors` |
| journal | 4886 | 750 | `hookErrors` |
| both-cadences | 8190 | 1,759 | `hookErrors` |
| journal | 9157 | 750 | `hookAdditionalContext` |

The first four ran from the **4.7.2** cache (`…/gowth-mem/4.7.2/templates/…` in the text). Only rec 9157 (4.7.6) used `additionalContext`.

Latency: the Stop hook takes 46–53 ms when nothing fires and 360–471 ms when the journal cadence fires. The extra time is the synchronous `_prune.py` / `_consolidate.py` / `_forget.py` subprocesses (auto-journal.py:391-422).

### A3. Bytes and ≈tokens (chars/4) per 100 real user turns

Cadence settings in the copy's `settings.json`:
- **None of the keys the code reads are present.** Not present: `auto_journal.journal_every`, top-level `journal_every`, or any `reflection.*` key. There is also no key named `journal.turn_interval`. The `journal` section holds only `raw_ttl_days:7, max_bytes:75000, salvage:true, auto_forget_enabled:true`.
- The code defaults therefore apply:
  - journal_every = **10** (auto-journal.py:115, 204)
  - reflection.enabled = true, turn_interval = **15**, min_review_turns = **10** (:341-343)
  - capture_enabled falls back to reflection.enabled = true (:324)
- Present but read by no code: `auto_sync.on_stop_every_n_turns: 10`, `on_session_start`, `on_pre_compact`, `on_post_compact`.
- The autosync debounce is the default 30 min (`sync.*` absent; _sync.py:279).

With these values, over 100 turns the journal fires 10× (turns 10…100), the review fires 6× (15…90), and both fire together 3× (30, 60, 90). That gives journal-only 7, review-only 3, both 3.

Assumptions: one session, 1 startup + 2 compactions, capture works so the review floor is met at turn 15, no conflict, no version drift.

| Source | n per 100 turns | chars each | chars | UTF-8 bytes | ≈tok |
|---|---|---|---|---|---|
| SessionStart startup (persisted preview) | 1 | 2,277 | 2,277 | 2,399 | 569 |
| SessionStart compact (persisted preview) | 2 | 2,277 | 4,554 | 4,798 | 1,139 |
| UserPromptSubmit (no conflict) | 100 | 0 | 0 | 0 | 0 |
| Stop, no directive | 87 | 0 | 0 | 0 | 0 |
| Stop journal-only (delegate) | 7 | 608 | 4,256 | 4,284 | 1,064 |
| Stop review-only | 3 | 608 | 1,824 | 1,857 | 456 |
| Stop both-cadences | 3 | 1,464 | 4,392 | 4,443 | 1,098 |
| PreCompact / PostCompact | 2 + 2 | 0 | 0 | 0 | 0 |
| **Hook-injected subtotal** | | | **17,303** | **17,781** | **4,326** |
| *Counterfactual: bootstrap delivered inline* (host without the 10k persist rule) | 3 | 15,589 | 46,767 (vs 6,831) | 48,864 | 11,692 |

Not hook output, but in the main context because of gowth-mem:

| Item | n | Size | ≈tok | Evidence |
|---|---|---|---|---|
| Skill listing: 39 `gowth-mem:*` lines | ≥1 per session (re-send after compaction not measured) | 9,153 chars | 2,288 | this session's `skill_listing` attachment (rec 47): 29,990 chars total |
| Follow-on per journal directive: Agent call input + background-launch ack + `<task-notification>` + relay text | 10 | 2,982–3,308 chars (n=3: 3,308 / 3,128 / 2,982) | ≈746–827 each | lead Agent calls at recs 4889, 8193, 9165 (4889 = the 18:25 journal directive) |
| Follow-on per self-review directive (judge) | 6 | **15,382–28,285 chars** (n=2). The task-notification alone is 12,363–12,695 chars; one relay was 12,490 chars | ≈3.8k–7.1k each | lead recs 4499 and 8202. The directive asks to "Relay its 3-line summary" (auto-journal.py:385) |
| Extra model continuation per directive (Stop feedback re-invokes the model) | 13 | cache-read of the whole context each time | not measurable here | |
| Discretionary `Read` of the persisted bootstrap file | 0–3 | 16,288 B plus Read's line-number prefixes | ≈4k each | observed 1× in 3 SessionStarts (lead rec 75) |

Empirical cross-check: the lead session had 40 counted real turns (`state.json.session["1c16482b…"].total_turns=40`). It produced 5 directives totalling 4,770 chars plus 3 SessionStart previews (6,831 chars), so 11,601 hook-injected chars.

### A4. Compaction specifics

- **SessionStart `source=compact` re-injects the full bootstrap** (session-start.sh:42), again as a 2,277-char preview on CC 2.1.283.
- The journal slice in the bootstrap is the **head** of today's journal (`raw[:allowance]`, bootstrap-load.py:83).
  - Test: PreCompact appended a new dump `## [auto-precompact-dump] 2026-09-28 14:38:14` (+12,095 B) at the end (precompact-flush.py:185-191).
  - The following `source=compact` bootstrap's 4,000-char journal content was **byte-identical** to the pre-dump one. It is still the 10:23:01 dump head.
  - Only the `[truncated: N chars omitted]` count changed (31,515 → 43,271).
- **PostCompact injects nothing**:
  - The gowth-mem script prints nothing in `--quiet` mode (auto-sync.py:37-40).
  - CC treats PostCompact stdout as a user display message only (binary `Urt`).
  - On the real machine this hook also does commit + pull --rebase + push (auto-sync.py:340-345).
- **PreCompact injects nothing**: 0-byte stdout, so no `newCustomInstructions` reach the summariser.

---

## B. Bootstrap composition

`bootstrap-load.py --report` takes no workspace flag. The workspace comes from `active_workspace()`, checked in this order:
1. env `GOWTH_WORKSPACE`
2. `.session-workspace`
3. `config.workspace_map` glob against the **process cwd** (_home.py:123-139). The hook's stdin `cwd` is not read; bootstrap-load.py never reads stdin.

`/Volumes/Data/Git/bot/openclaw-bridge/**` maps to `personal`. The largest workspace by disk is **devops**: 11,036 KB, 1,008 files. For comparison: trade 3,336 KB, idol-ai 2,464, default 1,560, personal 688.

```sh
for ws in personal devops trade idol-ai default; do GOWTH_WORKSPACE=$ws python3 hooks/scripts/bootstrap-load.py --report; done
printf '%s' '{"session_id":"audit0001",…,"source":"startup"}' | GOWTH_WORKSPACE=devops bash hooks/scripts/session-start.sh   # offsets parsed from additionalContext
```

**Budget logic.**
- `MAX_TOTAL=15_000` (bootstrap-load.py:46), `MAX_PER_FILE=4_000` (:52).
- `_plan` (:131-137):
  - statics = `shared/AGENTS.md`, `shared/secrets.md`, `shared/tools.md`
  - deltas = `ws/AGENTS.md`, `ws/docs/handoff.md`, plus `journal/<today>.md` only if it exists
- `_allocate` (:87-120):
  - want = min(len, 4,000) for every file
  - **reserve sum(want[deltas]) first**
  - statics share `15,000 − reserved` in order AGENTS → secrets → tools
  - deltas get what remains
- Truncation keeps the **head** (:80-84).
- Emission order is statics then deltas (:244-258). The header is `[gowth-mem:bootstrap workspace=<ws> v4.7.6]` (:280), followed by `drift_nudge()` (empty here; 429–561 chars when a drift exists) and the layout nudge (empty, `layout_version:3`). A summary line comes last (:263).
- The budget-planner path is off (:179-186): settings have neither `retrieval.use_budget_planner` nor `context_budget.enabled`.

**personal** (active; today's journal exists). Reserved for deltas = 1,187 + 4,000 + 4,000 = 9,187, which leaves 5,813 for statics: AGENTS 4,000, secrets 1,813, tools 0.

| # | Block | chars on disk | loaded | block chars | char offset | **byte offset** | status | changes |
|---|---|---|---|---|---|---|---|---|
| 0 | header | – | – | 47 | 0 | 0 | | on upgrade |
| 1 | shared/AGENTS.md | 12,884 | 4,000 | 4,071 | 47 | 47 | truncated, 8,884 omitted (per-file cap) | stable |
| 2 | shared/secrets.md | 32,226 | 1,813 | 1,886 | 4,118 | **4,328** | truncated, 30,413 omitted (static room exhausted) | content stable; **slice length varies with delta sizes** |
| – | shared/tools.md | 6,567 | **0** | 0 | – | – | **DROPPED, no budget** | stable |
| 3 | workspaces/personal/AGENTS.md | 1,187 | 1,187 | 1,239 | 6,004 | 6,252 | full | stable in practice |
| 4 | docs/handoff.md | 9,715 | 4,000 | 4,090 | 7,243 | **7,510** | truncated, 5,715 omitted | **every session** |
| 5 | journal/2026-09-28.md | 35,515 | 4,000 | 4,097 | 11,333 | 11,605 | truncated, 31,515 omitted; the head is the **10:23:01** dump | daily; head stable within a day |
| 6 | summary | – | – | 159 | 15,430 | – | `loaded 5/6 files, 15000 chars / 15000 cap` | |
| | **Total** | | 15,000 | **15,589 chars / 16,288 B** | | | | |

**devops** (largest; no today journal). Reserved = 1,171 + 4,000 = 5,171, leaving 9,829 for statics: AGENTS 4,000, secrets 4,000, tools 1,829.

| # | Block | on disk | loaded | block chars | char off | **byte off** | status |
|---|---|---|---|---|---|---|---|
| 0 | header | | | 45 | 0 | 0 | |
| 1 | shared/AGENTS.md | 12,884 | 4,000 | 4,071 | 45 | 45 | truncated 8,884 |
| 2 | shared/secrets.md | 32,226 | 4,000 | 4,073 | 4,116 | **4,326** | truncated 28,226 |
| 3 | shared/tools.md | 6,567 | 1,829 | 1,899 | 8,189 | 8,488 | truncated 4,738 |
| 4 | workspaces/devops/AGENTS.md | 1,171 | 1,171 | 1,221 | 10,088 | 10,477 | full |
| 5 | docs/handoff.md | 14,776 | 4,000 | 4,089 | 11,309 | **11,758** | truncated 10,776 |
| | **Total** | | 15,000 | **15,557 chars / 16,042 B** | | | loaded 5/5 |

The other workspaces' `--report` runs:
- trade: tools **DROPPED**; ws AGENTS 4,138 → 4,000; handoff 64,535 → 4,000
- idol-ai: ws AGENTS.md missing; handoff 77,835 → 4,000
- default: 5/5 loaded

The report's "on-disk" column counts Python characters, not bytes. For example idol-ai handoff shows 77,835 but is 91,479 B.

**First changing file (stable prompt-cache prefix, as emitted).**
- The first file that changes every session is `docs/handoff.md`, at **byte 7,510** (personal) or **byte 11,758** (devops).
- However, the secrets.md slice length is `15,000 − Σdeltas − 4,000` (:105-113). It moves whenever today's journal appears or disappears, or handoff / ws AGENTS crosses 4,000 chars. On those days the prefix breaks at **byte 4,328** (block 2).
- **As delivered on CC 2.1.283**, the stable prefix of the gowth-mem block is **37 chars** (`<persisted-output>\nOutput too large (`). After that come the size and a per-session path containing the session UUID and hook toolUseID.

Handoff layout varies by workspace, so what the 4,000-char head captures differs:
- personal: the head holds the newest dates (2026-09-27..28).
- devops: the newest entry is at the top (`## host:NDP 2026-09-23 (late)`), but a 2026-09-23 section also sits at @12,211.
- trade: newest first at the top (`## Turn 46-69 status (…, 2026-09-11 12:45-23:20)` @0), but more 2026-09-09..11 auto-journal sections are appended at the tail (@51,491–63,787), so the ordering is mixed. The newest date anywhere in the file is 2026-09-11.
- idol-ai: `# handoff — idol-ai` @0 is followed by a 52,867-char block with no `##` heading. The first `##` is @52,867.

---

## C. Read paths: how stored memory reaches Claude

| # | Path | Trigger | Automatic? | Evidence |
|---|---|---|---|---|
| 1 | SessionStart bootstrap | startup / compact / empty source only | yes, but on CC 2.1.283 only the first 1,889 chars of shared/AGENTS.md are delivered | session-start.sh:41-44; bootstrap-load.py:244-289; A1 |
| 2 | Persisted bootstrap file (`…/tool-results/hook-*-additionalContext.txt`) | model decides to `Read` it | no (discretionary) | lead rec 75 (1 of 3 SessionStarts) |
| 3 | `/mem-recall` → `_query.py` (`query_ex` / `query_by_type`, BM25 over FTS5) | user or model runs the command | **no hook, template or skill invokes it**. Grep over `hooks/ templates/ skills/ commands/ bin/` finds only doc mentions. It is not an auto-trigger skill (the 13 `skills/*/SKILL.md` exclude it); it is visible only as a command line in the skill listing | _query.py:82-126, 344-391; commands/mem-recall.md |
| 4 | `/mem-budget` → `_budget.plan_context` | manual | no; the bootstrap budget-planner branch is disabled by settings | bootstrap-load.py:179-186, 227-242 |
| 5 | SRS resurfacing (state.json `files`) | none | **dead**. The writer `recall-active.py` (607 lines) was deleted in commit `08288df` (2026-05-17, v3.2). The 85 `files` entries are frozen (max `last_seen` 2026-05-17T15:45:18). No Python in hooks/scripts mentions `srs`/`resurface`. The frozen data is still read by `_consolidate.py:81-101, 142` (Stop journal cadence, auto-journal.py:410-420) and `_dream.py:42`, and nothing it produces is injected | `git show --stat 08288df`; state.json |
| 6 | `[[wikilink]]` resolution | none | no. `_wikilink.resolve()` (_wikilink.py:137) has **no runtime caller** in hooks/scripts | grep |
| 7 | `_MAP.md`, topic `00-README.md` MOCs, `skills/_index` | none | no. They are rebuilt on write (_moc.py via _lesson.py:203, _validate.py:520) but **never loaded** by bootstrap (bootstrap-load.py:131-137) | |
| 8 | UserPromptSubmit (per prompt) | every prompt | **No per-prompt retrieval exists.** The only hook is `conflict-detect.sh`, which emits 0 bytes unless `SYNC-CONFLICT.md` exists (conflict-detect.sh:14-16). The v3.2 on-prompt recall hook was removed (`08288df`: hooks.json −12 lines) | hooks/hooks.json:36-45 |
| 9 | Session logs, templates, vault writes | Stop directive, then subagent | subagent contexts only; results come back as task-notifications (A3 follow-on) | templates/auto-journal-instructions.md, self-review-instructions.md |

Text the model does see (inside the delivered preview) describes a bootstrap the code does not perform:
- vault `shared/AGENTS.md` lines 18-26 (§3) claim it loads `shared/{files,secrets,tools}.md`, `<ws>/_MAP.md`, `docs/{exp,ref,tools,files}.md`, the top-3 topic `00-README.md`, journal today+yesterday, and `skills/_index`.

Text past the delivered preview:
- `DEFERRED_NOTICE` (bootstrap-load.py:54-57) says docs, topics and skills "are loaded on-demand via recall". It sits at the end of the 15.6k block, so the model does not see it either.
- AGENTS.md §10 (L246-247) gives a recall score `R = 0.30·BM25 + 0.30·layer + 0.15·recency + 0.15·diversity + 0.10·log(1+recall_count)`. This is not implemented: `_query.py` ranks by BM25 only, with weights tag 5 / heading 4 / keywords 3 / content 1 (_query.py:48). `_profile.profile()` computes `answer_type` and `temporal` (_profile.py:143-149), but no other module reads them.

---

## D. Vault content stats (copy, taken 14:21)

```sh
python3 "$SCRATCH/stats.py" "$SCRATCH/vault-copy" > "$SCRATCH/out/stats.json"   # own read-only walker
```

| ws | topic folders | 00-README | aspects | lessons.md | handoff B | docs/*.md B | journal/*.md # / B | sessions/* # / B | .archive # / B | workspace.json |
|---|---|---|---|---|---|---|---|---|---|---|
| default | 12 | 12 | 178 | 2 | 12,065 | exp 1,275 · files 926 · handoff-archive 125,308 · ref 296 · tools 2,465 | 3 / 193,049 | 10 / 104,998 | 244 / 3,612,444 | yes |
| devops | 56 | 56 | 587 | 17 | 14,900 | exp 27,082 · files 923 · handoff-archive 276,987 · ref 1,534 · tools 20,164 | 2 / 458,925 | 22 / 99,433 | 448 / 1,650,994 | yes |
| idol-ai | 5 | 5 | 57 | 0 | **91,479** | tools 12,787 | 5 / 179,009 (dated 2026-08-10..19) | **53 / 1,391,022** (2026-07-28 → 09-02) | **0** | **no** |
| personal | 4 | 4 | 78 | 1 | 9,763 | exp 994 · files 929 · ref 160 · tools 1,274 | 3 / 46,253 | 2 / 85,801 | 27 / 75,616 | yes |
| trade | 21 | 21 | 412 | 8 | 65,152 | exp 8,136 · files 920 · handoff-archive 254,550 · ref 6,244 · tools 6,241 | 2 / 199,379 | 1 / 1,169 | 106 / 808,523 | yes |
| `workspaces/shared` (pseudo) | 1 | 1 | 1 | 0 | – | – | – | – | 0 | no |

Other sizes: `.archive/` total 7,976 KB; `index.db` 135,634,944 B; `state.json` 146,779 B holding 1,091 session entries. Of those sessions, 713 have ≥1 counted turn, with median total_turns = **1**; only 79 sessions ever reached 10 turns and 58 reached 15.

**Entries by type in the topic tree**, counted as `^- \[t\]` / `^## \[t\]`:

| ws | decision | exp | ref | tool | reflection | skill-ref | secret-ref | goal | hypothesis |
|---|---|---|---|---|---|---|---|---|---|
| default | 3/60 | 4/169 | 1/85 | 2/44 | 0/51 | 0/0 | 0/4 | 0/15 | 0/14 |
| devops | 19/205 | 28/215 | 37/339 | 9/119 | 27/73 | 42/0 | 0/6 | 3/29 | 0/12 |
| idol-ai | 0/76 | 0/126 | 0/78 | 0/23 | 0/120 | 0/0 | 0/2 | 0/16 | 0/11 |
| personal | 3/0 | 6/3 | 2/0 | 1/1 | 4/1 | 0/0 | 0/0 | 1/0 | 1/0 |
| trade | 9/270 | 12/408 | 8/245 | 4/80 | 0/84 | 0/0 | 0/1 | 0/70 | 0/21 |
| **total** | 34/611 | 50/921 | 48/747 | 16/267 | 31/329 | 42/0 | 0/13 | 4/130 | 1/58 |

Typed entries elsewhere:
- docs/: trade exp 25 / ref 25 bullets; devops tool 20 bullets; the rest are single digits.
- journal/: devops 9, trade 15, the rest 0.

**Topic visibility at session start.** The code loads **0** topic READMEs (bootstrap-load.py:131-137), so **100% of the 99 READMEs are invisible**: default 12, devops 56, idol-ai 5, personal 4, trade 21, pseudo-shared 1. Even the top-3 described in AGENTS.md §3 would leave most of them unseen: devops 94.6% (53/56), trade 85.7%, default 75%, idol-ai 60%, personal 25%.

**Aspect ages by filename date** (cumulative ≤7 / ≤30 / ≤90 days, then >90):

| ws | ≤7 | ≤30 | ≤90 | >90 | total |
|---|---|---|---|---|---|
| default | 0 | 74 | 172 | 6 | 178 |
| devops | 67 | 228 | 576 | 11 | 587 |
| idol-ai | 0 | 0 | 57 | 0 | 57 |
| personal | 39 | 39 | 75 | 3 | 78 |
| trade | 0 | 271 | 395 | 17 | 412 |

By mtime, the ≤7-day counts are: default 36, devops 117, idol-ai 0, personal 39, trade 103.

**index.db**

```sh
python3 - "$SCRATCH/vault-copy" <<'EOF'   # sqlite3 on the COPY: counts, tag fill, max(mtime) vs filesystem
…select count(*), sum(tag!=''), sum(keywords!='') from chunks where path not like '.archive/%' …
EOF
```

- FTS schema is `chunks_fts(tag, keywords, heading, content)`.
- **48,509 chunks over 2,689 paths.**
  - `.archive/` rows: 33,910 chunks (69.9%) over 793 paths. These are excluded from normal recall (_query.py:253-259).
  - Live rows: **14,599**. Tag filled 3,829 (**26.2%**), keywords filled 2,187 (15.0%). All rows together: tag 9.3%.
- Tag distribution (all rows): exp 1,477 · ref 1,043 · decision 799 · reflection 484 · tool 414 · goal 188 · hypothesis 82 · secret-ref 25.
- Staleness: the newest indexed chunk mtime is **2026-09-28T13:40:34** (`personal/gowth-mem/2026-09-28-installpath-…md`). The newest .md on disk is **14:21:05** (`devops/proveny/2026-09-28-dev-s3-credentials-…md`).
- Of 2,024 .md files on disk, **161 are not indexed** and **275 are newer than their index rows**.

| scope | files | not indexed | stale |
|---|---|---|---|
| idol-ai (all) | 122 | **122** | – |
| devops/journal | 24 | 13 | – |
| devops/topics | 955 | 14 | 87 |
| trade/topics | 482 | 0 | 105 |
| personal/topics | 113 | 0 | 41 |
| default/topics | 196 | 0 | 36 |

---

## E. Recall probe (copy; documented CLI `python3 _query.py "<q>"`, default `--ws ""` = all workspaces, `--limit 20`)

Population: 1,026 `[decision]`/`[ref]` lines that carry a title on the same line, in topic trees (excluding `00-README.md`). By workspace: default 113 · devops 533 · idol-ai 4 · personal 5 · trade 371.

idol-ai's other 154 [decision]/[ref] headings are bare `## [decision]` lines, with the title in bold on the next line.

`random.seed(42); random.sample(pop, 10)`. Two titles were non-descriptive (#4, #8), so their queries paraphrase the entry's first body line.

```sh
python3 "$SCRATCH/sample.py" "$SCRATCH/vault-copy" > "$SCRATCH/out/sample.json"
python3 "$SCRATCH/probe.py" "$SCRATCH"      # runs, per entry:
GOWTH_MEM_HOME="$SCRATCH/vault-copy" python3 hooks/scripts/_query.py --limit 20 "<query>"              # all-ws
GOWTH_MEM_HOME="$SCRATCH/vault-copy" python3 hooks/scripts/_query.py --ws <ws> --limit 20 "<query>"    # ws filter
GOWTH_MEM_HOME="$SCRATCH/vault-copy" python3 hooks/scripts/_query.py [--ws <ws>] --limit 3 "<query>"   # top-3 block bytes
```

| # | Sampled entry (ws, file:line) | Query | rank all-ws | rank ws-filter | ms (all, limit 20) | top-3 block B |
|---|---|---|---|---|---|---|
| 1 | [ref] RDS `dagster` Terragrunt config + capacity (devops, dagster-db-cleanup/2026-06-17-rds-cleanup.md:47) | dagster database storage and instance size | 8 | 8 | 263 (cold) | 873 |
| 2 | [ref] Web Search do PR #31 … (default, 1tokenai-rust-gateway/2026-08-10-wire-fidelity-cctest.md:66) | who implemented gateway web search | **1** | **1** | 197 | 968 |
| 3 | [ref] User `bytebase` read+write trên CẢ 3 cluster StarRocks (devops, starrocks/lessons.md:654) | bytebase account permissions on starrocks | 5 | 5 | 184 | 883 |
| 4 | [ref] "(2026-06-24 update)" (devops, service-health-alerting-noise-reduction/2026-06-24-aws-lambda-tiering.md:63) | MSK and SNS cloudwatch alert coverage | 12 | 12 | 90 | 955 |
| 5 | [ref] AIOps OSS candidates (Keep, Robusta+HolmesGPT) … (devops, service-health-alerting/2026-07-10-…:79) | open source aiops tools comparison | 2 | 2 | 126 | 819 |
| 6 | [ref] Data-hazard pre-flight audit … #infisical (devops, infisical/2026-07-10-…:29) | infisical upgrade safety check before migrating | 3 | 3 | 93 | 1,230 |
| 7 | [decision] Do NOT deploy NodeLocal DNSCache now … (devops, coredns-dns/2026-07-10-…:35) | why skip node local dns cache | **1** | **1** | 80 | 965 |
| 8 | [ref] "Cross-reference" (devops, bytebase-enterprise-patch/2026-08-12-forticlient-vpn-443-gap.md:42) | forticlient vpn cannot reach bytebase | **1** | **1** | 54 | 960 |
| 9 | [decision] RELEASE `bc65e-v240` … (trade, ea-shorttime-st1/2026-09-03-bc65c-v239-yearly-quarters.md:88) | why release the v240 EA build | **1** | **1** | 65 | 795 |
| 10 | [ref] z.ai chỉ phục vụ model GLM (default, claude-code-multi-profile/2026-08-03-…:109) | which models does z.ai serve | **1** | **1** | 55 | 801 |

Aggregates:

| Mode | hit@1 | hit@3 | MRR@20 | latency | top-3 block |
|---|---|---|---|---|---|
| all workspaces | **5/10** | **7/10** | **0.624** | 54–263 ms (median 93) | mean 925 B (795–1,230) |
| workspace filter on | **5/10** | **7/10** | **0.624** | 51–65 ms (median 55) | mean 940 B |

Rankings are identical in both modes. The source file counts as a hit, with per-path collapse (_query.py:193-215).

In the 5 queries whose source was not rank 1, the rank-1 file was:

| Query | Rank-1 file |
|---|---|
| #1 | `research/dagster-db-cleanup/06-…` |
| #3 | `docs/handoff-archive.md` |
| #4 | `research/aws-alert-standardization/01-…` |
| #5 | `research/aiops-alertmanager-jira/candidates.md` |
| #6 | `research/infisical-upgrade/00-summary.md` |

That is a `research/` note in 4 of the 5, and `docs/handoff-archive.md` in the fifth.

Across the 30 top-3 slots: 18 topic aspects/lessons, **9 `research/` scratch files**, 2 `shared/*`, 1 `handoff-archive.md`.

idol-ai check: `_query.py --ws idol-ai "render layer path choice"` and `--ws idol-ai "handoff"` both return `(no results)`. `select count(*) from chunks where path like 'workspaces/idol-ai/%'` = **0**.

---

## F. Other injectors on this machine (read only; nothing was run)

Sources:
- `~/.claude/settings.json` `enabledPlugins`: claude-mem **disabled**. Enabled: superpowers (×2 marketplaces), frontend-design, oh-my-claudecode, rust-analyzer-lsp, grafana-plugins, claude-obsidian, gowth-mem, vault-keeper, ruflo-federation.
- Project-scoped plugins are bound to `/Volumes/Data/Git/fg/trueprofit/supremor` and `/Volumes/Data/Git/kol-ai`, so they are inactive in this repo.

"Observed" figures are from the lead transcript `1c16482b` attachments (`hook_success.stdout` / `hook_additional_context`).

| Source | Event | Command | Observed / file size |
|---|---|---|---|
| ~/.claude/settings.json | SessionStart, UserPromptSubmit, Stop, StopFailure, SubagentStart/Stop, TeammateIdle, Pre/PostToolUse(Failure), PermissionRequest, PostCompact, SessionEnd | 2,966-char Orca agent-hook shell (forwards the event, prints `{}`) | stdout 3 B each; 0 context |
| superpowers (superpowers-marketplace and claude-plugins-official) | SessionStart `startup\|clear\|compact` | `"${CLAUDE_PLUGIN_ROOT}/hooks/run-hook.cmd" session-start` | stdout 3,604 B, delivered **3,405 chars** per SessionStart (3×) |
| oh-my-claudecode | SessionStart `*` | session-start.mjs, project-memory-session.mjs, wiki-session-start.mjs; `init`: setup-init.mjs; `maintenance`: setup-maintenance.mjs | 213 / 40 / 40 B stdout; delivered `<project-memory-context>` 190–196 chars |
| oh-my-claudecode | UserPromptSubmit `*` | keyword-detector.mjs, skill-injector.mjs | no attachment records in the lead transcript; not measured |
| oh-my-claudecode | Stop `*` | context-guard-stop, workflow-drift-guard, persistent-mode, code-simplifier (.mjs) | 40 / 24 / 40 / 18 B stdout per Stop (44 Stops); 0 context observed |
| oh-my-claudecode | PreCompact `*` | pre-compact.mjs, project-memory-precompact.mjs, wiki-pre-compact.mjs | not recorded. Any stdout would feed the summariser (`newCustomInstructions`) |
| oh-my-claudecode | Pre/PostToolUse, PostToolUseFailure, PermissionRequest, SubagentStart/Stop, SessionEnd | pre-tool-enforcer, post-tool-verifier, project-memory-posttool, post-tool-rules-injector, … | e.g. PreToolUse injected a 117-char "Use parallel execution…" line in this session (rec 28) |
| claude-obsidian | SessionStart `startup\|resume` | `[ -f wiki/hot.md ] && cat wiki/hot.md \|\| true` | `wiki/hot.md` **absent** in /Volumes/Data/Git/bot/openclaw-bridge, /Volumes/Data/Git/bot, and anywhere under /Volumes/Data/Git/bot to depth 3, so 0 B |
| claude-obsidian | PostCompact | same `cat wiki/hot.md` | 0 B here (display-only anyway) |
| claude-obsidian | PostToolUse `Write\|Edit`; Stop | git auto-commit of wiki/; `WIKI_CHANGED` echo when wiki/ is dirty | no wiki/ here |
| gowth-mem | SessionStart / UserPromptSubmit / Stop / PreCompact / PostCompact | as in A1 | as in A1 |
| rust-analyzer-lsp, frontend-design, grafana-plugins, vault-keeper, ruflo-federation | – | no hooks | – |
| claude-mem (disabled) | Setup, SessionStart `startup\|resume`, UserPromptSubmit, PostToolUse, PreToolUse(Read), Stop, SessionEnd | node worker | inactive |

---

## Anomalies

ANOMALY: The bootstrap (15,589 chars, MAX_TOTAL=15,000 at bootstrap-load.py:46) is over Claude Code 2.1.283's 10,000-char inline limit for hook additionalContext (`CLo=1e4`, preview `Vve=2000`). Every SessionStart (startup and compact) therefore delivers a 2,277-char `<persisted-output>` preview holding only the first 1,889 chars of shared/AGENTS.md. handoff.md, secrets.md, tools.md, ws AGENTS.md and today's journal reach the model automatically 0% of the time. Evidence: lead recs 9/5117/9235, this session rec 10, and 193 persisted bootstrap files since 2026-09-02. The v4.3 repair from 2/5 to 5/5 files loaded has no effect on what the model receives.

ANOMALY: Two things break the prompt-cache "stable prefix" rationale (bootstrap-load.py:100-103; docstring :4-5). The delivered block carries a per-session path in its first 278 chars (stable prefix 37 chars). Even as emitted, the statics' allocation depends on delta sizes (:105-113), so the secrets.md slice (1,813 vs 4,000 chars) changes whenever today's journal exists.

ANOMALY: Today's journal is head-truncated (bootstrap-load.py:83, `raw[:allowance]`), so the post-compaction bootstrap never contains the PreCompact safety-net dump written seconds earlier (appended at the end, precompact-flush.py:185-191). Test: after a +12,095 B dump at 14:38:14, the `source=compact` journal slice was byte-identical and still showed the 10:23:01 head.

ANOMALY: The injected AGENTS.md §3 (inside the delivered preview) says the bootstrap loads `_MAP.md`, `docs/{exp,ref,tools,files}.md`, the top-3 topic READMEs, journal today+yesterday, `skills/_index` and `shared/files.md`. The code loads none of them (bootstrap-load.py:131-137), so 99 of 99 topic READMEs are invisible at start. `DEFERRED_NOTICE` (:54-57) promises "loaded on-demand via recall", but no automatic recall exists.

ANOMALY: There is no automatic read path after SessionStart. UserPromptSubmit runs only conflict-detect.sh (0 B unless SYNC-CONFLICT.md exists). `/mem-recall` is invoked by no hook, template or skill. `_wikilink.resolve()` has no runtime caller.

ANOMALY: SRS resurfacing is dead. Its writer was deleted in `08288df` (2026-05-17); state.json `files` (85 entries) has been frozen since 2026-05-17T15:45. The Stop-hook `_consolidate.py` (:142) still ranks on that frozen data: the daily log repeats `candidates 27 / promoted 2`. `settings.json` `recall.*` (9 keys incl. srs_resurface_days/prob, max_total_chars 60000, mmr_lambda, cross_workspace) and `auto_sync.*` (4 keys) have no reader. CLAUDE.md "Key Decisions" still lists "SM-2-lite SRS resurfacing ~25% prob/prompt" and "Hybrid recall".

ANOMALY: idol-ai has no `workspace.json`, so `list_workspaces()` (_home.py:411-423) excludes it. Consequences: 0 index rows (_index.py:165), so all 122 idol-ai .md files are unrecallable; and it is never archived by `_forget.py --all-workspaces` (_forget.py:437; auto-journal.py:434). 53 session logs (1.39 MB, from 2026-07-28) and 4 dated journals (2026-08-10..19) are past `raw_ttl_days=7` and still live. idol-ai's handoff.md is 91,479 B. A `workspaces/shared/` pseudo-workspace (no workspace.json, 1 topic) also exists.

ANOMALY: Index staleness. The newest indexed chunk is 13:40:34 vs the newest .md at 14:21:05. 275 files are newer than their index rows (trade 105, devops 87, personal 41, default 36) and 161 are unindexed. Only `_topic.append_entry` and `_lesson.append_lesson` reindex on write (_topic.py:739-740, _lesson.py:162-163), and no hook runs a full reindex.

ANOMALY: The self-review directive says "Relay its 3-line summary" (auto-journal.py:385), but the judge's full report arrives in the main context as a 12,363–12,695-char task-notification. Each judge dispatch cost 15,382–28,285 main-context chars (lead Agent calls at recs 4499/8202) against a 608–761-char directive; one relay repeated 12,490 chars.

ANOMALY: In the lead session, 4 of 5 directives went out as `decision:block` / `hookErrors` (red "Stop hook error") because the session was executing the **4.7.2** cache (`…/4.7.2/templates/…` in recs 3651/4478/4886/8190). Only rec 9157 (4.7.6) used `additionalContext`. This is the CLAUDE.md "hooks upgraded mid-session" anti-pattern, observed live.

ANOMALY: precompact-flush.py counts and dumps every `type:"user"` record with text as a user turn (:90-101, :141-147). In the live 2026-09-28 personal dump, 5 of 24 `### [user]` blocks are `<task-notification>` XML and 5 are command/skill expansions. The synthetic dump also contains `<local-command-stdout>` records. v4.7.3 fixed this defect class in `_capture.py` only.

ANOMALY: `/clear` gets no bootstrap (session-start.sh:42 handles startup|compact|empty only; measured 0 B for `source=clear`). The context after /clear has no gowth-mem memory, while superpowers re-injects on clear.

ANOMALY: Docs and code disagree:
- `_query.py --ws` defaults to `""` = all workspaces (_query.py:348); mem-recall.md says "Default: active workspace".
- mem-recall.md documents `bm25(chunks_fts, 5.0, 3.0, 1.0)` over (tag, keywords, content); the code uses 4 columns with weights 5/3/4/1 (_query.py:48, schema order tag, keywords, heading, content).
- mem-reindex.md:31 says "nothing reads [FTS5] today".
- precompact.sh:2-3,9,16 still describe a HARD-BLOCK exit 2 that precompact-flush.py never returns.
- CLAUDE.md says "Bootstrap ≤60k chars"; the code uses 15,000.

ANOMALY: `research/` scratch notes (a reserved subdir) are indexed as a recall layer (_index.py:185-191) and took 9 of 30 top-3 slots in the probe. They were rank 1 in 4 of the 5 queries whose source was not rank 1; the fifth was `docs/handoff-archive.md`.

ANOMALY: gowth-mem's 39 skill-listing lines cost 9,153 chars (≈2,288 tok) per session, 4× the delivered bootstrap. The `mem-retag` description is cut to "Backfill deterministic" because YAML reads ` #tags` as a comment, and `mem-validate` appears with no description.

ANOMALY: A pending SYNC-CONFLICT.md injects its first 30 **lines** (not chars) on **every** prompt (conflict-detect.py:39). With one conflicted handoff.md that is 6,868 chars per prompt. Long lines could push it past the 10k persist limit.

ANOMALY: state.json keeps all 1,091 sessions (146,779 B; median session = 1 turn) and rewrites the whole file under `file_lock("state")` on every counted Stop (auto-journal.py:616-635). No session pruning exists.

---

## Not measurable (and why)

- **Exact token counts:** no tokenizer was used. All token figures are chars/4 as instructed.
- **Tokens re-billed per continuation:** each Stop directive re-invokes the model over the whole cached context. Cache-read tokens are not visible in transcripts here.
- **API request ordering / cross-session cache hits:** where SessionStart attachments sit relative to CLAUDE.md/system blocks in the actual API request cannot be seen offline.
- **OMC UserPromptSubmit / PreCompact hook output sizes:** they left no attachment records in the lead transcript, so they were not measured.
- **Skill listing after compaction:** whether it is re-sent after `/compact` is not visible in this session's single listing.
- **Whether subagents (Agent tool, non-team) receive SessionStart:** not tested. Team teammates do; this audit session got the 2,277-char preview at rec 10.
