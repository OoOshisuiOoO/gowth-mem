# gowth-mem v4.7.2..v4.7.6 — holistic review against G1 (remember) and G2 (main-context tokens)

Reviewer: code-reviewer teammate, fresh context, read-only. HEAD = v4.7.6 = 3442bcf.
Range: 8 commits (43d62b5 … 3442bcf), 30 files, +5,054/−325.

**Evidence sources**
- The repo at both tags.
- The suite, run 5× under an isolated HOME, plus once on 3.9.
- A scratch vault at `…/1c16482b…/scratchpad/review-vault/holistic-probe`.
- Read-only analysis of the lead transcript 1c16482b (19.9 MB, 9.7k records).
- This session's own SessionStart bootstrap.
- Size and headings only of the live `~/.gowth-mem/workspaces/personal/journal/2026-09-28.md`.

Nothing was written under `~/.gowth-mem`. `lsp_diagnostics` was unavailable (`ty` server not installed), so I used `py_compile` (47/47 OK) and an AST unused-import scan instead.

## Code Review Summary

- **Files reviewed:** 30 changed in the range, plus the unchanged G1/G2 path: `bootstrap-load.py`, `precompact-flush.py`, `session-start.sh`, `precompact.sh`, `templates/AGENTS.md`, `templates/self-review-instructions.md`.
- **Total issues:** 12, none CRITICAL.
- I found no correctness regression in the range's own code. As instructed, I did not re-hunt for defects.

### By Severity
- CRITICAL: 0
- HIGH: 2 — both are goal-level leaks on paths the range did not touch
- MEDIUM: 6
- LOW: 4

## Ranked findings

### [HIGH] H1 — One review cadence costs about 7× its contract in the MAIN context (G2)
**Files:** `hooks/scripts/auto-journal.py:381-387` (review directive) vs `:294-301` (journal directive); `templates/self-review-instructions.md:168-174`. **Confidence:** HIGH (measured).

