# v4.8 — Native-first memory: design

Date: 2026-09-28. Status: approved in conversation (design), spec under user review.
Evidence: `docs/audits/2026-09-28-audit-flow.md` (data-flow + token audit on the live vault),
`docs/audits/2026-09-28-holistic-review.md` (fresh-context review of v4.7.2..v4.7.6), and the
real-binary E2E runs summarised in §2.

## 1. Goals and success criteria

Owner's goals: (G1) Claude actually remembers across sessions and machines; (G2) minimum
main-context token cost; deep integration with Claude Code rather than beside it.

Measured on the live vault today (v4.7.6, Claude Code 2.1.283):

| Criterion | Today | v4.8 target |
|---|---|---|
| Bootstrap delivered to the model | 2,277 of 15,589 chars (first 1,889 chars of `shared/AGENTS.md`); `handoff.md` 0% in 193 sessions since 2026-09-02 | 100% of the managed block, verified by E2E on the real binary |
| Automatic read path after session start | none (UserPromptSubmit emits 0 bytes; `/mem-recall` invoked by nothing) | gated per-prompt recall; 0 bytes when nothing qualifies |
| Recall probe (10 real entries, seed 42) | hit@1 5/10, hit@3 7/10, MRR 0.62 | hit@3 ≥ 8/10 on the same 10; precision of injected entries ≥ 0.8 on 30 queries; 0 injections on 20 generic prompts |
| Main-context follow-on per 100 real turns | 122k–203k chars (judge relays of 12.5k chars) | ≤ 60k chars (journal teammate ≈3k × 10 + judge ≤ 4.5k × 6) |
| Hook output size | bootstrap 15,589 chars (> 10,000 → persisted) | every hook emission < 9,000 chars, enforced by one constant and one test |
| Skill listing per session | 39 lines, 9,153 chars | ≤ 15 commands, ≤ 7 skills, ≤ 3,000 chars |
| Index freshness | 275 files newer than their rows, 161 unindexed, idol-ai 0 rows | 0 stale files after a Stop; every workspace directory indexed |

Non-goals: no LLM in any hook path; no pip dependencies; no change to the topic-folder layout,
the write gate, the privacy sanitizer, or the git sync model.

## 2. Facts the design rests on (all measured on Claude Code 2.1.283)

1. **Hook context is persisted at 10,000 chars.** Any hook `additionalContext` or SessionStart
   stdout of ≥ 10,000 chars is written to `tool-results/hook-*.txt` and the model receives a
   2,000-char preview. 9,500 chars is delivered in full. Same for stdout and for
   `hookSpecificOutput.additionalContext`. Binary constants `CLo=1e4`, `Vve=2000`. Undocumented.
2. **Auto memory (`MEMORY.md`) is not subject to that rule.** The first 200 lines / 25 KB are
   attached with the CLAUDE.md `instructions` bundle (`{"type":"AutoMem"}`), fully visible at
   12 KB / 169 lines, cut at line 200 with a "139 cut off" notice at 339 lines. Files beside it
   are read on demand.
3. **`MEMORY.md` is re-attached at session start, on resume, and after every compaction**, not
   when the file changes mid-session (five re-attachments in one 14-day session file, each at
   a resume or a compaction boundary). Regenerating it at Stop is free until the next
   (re)attachment.
4. **`autoMemoryDirectory` is honoured from project scope**, including `.claude/settings.local.json`,
   and names the memory directory itself (no `<project>/memory/` nesting): Claude wrote
   `project-postgres-16.md` and updated `MEMORY.md` directly inside it.
5. **`@/absolute/path` imports in CLAUDE.md are not expanded headless** (external imports need a
   one-time interactive approval). Not used.
6. **Stop directives are pointers ≤ 1,464 chars** (well under the rule). The token leak there is
   the judge's 12.5k-char report relayed into the main context because the review directive
   dictates no prompt (H1 in the holistic review).
7. **The bootstrap's journal slot loads the head of today's journal** — the oldest precompact
   dump, 84% machine text — and drops the newest (H2).
8. Research (Perplexity deep research, 2026-09-28; SWE-ContextBench, Terminal-Bench 2.0 proactive
   memory, MemSyco-Bench): correctly selected concise memory improves task success and cost;
   autonomous retrieval that picks wrong experience scores below no memory. Recommended budgets:
   bootstrap 300–800 tokens, working index 500–1,500, retrieved 3–8 items ≤ 2,500 tokens, deep
   archive only through a tool. Place dynamic memory late for prompt-cache stability.