**Evidence** (lead transcript 1c16482b; `#` = record number):
- **The journal directive dictates the teammate prompt verbatim.** Those dispatches are 628–632 chars.
- **The review directive dictates no prompt.** On each fire the main context:
  - invoked the user's `session-insights` skill (Skill call #4486; the 3,569-B SKILL.md was loaded at #4494);
  - composed its own judge prompts (1,995 and 1,762 chars at #4499 and #8202), both importing session-insights instructions.
- **The judge returned a full report instead of 3 lines.**
  - Notifications: 12,695 and 12,363 chars (#4942, #8416).
  - Relays: 12,490 and 12,452 chars (#4945, #8423).
- **Cost per review fire:**
  - Observed: 761 (directive) + 3.6k (skill) + ~1.9k (prompt) + 1,070 (launch ack) + ~12.5k (notification) + ~12.5k (relay) ≈ **32k chars (~8k tokens)**.
  - Contract: ≈ 4.5k chars.
- **The stable trigger token invites this.** `auto-journal.py:718-721` deliberately keeps `[gowth-mem:self-review` as the token external review skills key on, so any similar skill reproduces the inflation.

**Fix:** make the review directive dictate the judge prompt verbatim, like the teammate's. It should say:
- "Your final message is exactly 3 lines."
- "The long report goes in the review block via `--append-review`."
- "Do not load other review skills in the main context."

### [HIGH] H2 — Bootstrap's raw-journal slot delivers the day's OLDEST, machine-dominated dump (G1 + G2)
**Files:** `hooks/scripts/precompact-flush.py:67-104, 122-161`; `hooks/scripts/bootstrap-load.py:73-84, 131-135`. **Confidence:** HIGH (measured).

**Evidence:**
- **It is the only raw file every new main context receives.** `journal/<today>.md` is a bootstrap delta.
- **Its writer, precompact-flush, never received the v4.7.3 classification.**
  - `user_turn_count` = 77 on the lead transcript.
  - The same 77 records under `_capture._classify_record`: 7 human, 33 machine, 11 unconfirmed, 26 non-prompt.
  - At EOF, the 20k dump window is 84% machine text (4,970 of 5,889 chars).
- **`_format_block` keeps `raw[:4000]`.** That is the HEAD of an append-chronological file.
- **This session's bootstrap shows the damage.** It loaded the 10:23:01 dump, which opens with the relayed 12k judge report. It omitted 31,515 chars, including the 14:08:18 dump (live file 37,876 B; dump headings at lines 3 and 199).
- **Consequences:**
  - H1's relay is paid again in every later session that day.
  - At the documented 32.5 sessions/day, the 4.2k slot is ≈34k tokens/day on stale text.
  - The freshest state is exactly the part that gets cut.
- **Scope note:** `docs/SHIPPED-FEATURES.md:473` verified head-truncation only for the newest-FIRST `handoff.md`, and a test pins that behaviour. Keep it for handoff.

**Fix:**
- precompact-flush should reuse `_capture._classify_record` / `prompt_text` to drop machine records and notification relays, and cap each assistant chunk at ~300–500 chars.
- Bootstrap should load the newest section of the journal (tail), not its head.

### [MEDIUM] M1 — Boolean and partial-parse settings reads can silently flip privacy opt-outs
**Files:**
- `auto-journal.py:199-209, 329-348`
- `_sync.py:318`
- `_forget.py:434, 454-455`
- `_capture.py:618`
- `_gate.py:153, 158`
- `_tags.py:564`
- `_index.py:759`
- `bootstrap-load.py:183, 186`
- `_topic.py:677`
- `_lesson.py:113`
- `_home.py:133`

**Confidence:** HIGH (scratch-vault probe).

**Evidence:**
- **Coercion is inconsistent.** `_coerce_bool` exists only in auto-journal.py (6 call sites). 13 other boolean reads use `bool()` or plain truthiness, so the string `"false"` is True.
- **The string `"false"` reads as True:**
  - `{"sync":{"auto_sync_on_stop":"false"}}` → autosync stays ON (the vault keeps pushing).
  - `{"topic_layout":{"auto_archive_enabled":"false"}}` → aspect archival turns ON.
  - `{"reflection":{"capture_thinking":"false"}}` → True.
- **One malformed sibling resets the whole tuple to defaults:**
  - `{"reflection":{"enabled":false,"turn_interval":"15m"}}` → enabled=True and capture_on=True. Raw-turn capture, which syncs to the remote, comes back on.
  - The same happens with `min_review_turns:"ten"`.
  - It also happens with `auto_journal_enabled:false` plus `journal_every:"10x"`.

**Fix:** add one typed accessor in `_home` (`setting("reflection.enabled", bool, True)`) with per-key fallback and `_coerce_bool`, and use it everywhere.

### [MEDIUM] M2 — 62% of the documented settings surface does nothing
**Files:** `templates/dot-gowth-mem/settings.example.v3.json`; `README.md:264, 282-283`. **Confidence:** HIGH.

**Evidence:** the example documents 82 leaves; 51 of them are read by no code:
- language, version
- workspace.create_if_missing
- topic_layout.{mode, readme_filename, aspect_filename_pattern, lessons_filename, reserved_aspect_names, reserved_subdirs, promote_lines, lazy_nest_threshold, max_depth, live_state_window_days}
- topic_routing.{auto_create_threshold, default_aspect}
- slug.{regex, aspect_regex, unique_scope, index}
- moc.* (4)
- embedding.{provider, model}
- retrieval.{fts5_top_k, jaccard_min_score, jaccard_top_k, jaccard_n}
- context_budget.{budget_chars, head_chars_per_file}
- compression.* (4)
- contradictions.* (3)
- recall.* (10, incl. layer_scores)
- migration.* (2)
- conflict_resolution.mode

The README advertises "compression.enabled … /mem-save and journal writers pipe through _compress", recall limits, an embedding provider and a conflict mode. Nothing reads any of them.

**Fix:** delete the unused keys from the example, or move them to a "reserved/unused" note. Add the missing real knobs (see Q2).

### [MEDIUM] M3 — The two Stop-path functions became god functions
**Files:** `auto-journal.py:517-749` (main); `_capture.py:578-778` (capture_turn). **Confidence:** HIGH.

**Evidence:**
- **Size and complexity:** `main` grew 162 → 233 lines (cc ≈32 → ≈48); `capture_turn` grew 116 → 201 lines (cc ≈32 → ≈56).
- **Repetition in `main`:** 7 identical no-op prints (lines 594, 601, 610, 638, 643, 748, 762).
- **Repeated settings parsing:** `read_settings()` runs 5× per Stop (lines 202, 220, 320, 337, 670).
- **Duplicated between the two modules:**
  - `_TURN_RE` (auto-journal.py:118 / _capture.py:49)
  - the sid8 derivation (auto-journal.py:252 / _capture.py:734)
  - the session-log path (auto-journal.py:249-254 / _capture.py:735)
- **This is not a latency problem.** The hook runs in 0.05–0.11 s on a 19.9 MB transcript.

**Fix:**
- Extract parse, identity, bump and fire helpers, plus one `_noop()`.
- Take one settings snapshot per Stop.
- Have `capture_turn` return the path it wrote.

### [MEDIUM] M4 — Three capture-privacy tests are vacuous
**Files:** `tests/test_turn_counting.py:735, 744, 749` (helper at `:282-286`). **Confidence:** HIGH (mutation).

**Evidence:**
- **`log()` and `line()` return "" when the log is missing.** With `capture_turn` mutated to `return False`, all three still PASS. The positive control `test_final_answer_fills_the_claude_line` fails, as it should.
- **An erase-everything sanitizer also passes.** With `_sanitized` mutated to blank every field, 7 of the 17 tests in TestCaptureWindowPrivacy + TestCaptureCompleteness pass, including `test_privacy_v475.py:272`. Sibling tests in those classes do catch over-redaction.
- This breaks CLAUDE.md rule 4: positive signal, never absence alone.

**Fix:** assert that the line exists, that `[REDACTED:gitlab-pat]` is present, and that the neighbouring prose survived.

### [MEDIUM] M5 — The vault AGENTS.md misdescribes the bootstrap to every session (G1)
**Files:** `templates/AGENTS.md:20-26` vs `bootstrap-load.py:131-135` and `:54-57`. **Confidence:** HIGH on the mismatch; MEDIUM on its behavioural effect.

**Evidence:**
- **What sessions are told is loaded:** shared/files.md, _MAP.md, docs/{exp,ref,tools,files}.md, the top-3 topic READMEs, journal/{today,yesterday} and skills/_index.
- **What the loader injects:** AGENTS, secrets, tools, the workspace AGENTS.md, handoff.md and today's journal only.
- **`DEFERRED_NOTICE` and `commands/mem-cost.md:36` agree with the code.**
- **Why it matters for G1:** a model told that topic knowledge is already in context has less reason to call /mem-recall. Topic entries — where all of the range's write-path work lands — are reachable only through recall.

**Fix:** rewrite §3 to match `_plan()` and add "for topic knowledge, run /mem-recall".

### [MEDIUM] M6 — The junk-prune repair is 313 lines, and stale peers stay invisible
**Files:** `hooks/scripts/_validate.py:214-526`; `tests/test_junk_topics.py` (408 lines); `_version.drift_nudge`. **Confidence:** MEDIUM–HIGH.

**Evidence:**
- **It is a one-off repair for a bug fixed in the same release.** It carries rename-aside, pid liveness, abandoned-hold recovery and conflict copies.
- **It stays necessary while any peer runs < 4.7.6.** Per the handoff, sh1su102 is on 4.7.2 and still minting junk folders.
- **The drift notice cannot reach that peer.** It prints only in the stale machine's own bootstrap, so no healthy machine can see that a peer is stale.

**Fix:**
- Record the plugin version per machine in the vault, e.g. a `Plugin: vX` trailer beside the existing `Machine:` trailer in `_commitmsg`.
- Have bootstrap flag stale peers from the git log.
- Retire `--prune-junk` once no peer is stale, and keep `junk_topic_folders()` as a scan-only lint.

### [LOW] L1 — Duplicated primitives
**Confidence:** HIGH.
- **Path-escape guards: 5 implementations.**
  - `_atomic.py:55`, `_topic.py:317`
  - new in the range: `_moc.py:247`, `_validate.py:290`, `_topic.py:456`
- **Frontmatter parsers: 6 or more.** Canonical `_frontmatter.py:93`, plus `_validate.py:54`, `_migrate_v3.py:79`, `_index.py:79`, `_tags.py:463/664` and `_research.py:193`.
- **state.json has one unlocked writer.** It is written under `file_lock("state")` in 4 places, and unlocked by `_sync._record_autosync` (`_sync.py:292-305`). That can drop a concurrent Stop's `last_prompt_key` or counters. It is rare, because autosync is debounced to 30 min.

**Fix:** one `inside(path, root)` helper, one frontmatter parser, and one locked state updater.

### [LOW] L2 — Dead code
**Confidence:** HIGH.
- **10 unreferenced functions (~36 lines):**
  - `_home.py:160` and `:354-370` (six path wrappers)
  - `_topic.py:253` (`detect_section`)
  - `_dedup.py:56`
  - `_topic_templates.py:107`
  - `_wikilink.py:147`
- **2 functions used only by tests:** `_query.py:322`, `_topic.py:175`.
- **14 of 123 CLI flags are referenced nowhere:**
  - `_commitmsg` `--cwd`, `--host`, `--context`
  - `_forget` `--aspect-threshold-days`, `--keep-newest`
  - `_handoff` `--max-age-days`
  - `_index` `--no-archive`
  - `_lexical` `--min`, `--top`
  - `_review_ledger` `--min-bytes`, `--idle-minutes`, `--min-turns`
  - `_tags` `--max`
  - `_workspace` `--description`
- **Unused imports in range-touched files:**
  - `_topic.py:57` (RESERVED_FILES, RESERVED_TOPIC_FILES, topics_dir)
  - `auto-journal.py:111` (list_workspaces)
  - `_moc.py:34-36`
  - `_privacy.py:187` (Path)
  - `_sync.py:24` (Optional)

### [LOW] L3 — Stale docs on the G1/G2 path
**Confidence:** HIGH.
- `CLAUDE.md:33, 58, 74` describe SRS resurfacing, MMR and "state.json SRS data". None of that code exists.
- `CLAUDE.md:34` says "Bootstrap ≤60k chars". The real cap is 15k.
- `precompact.sh:2-3, 9, 16` still describe HARD-BLOCK / exit 2, which was removed in v3.5.1.

See Q5 for the full list.

### [LOW] L4 — Suite hot spot
**Confidence:** HIGH.
- `test_review_trigger` takes 11.9 s of the 39.0 s total (31%), because every simulated Stop spawns a hook subprocess.
- The LegacyChannel subclass (`test_review_trigger.py:695`) re-runs the whole cadence class; its two slowest tests alone take 2.6 s.
- **Fix:** run the legacy channel on one representative test per behaviour.

## Q1 — Cohesion & complexity

**Line counts** (`git show <tag>:path | wc -l`):

| file | v4.7.2 | v4.7.6 | delta |
|---|---|---|---|
| auto-journal.py | 632 | 766 | +134 |
| _capture.py | 375 | 850 | +475 (2.3×) |
| _topic.py | 601 | 835 | +234 |
| _privacy.py | 152 | 241 | +89 |
| _validate.py | 259 | 590 | +331 (2.3×) |
| _tags.py | 672 | 726 | +54 |
| **six files** | **2,691** | **4,008** | **+1,317 (+49%)** |

All `hooks/scripts/*.py`: 13,482 → 14,927 (+10.7%). `tests/*.py`: 8,331 → 11,162 (+34%).

**Functions over 80 lines at v4.7.6:** 13 of 512.
- **In the range:**
  - `auto-journal.main` 233 (was 162)
  - `_capture.capture_turn` 201 (was 116)
  - `_moc.rebuild_topic_readme` 130 (was 121)
  - `_lesson.append_lesson_status` 95 (new; replaced `append_lesson`, 78)
  - `_topic.append_entry_status` 94 (replaced `append_entry`, 84)
- **Unchanged:** `_sync.main` 148, `_commitmsg.build_message` 143, `_dream.run` 129, `_index.main` 127, `_migrate_v3.classify` 123, `_dream._run_phases` 118, `_query._run_query` 102, `_migrate_v3.migrate` 85.
- **Improved:** `_topic.route` went 100 → 22 lines, because selection moved into `_pick_topic` (79 lines). That unified three drifting selectors.

**Duplicated logic:**
- bool coercion and settings reads (M1): about 20 ad-hoc section reads, 1 strict coercer against 13 bare ones
- lock/fallback patterns (L1)
- path-escape guards (L1)
- frontmatter parsing (L1)
- session-log path, sid8 and `_TURN_RE` (M3)
- human/machine prompt classification: implemented in `_capture`, not reused by precompact-flush (H2)

**Dead code:** see L2 (functions, flags, imports) and M2 (51 dead settings keys).

## Q2 — Contract surface (settings)

**The code reads 36 keys:**
- layout_version
- auto_journal.{journal_every, auto_journal_enabled}, plus legacy top-level journal_every / auto_journal_enabled
- journal.{auto_forget_enabled, raw_ttl_days, max_bytes, salvage}
- reflection.{enabled, capture_enabled, turn_interval, min_review_turns, max_prompt_chars, max_thinking_chars, capture_thinking}
- gate.{enabled, strict, english_only}
- tags.{enabled, max_per_entry, max_frontmatter}
- topic_routing.{min_keyword_overlap, default_topic}
- topic_layout.{archive_threshold_days, auto_archive_enabled}
- workspace.{auto_detect_from_cwd, default}
- retrieval.{use_budget_planner, index_archive}
- context_budget.{enabled, tier_weights, recency_half_life_days}
- sync.{auto_sync_on_stop, min_interval_minutes}
- doctor.auto_heal — read by grep in `session-start.sh`, which matches `"auto_heal": false` at any nesting level, not just under `doctor`

**31 of the 36 are in `settings.example.v3.json`. The 5 that are not:**
- `gate.english_only` — only in `templates/AGENTS.md` and CLAUDE.md
- `topic_layout.auto_archive_enabled` — only in `commands/mem-forget.md` and `templates/AGENTS.md`
- `reflection.capture_enabled` — deliberately absent; described in `_notes.reflection` and `commands/mem-review.md`
- legacy top-level `journal_every` and `auto_journal_enabled` (`auto-journal.py:204-206`) — documented nowhere

**Other contract gaps:**
- **Default mismatch:** `archive_threshold_days` is 180 in the example (line 59) but 90 in code (`_forget.py:53`) and in the docs.
- **The range added no settings key.** It only clamped `reflection.max_prompt_chars` to 8192 (`_capture.py:46, 613`), which is documented only in CLAUDE.md and SHIPPED-FEATURES.
- **Reverse direction:** 51 of the 82 documented keys are dead (M2).

## Q3 — Goal fit and per-session MAIN-context cost

Unit: chars (≈4 chars/token for English; Vietnamese is denser). Measurements come from the lead transcript unless noted.

| mechanism | goal served | main-context cost | range effect |
|---|---|---|---|
| Turn counting (v4.7.3) | G2 (+G1 capture accuracy) | 0 (runs in the hook) | Cost DOWN — removes spurious fires. The judge's report in today's journal says the pre-fix counter read 19 turns for 2 real prompts. SHIPPED-FEATURES: 474 → 327 counted, reviews 26 → 12 on 15 heavy sessions |
| Session capture (v4.7.3–5) | G1 (turn source for teammate and judge; synced) | 0 in main; subagents read ≤2,000 (User) + 300 (Claude) + 500 (Actions) chars per turn | Subagent input UP: User joins every ask; Claude now carries the answer |
| Journal delegation directive | G1 (distils into topics and handoff); G2 vs inline | ≈3.8k chars (~1k tokens) per fire, every 10 real turns: 750 directive + 630 dispatch + 1,070 launch ack + 919–1,133 notification + 330–438 relay, plus 2 extra model invocations. Inline fallback: 7.5 KB template + 10–20 tool calls | Text unchanged. Template +5 lines (~470 chars), read by the teammate; by main only on the inline fallback |
| Judge / self-review | Neither G1 nor G2 (metacognition; weak G1 via [reflection]) | Contract ≈4.5k chars per fire; observed ≈32k (H1). Every 15 real turns, plus a 290-char header when both cadences fire | Per fire unchanged; fewer fires |
| Privacy sanitizer (v4.7.5) | Data safety (precondition for syncing G1 data) | 0 | none |
| Topic identity (v4.7.6) | G1 (entries land in the real topic; MOC and recall find them) | 0 (the teammate sees written / duplicate / rejected:<rule>) | none |
| Junk prune (v4.7.6) | G1 hygiene, one-off | 0 (manual CLI) | none; maintenance +313 code / +408 test lines |
| Tags (v4.7.6) | G1 (recall keywords) | 0 | none |
| Drift notice (v4.7.6 reload variant) | G1 (stale-machine visibility) | 7 chars/session header tag; 429 chars (reload variant) or 561 (upgrade variant) only while drifted | DOWN (429 vs 561) |
| Stop channel (v4.7.3) | UX; G2-neutral | identical text | neutral |

For scale (not in the range): this session's bootstrap was 15,589 chars — AGENTS 4,034, secrets 1,848, workspace AGENTS 1,189, handoff 4,034, today's journal 4,193.

**What INCREASED per-session cost in the range:** nothing in the main context.
- The directive strings are byte-identical across the range.
- `bootstrap-load.py`, `precompact-flush.py`, `session-start.sh` and `self-review-instructions.md` are unchanged.
- The increases are confined to subagent contexts: the +470-char teammate template and the richer session-log lines.
- The net main-context effect is a decrease: fewer fires and a shorter drift notice.

The largest main-context costs — H1 (≈32k per review fire) and H2 (≈4.2k of stale dump per session) — sit on paths the range never touched.

## Q4 — Test suite

- **Runtime:** 764 tests, 0 failures, 0 skips.
  - Python 3.14.6: 39.0 s (5 full runs: 38.8–40.1 s).
  - Python 3.9.21: 36.2 s.
- **10 slowest:**
  1. 1.81 s `test_review_trigger.TestCounterIndependence.test_journal_and_review_cadences_do_not_collide`
  2. 1.80 s `test_review_trigger.TestCounterIndependenceLegacyChannel.test_journal_and_review_cadences_do_not_collide`
  3. 0.85 s `test_review_trigger.TestCounterIndependence.test_review_only_stop_15_has_no_journal`
  4. 0.83 s `test_review_trigger.TestCounterIndependenceLegacyChannel.test_review_only_stop_15_has_no_journal`
  5. 0.80 s `test_privacy_v475.TestPrivateKeyBodies.test_key_body_scan_uses_bounded_memory`
  6. 0.77 s `test_review_trigger.TestReflectionDisabled.test_disabled_no_review_no_capture_journal_inline`
  7. 0.76 s `test_regressions.AutoSyncRebaseTests.test_pull_rebase_auto_stashes_dirty_tree_and_restores`
  8. 0.57 s `test_migrate_v3.MigrateV3Tests.test_idempotent_short_circuit_on_second_run`
  9. 0.50 s `test_privacy_v475.TestPrivateKeyBodies.test_linear_time_on_hostile_input`
  10. 0.40 s `test_review_trigger.TestV471PausedNotice.test_notice_not_repeated_after_a_real_fire`
- **Module totals:** review_trigger 11.9 s, turn_counting 5.2 s, junk_topics 4.5 s, topic_identity 3.1 s, privacy_v475 1.8 s. Modules added or grown in the range account for ≈15 s (38%).
- **Order dependence:** none. Reverse order and cross-module shuffles (seeds 1 and 7) all gave 0 failures; all 44 modules pass when run alone.
- **HOME / real vault:** none touched. The suite ran with HOME pointed at an empty fake home and at a populated one (`.gowth-mem` plus `.claude/plugins`); 0 files were created or modified. 37 of 44 modules set `GOWTH_MEM_HOME` themselves; the other 7 test pure functions.
- **Absence-only assertions:** 120 of 753 test methods (AST heuristic), plus 23 methods with no assertion call (mostly "never raises" or compile smoke tests). Most are legitimate negatives on pure functions. The 3 proven vacuous are in M4.

## Q5 — Docs drift (15 spot checks: 8 MATCH, 7 DRIFT)

| # | claim | verdict | evidence |
|---|---|---|---|
| 1 | CLAUDE.md:81 "full suite (764 tests)" | MATCH | 764 ran |
| 2 | CLAUDE.md:103-104 "39 commands / 13 skills" | MATCH | 39 / 13 on disk |
| 3 | CLAUDE.md "CI: py_compile + unittest on 3.9 and latest 3.x" | MATCH | `ci.yml:19-29` |
| 4 | v4.7.5 "max_prompt_chars clamped to 8192" | MATCH | `_capture.py:46, 613` |
| 5 | v4.7.4 "narration[:148] … answer[:149]" | MATCH | `_capture.py:713-716` |
| 6 | v4.7.6 "caps 64 prose / 128 identifier" | MATCH | `_tags.py` MAX_TAG_LEN / MAX_PRIORITY_TAG_LEN |
| 7 | v4.7.6 "`--append` prints written \| duplicate \| rejected:<rule> \| rejected:unroutable" | MATCH | `_topic.py:651-744, 823-825` |
| 8 | commands/mem-lesson.md:33 CLI output forms | MATCH | `_lesson._cli`; the ValueError form "not appended: <exc>" with exit 2 is undocumented |
| 9 | precompact.sh:2-3, 9, 16 "may HARD-BLOCK with exit 2" | DRIFT | precompact-flush has never exited 2 since v3.5.1; CLAUDE.md's "never blocks" is the true statement |
| 10 | CLAUDE.md:34 "Bootstrap ≤60k chars" | DRIFT | cap is 15,000 (`bootstrap-load.py:46`); `bootstrap-load.py:128` itself calls 60k wrong |
| 11 | CLAUDE.md:33/58/74 "SRS resurfacing, MMR, state.json SRS data" | DRIFT | no srs / resurfac / mmr code anywhere; state.json holds counters, forget_last_run and last_autosync |
| 12 | templates/AGENTS.md:20-26 bootstrap order | DRIFT | `bootstrap-load.py:131-135` (M5) |
| 13 | self-review-instructions.md:168-174 "3 lines are your entire final report" | DRIFT in practice | 12.7k and 12.4k-char reports (H1) |
| 14 | README.md:264, 282-283 (compression.enabled "pipes /mem-save + journal writers", recall limits, embedding provider, conflict mode, layer_scores) | DRIFT | no reader for any of them |
| 15 | templates/AGENTS.md:244-245 + mem-forget.md:15 "aspects older than 90 days are auto-archived" | DRIFT | gated by `auto_archive_enabled` (default False, `_forget.py:454-455`, absent from the example); the example scaffolds 180 days |

**Bonus check:** `auto-journal-instructions.md:20-21` ("relay its 1-line confirmation") roughly MATCHES in practice: notifications are 919–1,133 chars, relays 330–438 chars.

## Q6 — Five simplifications (most code or token savings, least risk)

1. **Verbatim judge prompt plus a 3-line cap** (text-only change).
   - Saves ≈25–28k main-context chars per review fire in the deployed setup (H1).
   - Safe with: `test_dispatch_contract.py` (extend the reason/template mirroring checks to the judge), `test_review_trigger.py` (reason text), `test_stop_channel.py`.
2. **precompact-flush reuses the `_capture` classification plus a per-chunk cap; bootstrap tail-loads today's journal only** (handoff stays head-truncated, which a test pins).
   - Removes precompact's duplicate `user_turn_count` / `_extract_text` (~50 lines).
   - Keeps up to 4k chars of machine and relay text out of every bootstrap, and delivers the newest state instead (H2).
   - Safe with: `test_precompact_raw_dump.py`, `test_turn_counting.py` (Transcript fixtures), and `test_bootstrap_budget.py:126` extended to assert that the newest section is the one loaded.
3. **One typed settings accessor, plus deleting the dead keys from the example.**
   - Removes about 20 ad-hoc reads and fixes the 13 truthiness sites and the partial fallback (M1, M2).
   - Safe with: `test_review_trigger.TestV471BoolCoercion`, `test_dispatch_contract.TestSettingsExamplePrivacy`, `test_autosync_cadence`, `test_archive_sweep.ArchiveSettingTest`, `test_tags`, plus one malformed-sibling test per knob.
4. **Retire the junk-prune machinery once every peer runs ≥ 4.7.6.**
   - Removes 313 code lines and 408 test lines; keep `junk_topic_folders()` as a scan-only lint (M6).
   - Safe with: `test_topic_identity.py` (proves the router can no longer mint `<topic>-<aspect>/`) and `test_validate.py` (`--scan` / `--fix` unchanged).
5. **Decompose `auto-journal.main` and `capture_turn`; dedupe `_TURN_RE`, sid8, the log path and the 7 no-op prints.**
   - About 60–80 fewer lines and ~40% lower cyclomatic complexity; no token change (M3).
   - Safe with: `test_turn_counting.py` (faithful Stop replay), `test_review_trigger.py`, `test_stop_channel.py`, `test_capture.py`, `test_privacy_v475.py`, `test_hook_wrappers.py`.

## Open Questions (lower-confidence; surfaced, not blocking)
- **[HIGH, confidence MEDIUM]** H1's magnitude depends on the user's `session-insights` skill. Any user whose review skill keys on `[gowth-mem:self-review` will see the same inflation, but by an amount that varies.
- **[MEDIUM, confidence LOW]** Whether M5 actually makes the model skip /mem-recall. This is inferred, not observed.
- **[LOW, confidence LOW]** How often the L1 state.json race hits in practice.

## Positive Observations
- **The suite is solid.** 764 tests green on 3.9 and 3.14, order-independent, module-isolated and HOME-isolated. The positive-signal tests (e.g. `test_final_answer_*`) did catch a broken capture under mutation.
- **The Stop hook is cheap.** 0.05–0.11 s against a 19.9 MB transcript.
- **The range kept main-context cost flat or lower.** It added zero directive text, cut spurious cadence fires, and replaced the 561-char drift notice with a 429-char one.
- **Topic routing is simpler and safer.** `_pick_topic` ends the three-way selector drift and `route()` shrank from 100 to 22 lines. The decide → check → create split means refused writes no longer leave junk behind.
- **Privacy got real engineering.** Linear scanners with differential fuzz, a clamped capture window, and sanitize-before-collapse/cap.

## Recommendation
COMMENT on the range as shipped — no regression was found in its code. REQUEST CHANGES scoped to H1 and H2 as the top priority for the next cycle; both predate the range and both are goal-level HIGH at HIGH confidence.

## Verdict (3 lines)
1. The range is healthy as shipped: tests, Stop latency and main-context token cost are all fine or better, and nothing regresses G1 or G2.
2. Complexity grew 49% in the six core files. Nearly all of it bought write-path correctness (capture, routing, privacy, tags) that feeds subagents and /mem-recall — not what the next session's main context actually sees.
3. The biggest G1/G2 wins now sit off the range's paths: H1 (the review relay, ≈32k chars per fire) and H2 (the bootstrap journal slot holds the oldest, machine-heavy dump). Fix both before investing further in the write path.