## 3. Architecture

Read side (new):

```
session start / resume / compact ──► Claude Code attaches  <ws>/memory/MEMORY.md
                                     (managed block ≤130 lines + Claude's own index)
SessionStart hook ──────────────────► ≤ 600-char header (version, ws, drift, native status)
                                     or ≤ 9,000-char fallback bootstrap when native is not wired
UserPromptSubmit hook ──────────────► 0 bytes, or ≤ 2,000 chars of gated BM25 recall
on demand ──────────────────────────► Read <ws>/<slug>/00-README.md, /mem-recall, memory/*.md
```

Write side (unchanged, plus): the Stop hook regenerates the managed block when its sources
changed, reindexes changed files incrementally, sanitizes Claude-written `memory/*.md`, and
dictates a verbatim judge prompt.

Layout change: `workspaces/<ws>/memory/` becomes a reserved subdirectory (added to the
reserved-subdir set behind `_home.is_reserved`, next to `docs/`, `journal/`, `skills/`,
`research/`, so topic routing never treats it as a topic). It holds `MEMORY.md` plus whatever
Claude Code's auto memory writes. It syncs with the vault.

Delivery order inside v4.8.0 (one plan, three phases, each green before the next): A — `_home`
limits + accessor, `_memfile`, `_native`, SessionStart, UserPromptSubmit recall, index changes;
B — Stop hook (judge directive, precompact classification, memfile/reindex/sanitize steps);
C — command consolidation, settings example, docs. Release only after all three.

## 4. Components

### 4.1 `_native.py` — per-project wiring (`/mem-setup native`)

- `projects_for_workspaces(config) -> list[(project_dir, ws)]`: every `workspace_map` glob whose
  base directory exists on this machine, plus the current cwd's workspace. Globs with `**` map
  to their base directory.
- `wire(project_dir, ws, *, force=False) -> str`: merges
  `{"autoMemoryDirectory": "<GOWTH_MEM_HOME>/workspaces/<ws>/memory"}` into
  `<project_dir>/.claude/settings.local.json` (created if absent, other keys preserved, JSON
  re-serialised with 2-space indent). Returns `wired | already | conflict | tracked`:
  `conflict` when the key exists with another value and `force` is false;
  `tracked` when `git -C <project_dir> ls-files --error-unmatch .claude/settings.local.json`
  succeeds (the file is committed) — then nothing is written and the doctor reports it.
  The vault path is written with `~/` when it is under `$HOME`, so the same file works on
  every machine that keeps the vault at the default location.
- `import_native(ws_map, *, apply=False) -> report`: for each `~/.claude/projects/<slug>/memory/`
  whose slug maps to a project in `workspace_map`, copy `*.md` into `<ws>/memory/` through
  `_privacy.sanitize`; on a filename clash keep the vault copy and write the incoming one as
  `<name>.from-<host>.md`; the incoming `MEMORY.md` index lines are appended to the free zone
  (deduplicated by exact line). Unmapped slugs are listed, never imported. Dry-run by default.
- `status() -> dict` for `/mem-doctor`: wired projects, conflicts, tracked files, unmapped memory
  directories, whether the active ws's `MEMORY.md` exists and is within budget, and whether the
  host has auto memory disabled (`autoMemoryEnabled: false` in `~/.claude/settings.json` or
  `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`) — when it is, `is_wired()` is false and the hooks use
  the fallback bootstrap.
- Nothing here runs from a hook. Wiring is an explicit one-time command per machine.

### 4.2 `_memfile.py` — the managed block

`MEMORY.md` layout (managed block first, then Claude's free zone):

```
<!-- gowth-mem:begin ws=<ws> -->
[gowth-mem:bootstrap workspace=<ws> v4.8.0]            ← + drift nudge line when outdated
## Rules
<≤ 25 lines: identity, write path (/mem-save → _topic.py --append, 9 types, gate), recall path
 (/mem-recall, Read <ws>/<slug>/00-README.md), workspace rule, secrets are pointers, language,
 end-of-session handoff>
## Handoff
<≤ 60 lines from _handoff.digest(ws): sections ordered newest date first; within the newest
 section [blocker] [doing] [next] [thread] bullets before [done]; lines > 160 chars cut>
## Topics
<≤ 40 lines: `- <slug> — <title or first summary line> (last_touched YYYY-MM-DD)` sorted by
 last_touched desc, from 00-README.md frontmatter; nested topics as `parent/child`>
## Recent decisions
<≤ 10 lines: `- YYYY-MM-DD [decision] <title> — <ws>/<path>` from the index, last 7 days>
## Secrets (pointers only)
<≤ 8 lines: env-var names from shared/secrets.md; never values>
## Using memory
<3 fixed lines: /mem-recall <query>; Read the topic README before working on that topic;
 /mem-save <type> "<entry>" to remember>
<!-- gowth-mem:end -->
<Claude's own index lines, untouched>
```

- `render(ws, *, max_lines, max_chars) -> str` is pure. Default budget: `max_lines=130`,
  `max_chars=12000`. When Claude's free zone has `n` lines, the block budget is
  `min(130, 190 - n)`; sections shrink in the order Recent decisions → Topics → Handoff, each to
  its minimum (0 / 10 / 20 lines) before the next is touched. Rules and Using memory are never
  cut, so the block never goes below its 28-line floor; when `190 - n < 28` the block is still
  written at the floor (the host then trims the free zone's tail) and `/mem-doctor` reports
  "MEMORY.md free zone over budget: <n> lines".
- `write(ws) -> bool`: reads the existing file, splits at the markers (missing markers = the
  whole file is free zone), renders, compares SHA-1 of the managed block, and rewrites only on
  change under `file_lock("memfile-<ws>")` with `atomic_write`. Deterministic: no timestamps,
  hostnames, or session ids in the block.
- `sources_changed(ws) -> bool`: newest mtime among `docs/handoff.md`, every topic
  `00-README.md`, `shared/secrets.md`, `index.db`, and the plugin version, compared with the
  file's own mtime. Used by the Stop hook as a cheap gate (≈100 stats).
- Bootstrap fallback: `render_hook_bootstrap(ws) -> str` = the same sections without the
  markers, `max_chars=8500`, header first, handoff before rules. This is what
  `bootstrap-load.py` prints when native is not wired.
- Sync conflicts on `memory/MEMORY.md` are resolved in `_sync` by regenerating the block and
  taking the union of both free zones (line-exact dedupe, ours first). No `SYNC-CONFLICT.md`
  for this file.
- Privacy: the Stop hook runs `_privacy.sanitize` over every `memory/*.md` whose mtime is newer
  than the last Stop (`state.json.memory_sanitized_at`); rewrites only when the count > 0.

### 4.3 `session-start.sh` and `bootstrap-load.py`

- `HOOK_CONTEXT_MAX = 9000` in `_home.py`; `_home.clamp_context(text) -> str` truncates at a
  line boundary and appends `[gowth-mem: truncated to fit the host limit]`. Every emitter
  (`bootstrap-load.py`, `auto-journal._stop_output`, `conflict-detect.py`, `_recall_prompt.py`)
  passes its payload through it.
- `startup`, `clear`, empty source: if `_native.is_wired(cwd, ws)` (the project's
  `autoMemoryDirectory` resolves to `<ws>/memory` and auto memory is not disabled on the host)
  and `<ws>/memory/MEMORY.md` exists → print the header only: version tag, workspace, drift nudge, `memory: MEMORY.md
  (<n> topics, handoff <date>)`, and `run /mem-setup native` when not wired. ≤ 600 chars.
  Otherwise → `render_hook_bootstrap(ws)` (≤ 8,500 chars).
- `compact`: header + `## Handoff` first 15 lines, ≤ 1,500 chars (MEMORY.md is re-attached by
  the host anyway).
- `resume`: unchanged (0 bytes).
- On `startup`: ensure `workspace.json` exists for the resolved workspace (create from the
  template when missing — the idol-ai defect), regenerate `MEMORY.md` if missing, and start a
  detached `_index.py --incremental` (see 4.5). The existing background pull and doctor stay.
- `bootstrap-load.py --report` keeps its output shape for `/mem-cost` and adds the delivered
  size, the MEMORY.md block size, and the free-zone line count.

### 4.4 `recall-on-prompt.sh` → `_recall_prompt.py` (UserPromptSubmit)

- Bash pre-check, no Python: exit 0 silently when `recall.on_prompt.enabled` is `false` in
  `settings.json` (grep), when the prompt (first 200 bytes extracted with `sed`) starts with `/`
  or `!`, when it is shorter than 40 bytes, or when the event names a subagent
  (`agent_type`/`in_loop` as in the Stop hook's subagent skip).
- Python: `query = _profile.fts_match(_profile.profile(prompt))`; `rows = _query.query_ex(query,
  ws=active, limit=8, exclude=("research/", "docs/handoff-archive.md", "journal/", "memory/MEMORY.md"))`.
  Injection gate, all required:
  - ≥ `recall.on_prompt.min_terms` (default 2) distinct query terms present in the chunk;
  - `bm25 ≤ recall.on_prompt.score_threshold` (default calibrated in §7.3; stored in settings);
  - chunk id not in `state.json.session[<sid>].injected` (capped at 300 ids, FIFO);
  - at most `recall.on_prompt.max_entries` (3) entries and `max_chars` (2,000) in total.
- Output format (additionalContext, through `clamp_context`):
  ```
  [gowth-mem:recall ws=<ws>] related memory (read the file for more):
  - <ws>/<path>:<line> [<type>] <title> — <snippet ≤ 700 chars>
  ```
  No qualifying chunk → no output at all (no `{}` envelope needed; exit 0 with empty stdout).
- Telemetry: `state.json.session[<sid>].recall = {"prompts": n, "injected": n, "chars": n}`;
  `/mem-cost` prints it.
- Latency budget: ≤ 150 ms warm (Python start + one FTS5 query; measured 55 ms today).

### 4.5 Index and query changes

- `_index.py --incremental`: walk every workspace's `.md` files (≈2,000 stats), reindex those
  with mtime newer than the stored row or with no row, at most 200 files per run; prints the
  count. The Stop hook runs it synchronously when `sources_changed` or when the last run is
  older than 10 minutes; SessionStart starts it detached. The daily forget slot runs a full
  `--rebuild`.
- `list_workspaces()` returns every `workspaces/<name>/` directory that contains `docs/`,
  `journal/`, or a topic folder, with or without `workspace.json`; `/mem-doctor --fix` creates
  the missing `workspace.json`.
- Default recall layers exclude `research/` and `docs/handoff-archive.md`; `_query.query_ex`
  gains `exclude=` (path prefixes) and `include_research=False`; `/mem-recall --include-research`
  restores the old behaviour.
- `mem-recall.md` documents the real defaults (`--ws` = active workspace, 4-column bm25 weights).

### 4.6 Stop hook

- Review directive dictates the judge prompt verbatim, mirroring the journal directive:
  "You are the dispatched gowth-mem judge — never dispatch further subagents. Read
  <self-review-instructions.md> and judge <log paths>; scores go to <_scores.md>. Write the
  full report with `python3 <_capture.py> --append-review …` (use the session-insights format
  there if that skill is available to you). Your FINAL message is exactly 3 lines: scores /
  top friction / one rule." The main-context instruction: dispatch verbatim, do not load review
  skills in the main context, relay the 3 lines only. `templates/self-review-instructions.md`
  gains the same 3-line contract at the top and the end.
- `precompact-flush.py` reuses `_capture._classify_record`/`prompt_text` so dumps hold human
  prompts and capped assistant text (≤ 500 chars per chunk); machine records and notification
  relays are dropped. The 20 KB cap stays. The journal is no longer part of any bootstrap.
- After capture and cadence work: `_memfile.write(ws)` when `sources_changed`, the incremental
  reindex per 4.5, and the `memory/*.md` sanitizer pass. Each step is best-effort and logged via
  `_debug`; the hook still always exits 0 with the same envelope rules as today.
- One settings snapshot per Stop (`read_settings()` once), replacing the five reads.

### 4.7 Command surface

- Keep 15 top-level commands: `mem-recall`, `mem-save`, `mem-review`, `mem-sync`, `mem-doctor`,
  `mem-setup`, `mem-install`, `mem-workspace`, `mem-research` (subcommands `start | distill |
  status`), `mem-distill`, `mem-topic`, `mem-lesson`, `mem-goal`, `mem-handoff`, `mem-cost`.
- Everything else moves under `mem-ops <sub>`: `budget changelog compress config dream forget
  gate journal lint migrate-global migrate-v3 promote prune reflect reindex restructure retag
  review-backlog skillify validate verify`, plus `sync-resolve` becomes `mem-sync resolve`.
  `mem-ops.md` dispatches on the first argument and links each sub's instructions, which move
  to `commands/ops/<sub>.md` (not listed; read on invocation).
- Skills: keep `mem-save`, `mem-sync`, `mem-install`, `mem-distill`, `mem-sync-resolve` (retitled
  to fire on conflict text), add `mem-recall`; remove the other 8.
- Every description ≤ 80 chars, no `Usage:` text, no bare `: ` (frontmatter rule), and
  `mem-retag`'s description no longer contains ` #` (YAML comment — the current one is cut).
- The conflict hook's instruction text and every doc link are updated to the new names;
  `tests/test_version_drift.py` (bin script references) is extended to fail on a command name
  referenced anywhere in `commands/`, `skills/`, `templates/`, `hooks/` that no longer exists.

### 4.8 Settings and docs

- `_home.setting(path: str, kind: type, default)` — the single typed accessor: dotted path,
  per-key fallback to the default (a malformed sibling never resets a whole section),
  `_coerce_bool` for `bool` (moved from auto-journal to `_home`), `int` with `ValueError` →
  default. All 13 bare truthiness reads and the ≈20 ad-hoc section reads in `auto-journal`,
  `_sync`, `_forget`, `_capture`, `_gate`, `_tags`, `_index`, `bootstrap-load`, `_topic`,
  `_lesson` use it.
- New keys (all documented in the example): `native.enabled` (bool, default true),
  `recall.on_prompt.{enabled, max_entries, max_chars, min_terms, score_threshold}`,
  `memfile.{max_lines, max_chars}`. The 51 dead keys are deleted from
  `settings.example.v3.json`; `archive_threshold_days` example value becomes 90 (code default).
- `templates/AGENTS.md` §3 describes exactly what is loaded (MEMORY.md block, header, gated
  recall) and tells the model how to reach topics; §10's unimplemented recall formula is
  removed. `CLAUDE.md` (repo): SRS/MMR/"≤60k" claims removed; the 10,000-char host rule, the
  9,000-char hook cap, and the MEMORY.md channel added to Key Decisions and Anti-patterns;
  `precompact.sh` comments corrected; a new rule: `claude -p` in any test or probe always runs
  with `--setting-sources project` and a scratch `GOWTH_MEM_HOME` (three test session logs
  leaked into the live vault on 2026-09-28 without it).

## 5. Error handling and safety

- Every hook exits 0 with empty or well-formed stdout on every failure path; new code paths
  log through `_debug`.
- All vault writes: `atomic_write` + `file_lock`. `MEMORY.md` never loses Claude's free zone:
  the writer preserves everything outside the markers byte-for-byte, and a missing end marker
  means the file is treated as all free zone (the block is prepended).
- `_native.wire` never overwrites a differing `autoMemoryDirectory` without `--force`, never
  touches a git-tracked `settings.local.json`, and never runs from a hook.
- No hook emission can exceed 9,000 chars (`clamp_context`), so nothing new can fall into the
  persisted-preview path.
- Per-prompt recall injects nothing on any error (missing index, locked DB, malformed prompt).
- Secrets: the block lists env-var names only; `memory/*.md` written by Claude Code go through
  `_privacy.sanitize` before any sync.
- Live-vault steps that delete or move data (`--import`, migration cleanup) run on a copy until
  a fresh-context review approves and the user gives the go-ahead (CLAUDE.md rule).

## 6. Token budgets (chars; ≈ tokens at /4)

| Item | Today | v4.8 |
|---|---|---|
| Session start, delivered | 2,277 (useless preview) | MEMORY.md block ≤ 12,000 (attached by the host, fully visible) + header ≤ 600 |
| After compaction | 2,277 preview | header + 15 handoff lines ≤ 1,500; MEMORY.md re-attached by the host |
| Per prompt | 0 | 0, or ≤ 2,000 when ≥ 1 entry qualifies |
| Journal cadence (×10 / 100 turns) | ≈ 3,000–3,300 follow-on each | unchanged |
| Review cadence (×6 / 100 turns) | 15,000–28,000 each | ≤ 4,500 each |
| Skill listing per session | 9,153 | ≤ 3,000 |
| Follow-on per 100 turns | 122k–203k | ≤ 60k |

## 7. Testing and verification

### 7.1 Unit tests (all under scratch `GOWTH_MEM_HOME`)

- `test_memfile.py`: budget by lines and chars; shrink order; free-zone preservation
  byte-for-byte (including a file without markers and a file with only a begin marker); hash
  gate (three writes, one rewrite); determinism across two renders; handoff digest ordering on
  the trade/idol-ai shapes (mixed order, 52 KB headerless block); topic index from frontmatter;
  secrets pointer extraction never emits a value (fixture with fake keys).
- `test_native.py`: merge into an existing `settings.local.json` preserving other keys; `already`;
  `conflict` without force; `tracked` on a git-tracked file (temp repo); `~/` rewriting;
  import dry-run vs apply, clash renaming, unmapped slugs skipped, sanitizer applied.
- `test_recall_prompt.py`: bash gate cases (slash, bang, short, disabled, subagent); every
  qualifying entry has ≥ `min_terms` terms; caps by entries and chars; per-session dedupe; 20
  generic prompts inject nothing (fixture vault seeded from the audit's 10 entries + 20 more);
  empty stdout on missing index; growth probe 10k/20k/40k for the prompt normaliser on the four
  adversarial shapes required by CLAUDE.md.
- `test_hook_limits.py`: drives every emitter with an oversized vault and asserts `< 9000`
  chars on stdout, and that `clamp_context` cuts at a line boundary with the marker.
- `test_index_incremental.py`: new file, modified file, 200-file cap, workspace without
  `workspace.json`, `research/` excluded by default and included with the flag.
- `test_review_directive.py` (extends `test_dispatch_contract`): the judge prompt is verbatim
  in the directive; the template's 3-line contract is present at top and end; the directive
  never exceeds 1,500 chars with two log paths.
- `test_settings_accessor.py`: string `"false"` is False for every boolean knob; a malformed
  sibling leaves the other keys at their values; the example file contains only keys the code
  reads (reverse check: every documented key is read somewhere).
- Existing suites keep passing on Python 3.9 and 3.x (CI matrix).

### 7.2 Real-binary E2E (scratch project + scratch vault, `claude -p --setting-sources project`)

1. A seeded `MEMORY.md` block of 130 lines / 12 KB is fully visible (END marker reported).
2. SessionStart header is visible and < 9,000 chars; the fallback bootstrap at 8,500 chars is
   fully visible.
3. A seeded query prompt receives the `[gowth-mem:recall …]` block; a generic prompt receives
   nothing (hook stdout empty, `hookAdditionalContext` absent in the transcript).
4. Compact path: a transcript with a forced compaction shows the `AutoMem` attachment after the
   boundary and a hook delta < 1,500 chars (replayed through the hook with a synthetic
   `source=compact` event; the attachment behaviour is already proven on live transcripts).
5. Stop: the judge directive text in the transcript equals the template contract.

### 7.3 Recall calibration and probes

- Probe set: the audit's 10 seeded entries (seed 42), 20 more sampled with seed 43 across all
  workspaces, 20 generic prompts (continue/fix tests/ok làm đi/…). Metrics: hit@1, hit@3, MRR,
  precision of injected entries, injections on generic prompts.
- `score_threshold` default = the least negative bm25 value at which the 20 generic prompts
  inject nothing and precision ≥ 0.8; recorded in the settings example with the date and probe.

### 7.4 Pre-release checklist

`py_compile` + full suite on 3.9 and 3.14; `bin/test-install.sh`; E2E 7.2; probes 7.3; token
per-100-turns recomputed with the audit's method; fresh-context review of the whole diff
(≥ 1 round, all HIGH/MED fixed); every new path exercised on real data on a vault copy; CLAUDE.md,
SHIPPED-FEATURES, commands, templates updated with counts filled in last.

## 8. Rollout on the owner's machines

1. Release v4.8.0; update the plugin on this Mac; end the turn for `/reload-plugins`.
2. `/mem-setup native --dry-run` → review the project list → `/mem-setup native` (writes
   `settings.local.json` in the mapped projects) — user go-ahead required.
3. `/mem-setup native --import --dry-run` → review → `--import` (23 native memory directories)
   — user go-ahead required.
4. New sessions in those projects load `MEMORY.md`; `/mem-cost` shows delivered sizes.
5. sh1su102: `/plugin marketplace update gowth-mem`, `/plugin update gowth-mem@gowth-mem`,
   `/reload-plugins`, then steps 2–3 there.

## 9. Follow-ups (out of scope for v4.8)

- index.db bloat (70% archive rows, 135 MB): separate archive table or vacuum.
- state.json holds 1,091 sessions and is rewritten per Stop: prune sessions older than 30 days.
- Retire `--prune-junk` once every peer runs ≥ 4.7.6; record the plugin version per machine in
  the commit trailer so healthy machines can see stale peers.
- Reduce Stop-hook model re-invocations (13 per 100 turns) by batching cadences.
- The remaining v4.7.6 follow-ups (ensure_topic on a domain, symlink walks in `_forget --aspects`
  and `[skill-ref]`, `--route` traceback, `_commitmsg` lesson count).
