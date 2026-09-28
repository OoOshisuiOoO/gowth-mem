# SHIPPED FEATURES — gowth-mem roadmap log

History of what landed, in which version, and why. This is the audit trail for `RESEARCH.md`'s
catalog: any item with `✅ SHIPPED` in that catalog points here.

Live, unreleased items live in `RESEARCH.md` under the unshipped tiers; once shipped they migrate
here with a version tag.

## v0.4 roadmap (priority by ROI)

### Tier 1 — ship now (no new deps)

1. **mem0 ADD/UPDATE/DELETE/NOOP** in mem-distill skill — prevents dedup bloat, file-size wins.
2. **Contextual retrieval** (was in recall-active.py — REMOVED v3.2 with the hook) — prepend heading/breadcrumb to each match line. Reported 35-67% reduction in retrieval failures.
3. **MMR diversity** (was in recall-active.py — REMOVED v3.2 with the hook) — when 3 hits cluster in same file, pick across files.
4. **Voyager skill library** convention — `docs/skills/<name>.md` with description + steps. Auto-loaded by recall when intent matches.
5. **Generative Agents reflection** — `/mem-reflect` reads journal, produces 1-3 high-level summaries to docs/exp.md or wiki/concepts.

### Tier 2 — needs light infra (sqlite-vec, embedding API)

6. ✅ **SHIPPED v0.6**: Hybrid BM25 + vector recall via SQLite FTS5 + sqlite-vec + RRF fusion. Auto-detects `OPENAI_API_KEY` / `VOYAGE_API_KEY` / `GEMINI_API_KEY`. Graceful 3-tier fallback: vector hybrid → FTS5-only → grep. Build/refresh via `/mem-reindex`.
7. **SKIPPED**: Semantic response cache (GPTCache pattern). Stale-answer risk for evolving code work; limited ROI for our retrieval-only path.
8. ✅ **SHIPPED v0.5**: Spaced resurfacing — `.gowth-mem/state.json` SM-2-lite tracker; ~25% prob per prompt resurfaces files unseen ≥7 days.

### Tier 3 — architectural

9. ✅ **SHIPPED v0.5**: Temporal facts — `valid_until: YYYY-MM-DD` and `(superseded)` markers; recall auto-skips invalid lines.
10. ✅ **SHIPPED v0.6 / REMOVED v3.2**: HyDE-lite — was exposed as opt-in `/mem-hyde-recall <question>` skill. Removed alongside `recall-active.py`; token cost > benefit in observed usage.
11. ✅ **SHIPPED v0.5**: Provider prompt caching guidance in `templates/AGENTS.md` § Token efficiency. Stable prefix (AGENTS / SECRETS / TOOLS / FILES) → cache hit; volatile suffix (handoff / journal / recall) → cache miss expected.

### Bonus shipped v0.5

12. ✅ **Token cost estimator** `/mem-cost` — char + token breakdown of bootstrap; warns if approaching 60k cap.

### Shipped v0.9 — strict schema + active auto-delete

After deep-read of mempalace internals (`general_extractor.py`, `dedup.py`, `knowledge_graph.py`, `fact_checker.py`), shipped 4 strictness upgrades:

16. ✅ **7-type strict schema with `[type]` prefix** — every promoted entry MUST be one of `[decision]`, `[preference]`, `[milestone]`, `[problem]`, `[fact]`, `[tool]`, `[secret-ref]`. Entries without prefix are dropped. Templates updated.

17. ✅ **Quality gates** in mem-distill — adapted from mempalace's `general_extractor`: <20 chars → DROP; code-only → DROP; `[fact]` without Source → DROP; vague/hedged → DROP; Jaccard ≥ 0.85 dup → NOOP.

18. ✅ **Active auto-DELETE** via `_prune.py` + `/mem-prune` (shortcut `memp`) — diverges from mempalace's invalidate-only: actually removes superseded / deprecated / expired / duplicate entries from disk. Skips `docs/journal/**` (permanent log). Audit trail relies on `git log`.

19. ✅ **Auto-prune in Stop hook** — `auto-journal.py` now runs `_prune.py` synchronously every 10 turns before yielding. Distill + prune happen together with no manual intervention.

Mempalace cross-reference (verified from source):
- `general_extractor.py` 5 types: decision, preference, milestone, problem, emotional. We dropped `emotional` (not relevant for code workspaces) and added `[fact]`, `[tool]`, `[secret-ref]` for our docs/ taxonomy.
- `dedup.py` uses cosine 0.15 threshold (~85% similarity) keeping longest. We use Jaccard 0.85 in pure stdlib (no embedding deps required).
- `knowledge_graph.invalidate()` sets `valid_to`, never deletes. We DELETE per user direction.
- `fact_checker.py` marks stale, doesn't delete. We DELETE.

### Shipped v0.7 — auto-trigger hooks (mempalace-inspired)

After studying [MemPalace](https://github.com/MemPalace/mempalace) (their `mempal_save_hook.sh` fires every 15 messages and BLOCKS the AI; `mempal_precompact_hook.sh` forces emergency save before compact), shipped 3 auto-trigger upgrades to remove manual skill invocation:

13. ✅ **Stop hook `auto-journal.py`** — counts user turns in `.gowth-mem/state.json` per session; every 10 turns, emits `decision: "block"` with full mem-distill instructions inline. Claude saves before yielding. Replaces manual `/mem-distill`.

14. ✅ **PreCompact upgraded to BLOCK** — was advisory `additionalContext`; now `decision: "block"` with full save instructions. Compact can't proceed until docs/* are flushed.

15. ✅ **SHIPPED v0.7 / REMOVED v3.2**: UserPromptSubmit intent → inline skill body — `user-augment.py` detected intents (save / skillify / reflect / bootstrap; English + Vietnamese) and injected FULL skill instructions inline. Removed v3.2 alongside `recall-active.py` + `system-augment.py`: per-prompt augmentation duplicated the SessionStart bootstrap and burned tokens on every turn.

Note on MemPalace storage: their plugin DOES use ChromaDB embeddings under the hood (verified from `mempalace/searcher.py` + plugin manifest keywords `chromadb`). What's distinctive is they store **verbatim text** with embeddings as the index — no summarization/paraphrasing. The "no manual skill" pattern was the actual lesson worth porting.

### Shipped v3.0 — topic-folder + dated-aspect layout

After studying OpenClaw's `memory/<topic>/` folder pattern + Generative Agents' episodic-memory-per-day, shipped a structural overhaul:

20. ✅ **v3 topic-folder layout** — `<ws>/<slug>/{00-README.md, YYYY-MM-DD-<aspect>.md, lessons.md}` replaces v2.4 `<slug>/<slug>.md`. `00-README.md` is the topic MOC (auto-rebuilt from frontmatter); dated aspects are append-only per-day notes; `lessons.md` is the per-topic ledger.

21. ✅ **`_topic.route()` returns dated aspect** — every memory write lands in `YYYY-MM-DD-<aspect>.md`, never `00-README.md`. Default aspect slug `note` when keyword extraction yields nothing. `derive_topic_slug()` available for callers that need the slug without spawning a file.

22. ✅ **6-tier wikilink resolution** — `<ws>/<slug>/00-README.md` (v3) → `<ws>/<slug>/<slug>.md` (v2.4 fallback) → `<ws>/<slug>.md` (v2.3 flat) → `<ws>/lessons.md` → `shared/<key>.md` → `[[ws:slug]]` cross-workspace. Multi-machine partial-migration safe.

23. ✅ **Layer-score buckets** — recall scores by path layout: today's dated aspect = 90, MOC = 80, lessons = 75, older dated = 70, research/ = 65, other in-folder = 60, shared/skills = 40.

24. ✅ **7-step migration pipeline** `_migrate_v3.py` — snapshot → classify → execute → verify → cleanup → rebuild metadata → bump+commit. Microsecond-resolution UTC backup stamps; short-circuit on repeat; `_atomic.atomic_write` guarantees parent.mkdir; fetch + ff-only before STEP 7 commit (graceful no-op when no remote configured).

25. ✅ **Rolling-2 backup window** — keep newest 2 backups; demote oldest after ≥24h. `bin/rollback-v3.sh` restores from any snapshot non-destructively (current state staged under `.backup/rolled-back-<utc>/` first).

26. ✅ **Reserved subdirs include `research/`** — `docs|journal|skills|research` blocked as topic slugs; `readme|lessons|00-readme` blocked as aspect slugs.

### Shipped v3.1 — agentmemory-derived hardening

Adopted from [rohitg00/agentmemory](https://github.com/rohitg00/agentmemory) (4-tier consolidation + auto-capture + privacy-first). Subset selected by scope: anything requiring Node/MCP/server infra is out of scope for our pure-stdlib hooks.

27. ✅ **`_privacy.sanitize()` regex filter** — redacts AWS / GitHub PAT (ghp, gho, ghu, ghs, ghr) / OpenAI (`sk-…`) / Anthropic (`sk-ant-…`) / Slack / Google / Stripe / JWT / SSH-private / generic `password|token|secret|api_key|…=value` shapes to `[REDACTED:<kind>]`. Also strips `<private>…</private>` blocks (any case, multiline) → `[REDACTED:private-block]`. Fails open on any internal exception (never blocks a write).

28. ✅ **Sanitize wired into write paths** — `_topic.py` (`ensure_topic_folder` 00-README write), `_lesson.py` (lessons.md append) sanitize their final body before `atomic_write`. Templates pass through unchanged; user-typed `summary`/`tried`/`fix`/`root` get scrubbed.

29. ✅ **`_dedup.py` short-window dedup** — SHA-256 of whitespace-normalized text against rolling 5-minute window (`~/.gowth-mem/.dedup-window.json`). Atomic `check_and_record()` for the journal/lesson auto-write path. Per-entry TTL expiry on every read. fcntl-locked; fails open on contention.

30. ✅ **`_audit.log_prune_delete()` JSONL audit** — `_prune.py` now writes one line per deletion to `~/.gowth-mem/.audit/prune-<YYYY-MM>.log` with `{ts, op, file, reason ∈ {expired, superseded, duplicate}, preview ≤80ch}`. Dry-run skips audit. Gitignored (per-machine signal).

31. ✅ **gitignore backfill** — `_sync.write_default_gitignore()` now idempotently appends `.audit/` and `.dedup-window.json` to existing user gitignores while preserving user edits. New installs get full template.

### Shipped v3.1.1 — code-review + security-review hardening

After dual-track review (`code-reviewer` REQUEST CHANGES, `security-reviewer` MEDIUM-risk) on v3.1, applied all P1 + agreed P2 + H1/H2/M1/M2/M3/M4/L2/L3 findings:

32. ✅ **`_atomic.safe_write()` chokepoint** — single function that sanitizes ALL synced `.md`/`.markdown` writes under `workspaces/` and `shared/`. Replaces ad-hoc `sanitize(); atomic_write()` patterns at 9 call sites (`_topic`, `_lesson`, `_moc`×4, `_research`×2, `_workspace`, `_migrate_v3`×2, `_frontmatter`). Non-synced paths (state.json, config.json, index.db) bypass sanitize entirely. Caller-visible `INFO: redacted N secret(s)` on stderr when n>0.

33. ✅ **Expanded secret pattern catalog** — `_privacy._PATTERNS` now covers GitHub fine-grained PAT (`github_pat_…`), GitLab (`glpat-…`), npm, PyPI, OpenAI project keys (`sk-proj-…`, ordered BEFORE generic `sk-`), Slack webhooks (`hooks.slack.com/services/…`), Discord bot tokens, SendGrid (`SG.<22>.<43>`), Twilio SID, database URL credentials (`scheme://user:pass@host`), and HTTP `Bearer <token>` header (whitespace separator, dedicated pattern since kv-secret requires `:` / `=`). GitHub PAT length cap loosened to `{36,255}` to survive token format changes.

34. ✅ **Tightened kv-secret value class** — `[A-Za-z0-9_\-\.+/=]{12,}` excludes URL-fragment chars (`&?#/`) and prose punctuation; ≥12 chars reduces false-positives on short identifiers like `password=abc`. Vocab expanded: `bearer`, `refresh_token`, `client_secret`, `session_token`, `credentials`, `passphrase`, `dsn`, `connection_string`.

35. ✅ **Fail-OPEN with surfaced bypass (M3)** — `_privacy.sanitize()` returns `(text, -1)` sentinel on regex failure AND emits stderr warning + `.audit/sanitize-failures.log` JSONL line. Writes still proceed (never lose user data) but silent regressions become visible. `sanitize(None)` contract changed to return `("", 0)` for caller safety.

36. ✅ **First-write-wins dedup-prune rule (M4)** — `_prune.py` Jaccard ≥0.85 deduplication now keeps the FIRST entry (chronologically earlier, audit-stable) and drops later duplicates with reason `duplicate-newer-dropped`. Fixed iteration-mutation bug (was iterating `kept` while appending to it).

37. ✅ **Audit log permission hardening (M1)** — `_audit._open_log_secure()` chmods `.audit/` parent to `0o700`, opens log with `O_CREAT|O_WRONLY|O_APPEND` mode `0o600`, post-open `fchmod(0o600)` to guarantee perms even when umask is permissive. `f.flush() + os.fsync()` per write to survive crashes.

38. ✅ **Dedup window structural self-heal (M2)** — `_dedup._load()` transparently recovers from poisoned `.dedup-window.json`: non-dict root → fresh, non-dict `entries` → empty, non-string keys / non-numeric values skipped, non-numeric `window_seconds` → default. Previously a single corrupt write would silently disable dedup for the install lifetime.

39. ✅ **Line-by-line gitignore membership (P1)** — `_sync._gitignore_has_entry()` walks lines, skips `#` comments and `!` negations, matches normalized entries. Fixes substring false-positive: a user comment containing `# Maybe ignore .audit/ later` no longer bypasses the privacy backfill.

40. ✅ **Quoted heredocs + clean-room install hardening** — `bin/test-install.sh` heredocs are now `<<'PY'` (no shell expansion / injection); paths passed via `GOWTH_SCRIPTS`/`GOWTH_TMP` env vars. Added: comment-guard gitignore test, perm verification (`0o700`/`0o600`), dedup self-heal poison-recovery test.

Test coverage: 102/102 unit tests + 6/6 `bin/test-install.sh` steps green (verified parallel). New tests in `test_privacy_dedup_audit.py`: 8 new privacy shapes, `AuditPermissionsTests`, `DedupSelfHealTests` (3 poison shapes), `SafeWriteTests`, `PruneFirstWriteWinsTests`, extended `GitignoreBackfillTests` (comment-only mention + negation).

### Shipped v3.2 — drop per-prompt augmentation hooks

After 4 months of v0.7/v0.6 on-prompt magic in production, the per-turn token cost (system-augment + recall-active + user-augment fired on EVERY UserPromptSubmit) outweighed retrieval benefit. SessionStart bootstrap already loads stable context once; Claude can grep / wikilink-resolve on demand for the rest.

41. ✅ **Dropped `recall-active.py`** — UserPromptSubmit hook that ran BM25 + vector hybrid retrieval against `index.db` on every prompt. Index.db is retained but its consumer is now only `_wikilink.resolve()` for `[[slug]]` lookup. `/mem-reindex` still ships so wikilinks keep working.

42. ✅ **Dropped `user-augment.py`** — UserPromptSubmit hook that pattern-matched intents (`INLINE_MEM_SAVE`, `INLINE_MEM_REFLECT`, …; English + Vietnamese) and inlined full skill body. Replaced by direct `/mem-save`, `/mem-reflect`, etc. — explicit skill invocation is cheaper than always-on regex.

43. ✅ **Dropped `system-augment.py`** — SessionStart context augmenter that injected extra system messages duplicating `shared/AGENTS.md`. `bootstrap-load.py` already covers the same prefix; the duplication was confusing the cache + costing tokens.

44. ✅ **Dropped `/mem-hyde-recall` skill + command** — opt-in HyDE retrieval was tied to `recall-active.py`'s embedding/scoring stack; standalone it duplicated `/mem-reindex` setup with no surviving consumer.

Hook entrypoints: 8 → 5 (`bootstrap-load`, `auto-journal`, `precompact-flush`, `conflict-detect`, `auto-sync`).
Test coverage: 94/94 unit tests + 6/6 `bin/test-install.sh` steps green (was 102/102; 8 tests removed alongside their subjects — `test_multi_aspect_recall_v3.py` deleted entirely, 3 `MultiSignalTests` methods excised from `test_regressions.py`).

### Shipped v3.4 — hook waste cut, schema first-class, dreaming UX

Grounded in three deep-research passes saved to `.claude/research/v3.4-{brain-memory, llm-memory-systems, hook-patterns}.md`. Two convergent observations: (1) the 7-type tag was a formatting hint, never a schema constraint — duplicates of `[decision] foo` survived because dedup was content-only and within-300s only; (2) hooks burned 20-30k tokens/day on the auto-journal REASON and per-prompt conflict-detect Python startup. Both fixed without breaking v3.3 deterministic-only retrieval.

45. ✅ **Shell pre-check on UserPromptSubmit** — `conflict-detect.sh` wraps `conflict-detect.py`; uses bash `test -f` against `SYNC-CONFLICT.md` and `exit 0` silent on the 99% no-conflict path. Python startup eliminated from the hot path.

46. ✅ **Merged SessionStart and PreCompact hooks** — `session-start.sh` and `precompact.sh` collapse two-entry matchers each into a single command. SessionStart branches on `source` field (`startup` / `compact` → bootstrap; always: `auto-sync --pull-only` in background). PreCompact preserves HARD-BLOCK exit-2 semantics from `precompact-flush.py` even when chaining `auto-sync --commit-only` afterward.

47. ✅ **Externalized auto-journal REASON** — moved 3 KB instructions block to `templates/auto-journal-instructions.md`. Stop hook now injects a ~400 char pointer instead. Saves ~20-30k tokens/day on heavy-usage sessions.

48. ✅ **Tunable auto-journal cadence** — `auto_journal.journal_every` (default 10) and `auto_journal.auto_journal_enabled` (default true) in `~/.gowth-mem/settings.json` replace hardcoded modulo. Read via `_read_journal_settings()`.

49. ✅ **Subagent auto-skip** — `auto-journal.py` exits 0 silently when env `CLAUDE_SUBAGENT` is set OR stdin JSON carries `agent_type=="subagent"`. Prevents double-journaling under ralph/ultrawork/autopilot flows.

50. ✅ **Tag-aware FTS5 schema** — `chunks` and `chunks_fts` gain a `tag TEXT` indexed column. `_migrate_tag_column()` in `_index.py` runs idempotent `ALTER TABLE` + SQL-CASE backfill from leading `[tag]` regex + FTS5 rebuild. `KNOWN_TAGS` set drops unknown tags to `''`.

51. ✅ **Cross-file, tag-aware dedup wired into write path** — `_dedup._tag_digest(tag, content)` hashes over `tag\x00normalized_content` for the 300s hot-path window. `is_duplicate(ws_root, tag, content)` queries the index DB for ANY matching `(tag, hash)` row across all files using the same SHA-1[:16] hash `_index.py` stores. **v3.4 post-critic patch**: now called from `_lesson.append_lesson` and the new `_topic.append_entry(content, ws)` helper (CLI: `python3 _topic.py --append "..."`), so duplicate rejection actually fires on the Python write path — not just available as a helper. Preserves "[decision] foo" + "[exp] foo" as legitimate distinct facts; blocks "[decision] foo" + "[decision] foo" across files and sessions.

52. ✅ **`/mem-recall --type=<tag>` retrieval** — new `_query.query_by_type(ws, tag, query, limit)` API pre-filters chunks by tag before BM25 ranking. CLI entry via `python3 hooks/scripts/_query.py --ws X --type decision --query foo`. Empty tag = no filter (v3.3 BM25 behavior preserved).

53. ✅ **`/mem-dream` skill** — new `hooks/scripts/_dream.py` orchestrator wraps `_consolidate.py`'s three phases (`light_phase`/`rem_phase`/`deep_phase`). Per-workspace file lock prevents concurrent runs. `--dry-run`, `--no-light`/`--no-rem`/`--no-deep`, `--ws` flags. Progress to stderr, JSON to stdout. Maps onto biological sleep consolidation: SWS replay+prune (Light), counterfactual cross-topic synthesis (REM), schema abstraction (Deep).

54. ✅ **Command surface pruning (33→28)** — deleted `/mem-bootstrap`, `/mem-flush`, `/mem-workspace-create`, `/mem-workspace-archive`, `/mem-workspace-list`, `/mem-workspace-map` (auto-run via hooks; subcommands collapsed into `/mem-workspace [<verb>]` parent). `commands/mem-recall.md` added to match `_query.py` CLI surface. README + CLAUDE.md updated.

55. ✅ **v3.4 post-critic patches**:
    - **P0**: `is_duplicate()` wired into `_lesson.append_lesson` and new `_topic.append_entry(content, ws)` helper (CLI: `--append`).
    - **P1**: `_dream._filter_state_to_ws(state, ws)` restricts `state["files"]` to `workspaces/<ws>/` so `--ws=X` actually filters.
    - **P1**: `auto-journal._is_subagent` detects `hook_event_name == "SubagentStop"`, `data.get("in_loop")`, `agent_type == "subagent"`, and `CLAUDE_SUBAGENT` env.
    - **P1**: `_build_reason()` dead args dropped (`prune_summary`, `consolidation_summary`, `ws_list_str`) — pointer-only stays ≤400 chars.
    - **P2**: `_migrate_tag_column` wrapped in `file_lock("index-migrate", timeout=10)` to serialize concurrent migration.

Test coverage: 201/201 unit tests green (was 94/94 at end of v3.2; added test_hook_wrappers.py [10], test_index_tag_column.py [11], test_dedup_tag_aware.py [10], test_query_by_type.py [21], test_dream.py [6]; subtotal new = 58). Compile clean across all `hooks/scripts/*.py`.

Hook entrypoints: 5 → 5 (same count, but shell wrappers gate Python invocation; effective per-prompt overhead down ≥95% on no-conflict path).

Reference plugins consulted: claude-mem (thedotmack), claude-code-rewind, superpowers, oh-my-claudecode. Memory systems consulted: HippoRAG v1/v2, MemoRAG, Letta/MemGPT, Cognee, mem0, Zep/Graphiti, OpenAI Memory, Anthropic contextual retrieval, LangMem, A-MEM.

### Tier 4 — out of scope

12. RAPTOR / GraphRAG / HippoRAG — handled by claude-obsidian's wiki-fold + lint, or future plugin.
13. AutoCompressor / gist tokens — needs custom model, defer.
14. ColBERT / ColPali — overkill for markdown vault.
15. Entity/relation KG (HippoRAG/Zep pattern) — deferred from v3.4; needs entity extractor which would break deterministic-only rule. Re-evaluate for v3.5.
16. FSRS upgrade from SM-2-lite — backlogged for v3.5; SM-2-lite sufficient for current scale.
17. Two-factor synaptic edge weights (gemini deep-research finding) — defers with KG work.

## v3.6 — Active forgetting (journals are ephemeral)

Root cause found via two audits: a memory system that **captured but never consolidated**.
`precompact-flush.py` dumped ≤80 KB raw transcript into durable journals on every `/compact`,
deferring distillation to a manual `/mem-distill` that never ran. Journals grew unbounded
(one hit 1.8 MB / 26,812 lines); **79% of all stored data was unread raw transcript**, only
~5% actionable. The agent stopped reading journals (too large) → "captures everything, reads nothing".

Shipped:
- **`_forget.py`** — enforces canon §3 journal raw-TTL (default 7d). Per workspace: SALVAGE
  curated `- [type]` bullet entries → `journal/_salvage.md` (SHA1-deduped), then ARCHIVE
  (gzip → `.archive/journal/<ws>/`, gitignored) journals older than `raw_ttl_days` OR >1-day-old
  and over `max_bytes`. Today's / within-TTL journals never touched. Recoverable via gz +
  memory-repo git history. Verified-before-delete (re-reads the gz header).
- **Stop-hook wiring** — `auto-journal.py` runs `_forget --all-workspaces --quiet` beside
  `_prune`/`_consolidate`, gated by `settings.journal.auto_forget_enabled` (default true).
- **precompact cap 80 KB → 20 KB** — keeps the bootstrap-loaded today-journal cheap to read.
- **settings** `journal{raw_ttl_days, max_bytes, salvage, auto_forget_enabled}` (live + template).
- **`/mem-forget`** command. Fixed **broken `/mem-prune`** (passed `--workspace`, which `_prune.py`
  rejects with "unrecognized arguments").
- **One-time live-vault cleanup**: workspaces/ **14 MB → 1.9 MB**, `index.db` **29 MB → 3 MB**
  (rebuilt clean), 28 raw journals archived, 6 curated entries salvaged, stale `.backup/` (2.9 MB)
  removed, `hook-errors.log` truncated.

Test coverage: **219/219** green (added `test_forget.py` [10]). Compile clean.

Grounded in `.claude/research/v3.6-brain-storage.md` (Gemini + Perplexity deep research; Grok
unavailable). Companion to `shared/research/data-quality-2026.md` (what-to-keep canon).

Next gaps (see research note §5): auto-consolidation still under-fired (episodic→semantic leans
on Stop-hook block); no hard size-split hook for >500-line files; no decay-GC over stale topic
entries; defrag surfaces but doesn't auto-merge; progressive-disclosure TL;DR not length-capped.

### v3.6 — Hard write-rules gate + extraction canon

The canon documented DROP rules; nothing enforced them at the write path, so junk still landed.

- **`_gate.py`** — deterministic (no-LLM) write-time gate enforcing canon §1: REJECT on
  empty / placeholder(`todo/tbd/...`) / `<20`-char body / hedged-without-evidence /
  `[ref]`-without-Source / `[decision]`-without-rationale / `[tool]`-without-version-or-syntax /
  secret-leak(AKIA/sk-/ghp_/xox/JWT/PEM). Wired into `_topic.append_entry` + `_lesson.append_lesson`
  (gated by `settings.gate.enabled/strict`, best-effort — never blocks on gate-internal error).
  `--scan` finds junk in existing files (found 18 in the live vault). `/mem-gate` command.
- **`shared/research/extraction-reuse-2026.md`** (new shipped canon) — capture→extract→consolidate→
  forget lifecycle; what makes an entry reusable (self-contained, canonical phrasing, when-to-apply
  triggers, provenance, validity); the 12-rule write gate; ADD/MERGE/DEDUP/SUPERSEDE/NOOP matrix
  (Jaccard 0.85 / cosine 0.75/0.92); target architecture **B-TAZ** (Bi-Temporal Agentic Zettelkasten).
- **Fixed broken YAML frontmatter** in `mem-compress/mem-distill/mem-install` (mid-value `: `
  mis-parsed the description — a real cause of "Claude doesn't know which skill to use"). All 43
  command+skill frontmatter files now parse clean.

Deep research: 2× Gemini (conv c_742be0a1ce2e1d61, c_31a0d05a7bdf8421) + 2× Perplexity
(backend f4b8784b, 955b213d "Git Hippocampus"); Grok unavailable (x-statsig auth).
Test coverage: **234/234** green (added `test_gate.py` [15]).

### v3.6 — Descriptive auto-commits (git = audit trail)

The memory repo auto-commits on hooks, but messages were `"auto-sync from <host>"` —
`git log` told no story. The user's requirement: "khi plugin commit thì ghi rõ trong git,
để đảm bảo check từ git có thể hiểu được. và từ git sẽ đi sâu vào."

- **`_commitmsg.py`** — `build_message(gh, host, context)` generates a structured message
  **deterministically from the staged diff** (`--name-status -M` + `--numstat -M` +
  `--unified=0`, no LLM). Shape: `type(scope): summary` + body (counts/focus/largest) +
  git-trailer footers `Workspace:`/`Topics:`/`Entries: +2 decision -1 ref`/`Files:`/
  `Machine:`/`Context:`. Types `add/update/prune/archive/consolidate/sync` chosen by
  path-bucket (journal/handoff/aspect/lessons/moc/docs/shared) + entry-tag deltas +
  file add/delete/rename counts. Subject capped 72 chars; huge diffs cap hunk-scan.
- Wired into `auto-sync.commit_local` (SessionStart/PreCompact/PostCompact/Stop hooks)
  and `_sync.py` (manual `/mem-sync`). Falls back to the old one-liner if generation fails.
- **Bug fixed**: `auto-sync.py` called `log_debug` without importing it — a sync-lock
  timeout would crash the hook with a traceback (violates graceful-missing). Now imported.

Real output (verified end-to-end through the hook):
```
add(trade): +1 [decision] +1 [ref]; in exness-ea
- 3 files changed, +5 / -0 lines
- Focus: aspect
Workspace: trade / Topics: exness-ea / Entries: +1 decision +1 ref / Context: pre-compact
```
Now `git log --grep 'Workspace: trade'`, `git log --grep '^archive('`, `git log -- <path>`,
`git log --stat`, `git blame` all stay useful. Deep research: Perplexity (backend dcc8cb10),
Gemini (conv c_c23d12acdbbec4a3). Test coverage: **246/246** green (added `test_commitmsg.py` [8]).

### v3.7 — File-level schema validator (learned from supremor)

Studied the TrueProfit `supremor` team-knowledge vault (1295 .md, `claude-code-vault-keeper`
validated, BOARD.md kanban, 72 templates, themed auto-changelog, `type(scope):` commits). Its
**file-level schema validator** is the discipline gowth-mem most lacked: `_gate.py` checks entry
*content*, but nothing checked file *structure*, so **121 topic files** had missing/partial/wrong
frontmatter (invisible to wikilinks, recall scoring, auto-MOC).

- **`_validate.py`** (adapted to v3 file types): validates `00-README`(slug/title/type/status),
  dated-aspect(type=aspect/date/topic/slug/title), naming(slug regex), reserved-path placement.
  `--scan` reports; `--fix` deterministically repairs aspect frontmatter from the path
  (topic=parent, date+aspect=filename, slug=topic-aspect, title=H1), content-preserving.
- **Live vault reorganized**: 121 non-conforming → `--fix` repaired **124 aspect files** + MOC
  rebuild → **0 schema issues**. Accounts/configs preserved verbatim; `_gate.py` still 0 junk.
- `/mem-validate` command + 7 tests (**253 total**). Full comparison + remaining transferable
  learnings (deliberate taxonomy, themed `/mem-changelog`, work-board handoff, SSOT router) in
  `.claude/research/v3.7-supremor-comparison.md`.

### v4.0 — Metacognition: deterministic auto-tagging + session self-review loop

The user's three complaints, closed in one tier: (1) *"entries have no tags — AI search
struggles"*, (2) *"stored data has no clear value"*, (3) *"log my prompts and your thinking,
score us honestly every 15 turns, save it as experience, improve every use"*.
Design + research trail: `.claude/research/v4.0-metacognition.md` (2 codebase maps + Perplexity
deep research; Gemini down that run).

**A. Deterministic auto-tagging (`_tags.py`, pure stdlib, no LLM)**
- YAKE-lite extraction for 1-3 line entries (research: RAKE/TextRank collapse on short text):
  priority identifier harvest (`code`, dotted.paths, snake_case, kebab-case, CamelCase, --flags,
  acronyms) then prose scoring (freq × early-position × length × casing boost, noun-phrase
  bigrams). Quality guards: pure-alpha UPPER ≥5 demoted to prose (emphasis ≠ acronym — kills
  `ONLY`/`CONTENT`), post-normalize stopword check (~200 EN + ~100 VI + ascii-VI set),
  filesystem-component denylist (`opt`,`usr`…), substring collapse (keep `1tokenai-build`, drop
  `tokenai`+`build` — retrieval-safe: `--keyword` LIKE-matches compounds), 2-slot prose
  reservation so repeated topic words beat a fifth identifier. Typical 3-5 tags, hard cap 7.
- Write path: inline `  #tags` appended to entry first line in `_topic.append_entry` +
  `_lesson.append_lesson` (after gate, best-effort) + frontmatter `tags:` union on dated aspect
  files (cap 15) — the dead `tags: []` field finally lives. **Dedup stability**: `_index`/`_dedup`
  hash tag-STRIPPED content, so an entry dedupes identically with or without tags.
- Index/search: `chunks.keywords` column + rebuilt `fts5(tag, keywords, content)` (idempotent
  migration under `file_lock("index-migrate")`); ranked queries use column-weighted
  `bm25(chunks_fts, 5.0, 3.0, 1.0)` — tag/keyword hits outrank body hits (boost by default,
  filter on demand). `query_by_type` + `/mem-recall` gain `--keyword` / `--topic` / `--days`.
  `mem-recall.md` honesty fix: removed the never-implemented 5-signal formula claim.
- `/mem-retag` backfill (frontmatter-only — never rewrites historical entry lines): live vault
  **153/190 aspect files gained tags**, 294 chunks carry keywords.
- Data-value guards: topic auto-create denylist `(example|placeholder|redacted|akia…)` — the live
  `akiaiosfodnn7example-placeholder` topic can no longer mint; `validate_workspace()` in both
  write entrypoints (a swapped-args call had silently mkdir'd a junk dir named after entry prose).

**B. Session capture (`_capture.py`, wired into the Stop hook — no new hook process)**
- Every Stop: parse transcript tail (512KB), append to `<ws>/journal/sessions/<date>-<sid8>.md`:
  `**User:**` (prompt, cap 2000), `**Claude:**` (text head 300 — visible reasoning), `**Actions:**`
  (tool-use trace `Read(x) → Edit(y) → Bash(…)`, cap 500). **Thinking is NOT capturable**: Claude
  Code transcripts carry signature-only thinking blocks (`thinking` field empty — verified 24/24
  live + 681/681 across 40 transcripts). The Actions trace is the honest observable proxy for
  reasoning direction; an opportunistic extractor stays for future versions
  (`reflection.capture_thinking`). Idempotent per turn, never raises, TTL-managed by `_forget.py`
  (sessions archived after `raw_ttl_days`, `## [self-review]` blocks salvaged, `journal/**/_*.md`
  exempt).
- `state.json` per-session counters: `total_turns` (monotonic), `review_count` (independent of
  journal's `turn_count` — at turn 30 both fire as ONE combined `decision:block`).

**C. 15-turn honest self-review (`templates/self-review-instructions.md`, `/mem-review`)**
- Anti-sycophancy contract (research: self-preference bias is real): harsh-reviewer paragraph
  written FIRST; 3 dimensions (user prompting on 5 sub-criteria / Claude reasoning / collaboration)
  on an anchored 1-5 scale; ≥2 concrete weaknesses per dimension each with a **verbatim quote**
  (no quote → no score); score ≥4 needs 2 citations; prefer dispatch to a fresh-context critic
  subagent; **counterfactual gate** — a `[reflection]` rule is vault-written only if it would have
  prevented an observed rework in THIS log; <10-turn sessions skipped.
- Output: `## [self-review]` block in the session log + gate-checked `[reflection]` entries via
  topic routing + score row in `<ws>/journal/_scores.md` (`| date | sid | turn | P | R | C |
  delta |`) → the improvement trend `/mem-review --history` renders.

Settings: `tags.{enabled,max_per_entry,max_frontmatter}`,
`reflection.{enabled,turn_interval,capture_thinking,max_prompt_chars,max_thinking_chars}`.
Test coverage: **351/351** green (+100 vs v3.9: test_tags 32, test_capture 17,
test_review_trigger 8, +extensions in index/query/dedup/route/forget suites).

### v4.1 — Review coverage, one-shot portability, retention & English-storage policy

Shipped 2026-07-11. Two sessions: a vault deep-review that turned findings straight into
mechanisms (dogfooding), then a policy pass requested by the operator (">3 months → archive;
required data stored in English; clean up after storing").

**A. Conversation-review coverage (`_review_ledger.py`, `/mem-review-backlog`)**
- v4.0 only reviewed the LIVE session at the 15-turn cadence; conversations that ended early or
  predate v4.0 were never reviewed. Live machine: 1058 transcripts under `~/.claude/projects`,
  119 substantive unreviewed.
- Machine-local ledger (`review-ledger.json`, gitignored — transcripts never leave the machine;
  scores still go to the synced vault). Metadata-first: `--scan` is stat()-only; content is read
  for one `--next` candidate at a time. Substance = ASSISTANT turns (a 349 KB autonomous session
  had 7 user prompts); `"type"` is NOT the first JSON key in live transcript lines — substring
  match only (the prefix-match bug wrongly skip-marked all 119 before dogfooding caught it).
- Stop-hook self-review reason now appends the backlog count.

**B. One-shot machine portability (`_setup.py`, `/mem-setup`)**
- Backs up marketplaces (git URLs) + installed plugins + global MCP servers + `~/.claude/skills`
  + `settings.json` + global `CLAUDE.md` into synced `shared/setup/`, ALL text through
  `_privacy.sanitize` (first live run caught 2 real secrets sitting in skills files); MCP env
  values become `<env:NAME>` pointers with a `required_env` manifest.
- Generated `restore.sh` (file copies + MCP merge into `~/.claude.json`) + `RESTORE.md`
  (one-paste `/plugin` block). New machine = clone vault → 1 script → 1 paste.

**C. Handoff bullet-level rotation (`_handoff.py`)**
- v3.6 rotation only handled dated `## <snapshot>` sections; real handoffs are flat `- host:`
  bullet lists — never rotated (trade: 62 bullets / 57 KB, 43 stale, loaded EVERY session).
- New pass archives `[done]` bullets older than 14d; `[doing]/[blocker]/[thread]/[next]` survive
  at any age (live multi-machine threads). Live result: trade 57.7→37.3 KB, devops 33.3→23.8 KB,
  ~7.4k tokens saved per bootstrap, 0 data loss (65/31 bullets reconciled).

**D. English-storage policy (`_gate.py english_only`, vault AGENTS.md §7-LANG)**
- Curated entries are stored in ENGLISH — enforced in code (>2 Vietnamese diacritics →
  `not_english` reject), not docs (the v3.6 bloat postmortem proved docs alone don't hold).
  Raw journal stays bilingual. Legacy files migrate translate-on-touch; verbatim operator quotes
  are translated with a "(translated from Vietnamese)" provenance note.

**E. Aspect retention — the ">3 months → archive" mechanism (`_forget.py --aspects`)**
- `topic_layout.archive_threshold_days` existed in settings since v2.x but NO code honored it.
  Now: aspects older than the threshold (default 90d; age = FILENAME date, not mtime — mtime is
  perturbed by maintenance) are salvaged first (`- [type]` blocks → topic `lessons.md`, SHA1-
  deduped + provenance line) then gzip-archived to `.archive/topics/<ws>/<slug>/`. Every topic
  keeps its newest 3 aspects regardless of age; `00-README.md`/`lessons.md` never touched.
  Stop-hook applies it when `topic_layout.auto_archive_enabled` (live settings: 90d, enabled).

**F. Aspects born schema-conformant (`_topic.append_entry` → `_validate.fix_aspect`)**
- Routed writes created aspects with tags-only frontmatter — invisible to wikilinks/recall/MOC
  until a manual `_validate --fix`; 13 such files had accumulated in the live vault. New aspects
  now get full path-derived frontmatter at creation.

Live-vault cleanup shipped alongside: 8 empty husk topics deleted, 2 duplicate topics merged
(content translated, numbers preserved verbatim), 15 docs-core files + 9 entries translated to
English, full reindex + MOC regen; gate scan 0 junk, validate 0 schema issues.

Settings: `gate.english_only`, `topic_layout.{archive_threshold_days,auto_archive_enabled}`.
Test coverage: **399/399** green (+48 vs v4.0: ledger 9, setup 9, bullet-rotation 7, aspects 7,
english gate 4, frontmatter-at-birth 1, + suite fixture updates).

---

## v4.3 — Zero-Mem retrieval repair (2026-08-05)

Grounded in **arXiv 2607.29377v1**, "Zero-Mem: Zero-Token Memory Operations for LLM Agents"
(Xiao et al., 31 Jul 2026). Design + full rejection rationale:
`docs/superpowers/specs/2026-08-05-zero-mem-retrieval-design.md`.

The paper's thesis — every memory operation outside final question answering should cost zero
LLM tokens — is this repo's own priority list. Auditing against it found the deterministic
machinery already here was **not working**. Every figure below was measured on the live vault
(15,145 chunks, 5 workspaces).

**A. `/mem-recall` returned 0 hits for any natural-language query (`_profile.py`, new)**
- FTS5 ANDs bare terms: `why did we drop vector recall` → **0 hits**; the same terms OR-joined
  → **991**. Punctuation was worse — `vector-recall` raised `no such column: recall` and
  `forget: ttl` raised `no such column: forget`, both swallowed by `except Exception: return []`
  into a silent "(no results)".
- Adopts Zero-Mem eq (6) `phi(q) = {subject, keywords, answer-type, temporal-cues, boundary}`,
  reusing `_tags._harvest_priority` so `_forget.py` / `raw_ttl_days` / `prop-firm-funding`
  survive tokenisation. `fts_match()` emits ONLY quoted phrases joined by OR, so no user input
  can be read as FTS5 syntax; quoting also makes `vector-recall` match as a phrase.
- New `query_ex() -> {"hits", "error"}` reports WHY a set is empty. `query_by_type` keeps its
  documented `list[dict]` fail-open contract (21 existing tests untouched).

**B. The `tag` column was inert — 54 of 15,145 rows (0.4%) (`_index.py`)**
- Three independent causes, all fixed: `split_chunks` lifts `## [decision] Title` into
  `heading` but `_extract_tag` probed only `content` (4,714 chunks affected); frontmatter was
  chunked as content so every routed write landed untagged (635 chunks, ALL untagged); and
  `- [exp] …`, the dominant on-disk form, was not recognised.
- Result: tagged chunks **54 → 2,528** (46.8×) — exp 790, ref 615, decision 514, tool 266,
  reflection 212, goal 77, hypothesis 31, secret-ref 23. The 5.0 BM25 tag weight and the
  `--type` filter now apply to 16.7% of the corpus instead of 0.4%.
- Self-healing SQL backfill runs on every migration pass (0.43s on the real 25 MB index,
  idempotent), so no user has to remember a reindex.

**C. The heading was never searchable (`chunks_fts` 4th column)**
- `chunks_fts` indexed `(tag, keywords, content)`, so a term appearing only in an entry's
  `[type] Title` — or a session log's `turn N — HH:MM` anchor — could not be found at all.
  Added as an indexed column at weight 4.0; `bm25()` weights are now derived from the index's
  actual column arity (tag 5 > heading 4 > keywords 3 > content 1) instead of assumed.
  Recall output prints the heading, which is the most locating line a hit has.

**D. Per-workspace recall silently returned nothing (`_query.py`)**
- The workspace filter ran in Python AFTER `ORDER BY score LIMIT ?`, so a workspace whose hits
  ranked below the limit got zero rows: `--ws personal --limit 20 python` → nothing, while 3
  personal chunks matched. Now a SQL predicate, so the limit means "N hits in this workspace".

**E. Writes were invisible until a manual reindex (`_index.reindex_paths`)**
- Nothing on the write path touched index.db; the live index was 5 days stale. `append_entry`
  and `append_lesson` now refresh just the file they wrote — lock-guarded, never raising, and
  deliberately refusing to CREATE index.db (a missing index must be built whole by
  `/mem-reindex`, not half-populated from whichever file was written last).
- The per-file indexing body is factored into `_index_one()` so the full sweep and the
  write-time refresh cannot drift; `_drop_path_rows()` deletes `chunks_fts` rowids BEFORE the
  `chunks` rows they mirror (external-content table — the reverse order corrupts the index).

**F. Bootstrap dropped `docs/handoff.md` from every session (`bootstrap-load.py`)**
- The first file was handed the ENTIRE budget, so `shared/AGENTS.md` (13,281 B) plus a
  truncated `shared/secrets.md` (13,520 B) consumed the 15,000-char cap and the loop broke at
  `room <= 200`: `[bootstrap: loaded 2/5 files]`. The workspace `AGENTS.md` and `handoff.md` —
  the files carrying CURRENT state — never reached the model. v4.1 had shipped `_handoff.py`
  rotation to save ~7.4k tokens/bootstrap on a file that was not being loaded at all. At 32.5
  sessions/day this was the largest memory-token line item (~124,500 tokens/day) buying strictly
  less recall than designed.
- Budget is now allocated BEFORE reading, reserving the small per-session deltas first, with
  `MAX_PER_FILE = 4,000`. `MAX_TOTAL` stays 15,000 — recall quality outranks token efficiency.
  Emission order is unchanged (statics first) because CLAUDE.md requires a stable prompt-cache
  prefix and `handoff.md` changes every session. Live result: 2/5 → **5/5, 5/5, 4/5** across
  trade/personal/devops. Verified `handoff.md` is newest-FIRST, so head-truncation preserves
  current state (head[:4000] of the 167,624-char trade handoff = its 3 newest `host:` lines);
  there is a test asserting a tail-truncating implementation would fail.

**G. Per-path collapse (`_query._collapse_per_path`)**
- Zero-Mem's `Dedup` (eq 14) scoped to the real failure mode: one long file filled every slot
  of the caller's limit (`--ws personal --limit 3 python` returned 3 chunks of ONE session log).
  Over-fetches then trims, because trimming first returns fewer than `limit` distinct files.

**H. Six commands + seven skills targeted the dead pre-v2.7 layout**
- `/mem-journal` WROTE memory to `$PWD/docs/journal/` — outside the vault, so never synced,
  never indexed, invisible to every hook. Silent data loss.
- `/mem-cost` measured 0 of 9 files and quoted a 60,000-char cap the code never used. The one
  tool for detecting bootstrap bloat was blind, which is *why* F survived. It now calls a new
  `bootstrap-load.py --report` sharing `_plan()` with the hook, so it cannot drift again.
- `skills/mem-prune` called the journal "the immutable raw log" that must "never" be pruned —
  contradicting v3.6 active forgetting, teaching the model to leave data where `_forget.py`
  will archive it.

**REJECTED: the entity-context graph + Personalized PageRank (eq 3–4, 8–10)** — the paper's
largest ablation delta (−17.19 F1). A working stdlib prototype (regex identifier harvest for
spaCy NER, frontier-limited PPR at 16–39 ms/query over 15,145 chunks, γ=0.6) was ablated on 5
real vault queries and **lowered** quality at every ρ: MRR 1.000 (bm25+collapse) vs 0.900
(ρ=0.4) vs 0.767 (ρ=0.6). The gain initially credited to it came from per-path collapse. Also:
`V_e` needs spaCy and `η₀ = cos(e, ê)` needs BGE-M3, both violating the zero-pip rule; and the
paper attributes the graph's dominance to HotpotQA cross-document reasoning while its ONLY
reported loss anywhere is LoCoMo multi-hop — the conversational regime this vault occupies.
Honest loss: no multi-hop bridging to evidence sharing no surface overlap with the query.

Also rejected: min-max dual-view fusion (nothing to fuse without the graph); the hard `Filter`
that deletes candidates; post-reader answer calibration; and syncing `.audit/` prune previews
(would violate "never sync real secret values").

Still open (see spec Tier 2): archive indexing then orphan sweep — **4,031 chunk rows (27% of
the index) still cite files `_forget.py` deleted**, and the 272 archived `.gz` are unsearchable;
provenance backlinks; `conflict-detect` once-per-session suppression; calibration multipliers.

Test coverage: **463/463** green (+61 vs v4.1: profile 19, retrieval 22, bootstrap 11,
freshness 7, command hygiene 6, minus fixture updates). `bin/test-install.sh` all green.

## v4.7 — Delegation-first cadence blocks (2026-08-18)

**Problem (user-reported):** every N turns the Stop hook `decision:block`ed the MAIN session
into doing the journal routing (classify → route → gate → handoff → `_moc.py`) and the
self-review inline — 10-20 unrelated tool calls polluting the main context, diluting flow.

**Fix — the main session's only cost is one dispatch call:**

- **Journal cadence** → reason instructs dispatching ONE background subagent (memory teammate);
  turn source = the `_capture.py` session log. Inline fallback only when no session log exists
  or the harness has no subagent tool. The teammate prompt carries an anti-recursion sentinel.
- **Review cadence** → main session ONLY dispatches a fresh-context background judge (must NOT
  be a context-inheriting fork — anti-self-preference) and relays its 3-line summary. No
  session log → review is DEFERRED (counter kept, fires as soon as a log exists) with a
  once-per-session `[gowth-mem:review-paused]` notice naming the fix knob — never a judge
  pointed at a nonexistent file.
- **Capture gating** — `reflection.capture_enabled`, defaulting to `reflection.enabled`: the
  documented pre-v4.7 privacy opt-out survives (session logs sync to the git remote);
  journal-only users opt into delegation explicitly.
- **Collision turn** (both cadences) → `[gowth-mem:both-cadences]` header directing TWO
  SEPARATE subagents, keyed off what actually fired (not `len(reasons)`).
- **Templates** open with SKIP guards so a dispatched teammate/judge reading its own
  instructions cannot re-dispatch; teammate protocol gains Anchors (vault root derived from
  the session-log path — `GOWTH_MEM_HOME`-safe; scripts at `<template>/../hooks/scripts/`);
  dead `{prune_summary}`/`{consolidation_summary}`/`{ws_list_str}` placeholders removed.
- **Forget coverage** — `_run_forget()` factored out; review cadence runs it when the journal
  cadence is disabled (journal-off + reflection-on used to capture forever without archiving),
  guarded against per-Stop subprocess spawns at `turn_interval: 1`.

Process: brainstorm → approved design → TDD (17 RED→GREEN) → 3 adversarial review rounds by a
fresh-context reviewer (12 + 4 + 2 findings, all fixed, final verdict APPROVE). Every changed
path exercised live per the pre-tag rule: real 596KB transcript, scratch-vault smoke of
delegate/inline/deferred/opt-out/collision/notice-once/forget paths, `bin/test-install.sh`
ALL GREEN ×3. Test coverage: **517/517** (+54 vs v4.3).

## v4.7.1 — Delegation hardening (2026-08-19)

**Problem:** a max-effort adversarial review of the v4.7 range surfaced 15 verified defects
in three clusters: the `capture_enabled` privacy contract leaked three ways, the delegation
handoff lost guarantees the inline path had, and the hook could traceback or spawn wrongly.

**Fixes (hooks/scripts/auto-journal.py, _capture.py, templates, docs):**

- **Privacy contract sealed** — `settings.example.v3.json` no longer ships an explicit
  `capture_enabled: true` (it defeated the `reflection.enabled: false` opt-out on every vault
  `/mem-install` scaffolded — the default chain now governs, pinned by test); `_coerce_bool`
  parses hand-edited values (the JSON string `"false"` no longer reads as true on a
  privacy-critical knob; explicit `null` = default chain); capture runs even with BOTH
  cadences off (`/mem-review`-only users: the early return used to silently kill the knob).
- **Hook can no longer traceback** — `session_id` coerced to str (a JSON number from a
  wrapper harness crashed the `[:8]` slice and killed capture/autosync/cadences on every
  Stop, live-reproduced); `__main__` wraps `main()` so the entrypoint ALWAYS exits 0.
- **TTL archival is cadence-independent** — `_run_forget_daily()` runs at most once per
  calendar day per machine (state.json `forget_last_run`), replacing review-cadence modulo
  arithmetic that both spawned `_forget.py` per-Stop at `turn_interval: 1` + capture-on and
  never archived with both cadences off (while `/mem-journal` + `precompact-flush.py` keep
  writing `journal/<date>.md`).
- **Pre-dispatch signal floor** — `reflection.min_review_turns` (default 10, matching rubric
  §0b): a judge is never dispatched at a log it would immediately floor-skip; the review
  defers (counter kept) until the log holds enough `## turn` blocks.
- **Paused notice is literally once per session** — persisted `review_paused_notified` flag
  (the `== turn_interval` inference repeated after counter resets and went silent when the
  interval was lowered mid-session); the notice now carries the `/mem-review-backlog` nudge
  (permanently-deferred cohorts got no nudge at all post-v4.7); `_reset_counters` falls back
  to an unlocked write on lock timeout (a swallowed TimeoutError dispatched a second judge
  for the same window).
- **Judge/log race closed** — `_capture.append_review_block` + `python3 _capture.py
  --append-review <log>` (stdin block): the background judge appends its `## [self-review]`
  block under the SAME `capture-{ws}` lock capture uses; the rubric forbids raw Write/Edit
  of the session log (an unlocked write raced the next Stop's capture rewrite and silently
  lost a turn or the whole review block, together with its `_scores.md` referent).
- **Midnight date-split** — the previous-day session log (same sid) is a named secondary
  turn source for teammate + judge, and the signal floor counts across both files (the
  post-midnight cadence used to hand a fresh teammate a 1-2-turn log as its sole source).
- **Dispatch contract pinned** — teammate template quote now mirrors the hook reason
  verbatim (sentinel + absolute paths, no dangling "given in the hook reason"); judge SKIP
  guard keys on the payload ("your prompt hands you a log + this rubric = you ARE the
  judge"), not self-declared identity; judge rubric gains 0c Anchors (vault root/`<ws>`/
  scores-ledger derivation — never resolved against cwd, the v4.3 `$PWD` bug class) and a
  **Backlog mode** (§0b) so `/mem-review-backlog` JSONL judges stop floor-skipping
  everything; teammate template names the write interface (`_topic.py --append` with
  `GOWTH_MEM_HOME` prefix — gate/tags/dedup/reindex no longer bypassable by a fresh
  teammate hand-writing topic files) and `_moc.py` gets the same env prefix;
  `tests/test_dispatch_contract.py` pins all of it.
- **Docs truthful again** — `/mem-review` names `capture_enabled` + `min_review_turns` and
  drops the false "actions trace is always captured" claim; `/mem-review-backlog` documents
  the rubric's Backlog mode.

Test coverage: 33/33 in the hook suite + 34/34 capture + new `test_dispatch_contract.py`
(full-suite count in the release commit). Every changed path exercised live on a scratch
vault pre-tag per the repo rule.

## v4.7.2 — Plugin version drift is now visible (2026-09-03)

**Problem:** a live machine ran **v3.9.0 for months** after v4.7.1 shipped. The only symptom
was its every-10-turn Stop block, and it was identifiable only because the reason string
embeds an absolute path:

```
Stop hook error: [gowth-mem:auto-journal ws=trade] 10 turns elapsed.
Read /Users/<user>/.claude/plugins/cache/gowth-mem/gowth-mem/3.9.0/templates/
auto-journal-instructions.md ...
```

`git show v3.9.0:hooks/scripts/auto-journal.py` emits that string character for character and
contains zero occurrences of `DELEGATE`/`_capture`, so the machine really was executing v3.9.0
— i.e. the pre-v4.7 inline cadence, the exact main-context pollution v4.7 removed. (The
`Stop hook error:` label is just how the TUI renders a Stop-hook `decision: block`; both
versions print that JSON and exit 0. Nothing crashed. **Corrected in v4.7.3:** the label is
avoidable — since Claude Code 2.1.163 a Stop hook can use the non-error
`hookSpecificOutput.additionalContext` channel; see below.)

**Why it stayed invisible:**

1. gowth-mem **never printed its own version**. The bootstrap header was
   `[gowth-mem:bootstrap workspace=<ws>]` — drift was undetectable by inspection.
2. `bin/doctor.sh` detected and healed exactly this failure, but only ran when a human typed
   `/mem-doctor`.
3. `bin/auto-upgrade.sh` was a complete, working upgrade script **referenced by nothing in the
   repo** (grep: zero hits outside its own header). A fix nobody calls is not a fix.

Upgrading is per-machine and not guaranteed: `claude plugin marketplace update` refreshes the
catalog but does not move `installPath`/`version`, autoUpdate is off by default for
non-Anthropic marketplaces, and old cache dirs are never pruned — so the stale plugin keeps
running with nothing breaking loudly.

**This is a data problem, not just UI noise.** The vault is shared across machines. A v3.9.0
machine keeps writing entries with no v4.0 auto-tagging, no v4.1 `fix_aspect` on new aspects
(→ aspects invisible to wikilinks/recall/MOC), no v4.3 index repair and no `english_only` gate.

**Fixes:**

- **`hooks/scripts/_version.py`** (new, stdlib, exception-proof) — running version from the
  tree actually executing (`__file__`-derived, deliberately not `$CLAUDE_PLUGIN_ROOT`: the env
  var comes from the registry entry being audited); available version from the local
  marketplace clone (plain file read, no network); numeric comparison (the lexical trap:
  `"3.10.0" < "3.9.0"` as strings); non-semver versions (a registry pinning a git sha) never
  claim a drift.
- **Bootstrap reports itself** — header is now
  `[gowth-mem:bootstrap workspace=<ws> v4.7.2]`, and a stale machine gets a prepended
  one-line nudge naming the exact fix (`claude plugin update gowth-mem -y` → restart;
  `/mem-doctor` as fallback). Costs one prompt-cache miss per release — exactly when the
  cached prefix is stale anyway.
- **SessionStart runs the self-heal** — `bin/doctor.sh` detached, `startup` source only, no
  `--pull` (zero network on the startup path). Opt out with `GOWTH_MEM_NO_AUTOHEAL=1` or
  `settings.doctor.auto_heal: false`; the gate is a pure-bash `grep`, no python startup, per
  the hook-efficiency canon. The heal lands on the NEXT start, which is why the nudge still
  says "restart".
- **`doctor.sh` audits and patches EVERY registry entry** — the value is an array and the same
  plugin can be registered at user + project scope. Reading/patching `entries[0]` could heal
  the inactive scope and leave the scope actually running the hooks stale forever. It now also
  honours `CLAUDE_CONFIG_DIR`.
- **`bin/auto-upgrade.sh` deleted**, and `tests/test_version_drift.py` fails the build on any
  `bin/*.sh` that nothing outside `bin/` references (tests/ excluded — a script referenced only
  by the guard that watches it is still a script nobody runs).

**Not retroactive:** a machine already pinned at an old version runs that old version's
SessionStart, so none of this executes there. Each stale machine needs one manual
`claude plugin update gowth-mem -y` (then restart), or `/mem-doctor`. From v4.7.2 forward,
drift announces itself.

Verified against a faithful sandbox reproduction (registry pinned at 3.9.0, marketplace clone
at 4.7.2, both user and project scopes): heal moves both scopes, second run is silent
(idempotent), both opt-outs respected, `source=resume` skips, and the hook still exits 0 with
no vault. +27 tests (**576 total**); `bin/test-install.sh` green.

## v4.7.3 — Cadence directives are no longer "Stop hook errors", and count real turns (2026-09-27)

**Problem (user-reported: "tao hay bị lỗi này"):**

```
Ran 9 stop hooks
  ⎿  Stop hook error: [gowth-mem:self-review ws=trade] 15 turns logged. DISPATCH a fresh-context …
```

Nothing failed — and it came more often than configured.

**Root cause 1 — the error label was our choice of envelope.** `auto-journal.py` printed
`{"decision": "block", "reason": …}`. Claude Code (verified in the shipped 2.1.283 binary) files
a block reason under the stop-hook summary's `hookErrors`: red "Stop hook error: …" plus a
"Stop hook error occurred · ctrl+o to see" notification. Since **2.1.163** there is a sanctioned
channel (CHANGELOG: "Stop and SubagentStop hooks can now return
`hookSpecificOutput.additionalContext` to give Claude feedback and keep the turn going without
being labeled a hook error"): the same continuation — both land in the array that re-invokes the
model with `stopHookActive: true` — rendered gold as "Stop hook feedback", no notification; the
model reads "Stop hook additional context: …". The v4.7.2 note above called the label
unavoidable; that was only true before 2.1.163.

**Root cause 2 — Stop events are not turns.** The cadence counters were bumped on every Stop.
Claude Code also fires Stop (a) after every Stop-hook continuation — any hook's block/feedback,
ours included, re-invokes the model, which stops again with `stop_hook_active: true` — and
(b) after every background-agent `<task-notification>` relay, including the teammate and judge
gowth-mem itself dispatches. Each cadence therefore shortened the wait for the next one, and the
session log recorded "Stop hook feedback: …", notification XML and skill expansions as the
user's prompt — which the judge then scored as the user's prompting. Live audit of the 15 heavy
sessions of the last 30 days: **474 Stops counted as turns for 327 real turns** (×1.45); all 55
hook-feedback continuations were gowth-mem's own.

**Fixes:**

- **Non-error channel.** `_version.claude_code_version()` / `supports_stop_context()` read the
  host version from `AI_AGENT` (`claude-code_2-1-283_harness` — exported to every subprocess,
  hooks included, since 2.1.120; Claude Code only rewrites unset or claude-code-prefixed
  values). `auto-journal._stop_output()`: ≥ 2.1.163 → `hookSpecificOutput.additionalContext`;
  older or unknown → `decision: block`, the one shape every version acts on (a silently dropped
  directive is worse than a mislabeled one). Trigger tokens (`[gowth-mem:self-review ws=…]`, …)
  unchanged.
- **Real turns only.** A Stop counts iff its newest human-prompt record has a new `uuid`
  (`session.last_prompt_key`) — never its text ("tiếp" repeats). `_capture._classify_record`:
  *human* = `origin.kind == "human"` on a user record **or on a `queued_command` attachment** (a
  prompt typed while the model worked — not a user record at all), authoritative over any text;
  *machine* = any other `origin.kind` (Claude Code's own contract: keyboard input is stamped
  `human`; task-notification, peer, coordinator, plugin, observer, auto-continuation, …) except
  `channel`/`slack-ping` (they relay what a person typed elsewhere, e.g. a Telegram channel →
  *unconfirmed*), queued non-prompt commands, origin-less teammate / notification text; *transparent* = `isMeta`, compaction summaries, tool results, interrupt
  markers, local-command output, `!cmd` bash **output** (never recorded as the user's words — it
  can hold secrets); origin-less records with `promptSource` typed/queued/sdk or `turnOrigin`
  human/sdk are *human* (`claude -p` prompts are `sdk`) and `promptSource: system` is *machine*;
  any other origin-less record (`/goal`, `<bash-input>`, legacy prompts, but also `/model`) is
  *unconfirmed* — an ask only if the model answered it, or if it is the prompt the Stop input's
  `prompt_id` names: the FINAL assistant record is written after the Stop hook runs, so a
  tool-less answer is never visible yet.
  Machine records are skipped, not decisive: a teammate message delivered mid-turn must not
  swallow the turn it interrupted. A prompt pushed out of the 512 KB tail by heavy tool output
  is found by a bounded backward scan (`scan_back_for_human`, 32 MB). With record identity (every
  real transcript) `stop_hook_active` is redundant — a continuation without new input compares
  equal — and harmful as a skip: a prompt typed during our own dispatch continuation was dropped.
  It still decides for identity-less (legacy / hand-built) transcripts, which keep per-Stop
  counting. A transcript with identity but no human prompt (agent-team teammate sessions: only
  `<teammate-message>` prompts, 26 live sessions ran this hook) is never a turn — counting there
  fired cadences with no capturable log. A keyless prompt record under identity plus
  `stop_hook_active` is never a turn (latent block loop; every real prompt has a uuid).
- **Capture records the human — every ask of the turn.** `capture_turn`'s **User:** line holds
  every human ask since the previous Stop's summary record, oldest first (`request ⟶ follow-up
  typed while the model worked`), and Actions start at the first ask — keying the log on the
  newest ask alone dropped the main request in 13% of live turns (review round 2). When heavy
  tool output pushed the request out of the tail, a bounded backward scan stops at the previous
  Stop's summary. The tail is parsed once per Stop and shared.
- **Autosync on every Stop.** The debounced vault push also runs on Stops that are not turns —
  the relay Stop right after the background teammate/judge finishes is exactly when the vault
  holds fresh writes.
- **Deterministic tests.** `tests/test_review_trigger.py` inherited the runner's `AI_AGENT`
  (green in CI, red inside Claude Code) and `GOWTH_WORKSPACE` (16 failures in a `trade` shell).
  Harnesses now pin the host and clear `GOWTH_WORKSPACE`/`CLAUDE_SUBAGENT`; the
  directive-content contracts rerun on a pre-2.1.163 host; new `tests/test_stop_channel.py` +
  `tests/test_turn_counting.py`.

**Verified:**

- **Every recorded Stop, real hook, Stop-time-faithful replay** — all 173 transcripts of the
  last 30 days that ran this hook (950 Stops). At each Stop the hook sees the file exactly as
  Claude Code leaves it at that moment: WITHOUT that Stop's summary record and WITHOUT the final
  assistant record (both are written after the hooks), with the `prompt_id` and
  `stop_hook_active` Claude Code sends. Ground truth computed separately (`parentUuid` links):
  **706 real turns, 706 counted — 0 missed, 0 extra.** (A first harness that wrote the current
  summary before running the hook hid the round-2 capture bug — harness fidelity matters.)
- **Old vs new** on the 15 heavy sessions: turns **474 → 327** (= the 327 real), reviews 26 → 12
  (seven of those sessions had 7–14 real prompts — the review fired there on inflation alone),
  journal 38 → 27, every directive on the non-error envelope, fake "User:" lines **212 → 0**,
  captured turns 429 → 327 (56 of them now keep a typed-ahead follow-up with its main request).
- **End-to-end on the real 2.1.283 binary** (`claude -p --setting-sources project`, scratch
  vault, review forced at turn 1): the stop-hook summary has `hookErrors: []` with the directive
  in `hookAdditionalContext`, the model acted on it, and the continuation Stop was silent and
  uncounted (`total_turns: 1`). It also caught what no offline replay can see — a transcript
  snapshot taken *inside* a real Stop hook has no assistant record yet (the final reply is
  flushed after the hook), so the first design never counted a headless prompt; fixed with
  `promptSource`/`turnOrigin` and the Stop input's `prompt_id`, then re-verified end-to-end.
- **Adversarial fresh-context review, 3 rounds** — round 1 REQUEST CHANGES (1 high, 1 medium,
  5 low: typed-ahead prompts, answered bash mode, teammate-only sessions, origin authority,
  autosync on relay Stops, `/model` relays, runner-env leaks); round 2 COMMENT (1 medium: the
  typed-ahead capture regression above; 1 low: keyless-prompt loop; `channel`/`slack-ping`
  framing); round 3 on the final delta. Every finding verified against live transcripts or the
  binary before it was fixed.
- +71 tests (**647 total**), green in the Claude Code env, a CI-like env (no `AI_AGENT`) and a
  hostile runner env (`GOWTH_WORKSPACE=trade CLAUDE_SUBAGENT=1`); `bin/test-install.sh` green.

**Known limits:** a directive still costs one continuation turn (the channel's semantics);
headless `claude -p` pipelines still count their origin-less prompts (as before); a turn whose
prompt is more than 32 MB back is not counted; a host that inherits a newer Claude Code's
`AI_AGENT` without rewriting it (Claude Code < 2.1.120 nested inside ≥ 2.1.163, or a
non-Claude-Code harness running these hooks) would get the new envelope and drop the directive.

## v4.7.4 — Session logs carry the final answer and readable commands (2026-09-28)

**Problem:** the judge and the memory teammate read only the session log, and one turn in five
had an **empty `Claude:` line** — 141 of 706 live captured turns. Claude Code writes a turn's
final assistant record to the transcript only AFTER the Stop hook runs (the v4.7.3 in-hook
snapshot), so a tool-less answer was never visible to `capture_turn`. Separately, `/goal` and
`!cmd` turns logged Claude Code's raw `<command-name>…<command-args>` / `<bash-input>` markup
as the user's words (12 live lines).

**Fixes:**

- **Final answer from the Stop input.** `auto-journal` passes `last_assistant_message`
  (Claude Code ≥ 2.1.47) to `capture_turn(final_text=…)`; it follows the turn's narration
  unless it is already the last text seen (a host that flushes before hooks cannot duplicate
  it). When narration + answer exceed the 300-char line, BOTH ends are kept
  (`narration[:148] … answer[:149]`) — otherwise the head cap cut the answer off in 70% of
  turns.
- **Sanitize before capping — bounded.** Every captured field (prompt, narration, answer,
  actions, thinking, each tool argument) is sanitized BEFORE it is sliced — a secret cut at a
  field cap became a fragment the sanitizer no longer recognised (a Bash token straddling the
  60-char argument cut leaked). Only `cap + 4 KB` of a field is sanitized: `_privacy`'s regexes
  are super-linear on adversarial input, and a 200 KB pasted `a.a.a…` prompt + answer made one
  Stop take **144 s** (no hook timeout is set) — now well under 5 s, pinned by a test.
- **Readable prompts, safely.** `_capture.display_prompt()` renders `/goal ship it` and
  `!kubectl …` on the `User:` line only when the WHOLE text is that markup (a prompt quoting a
  tag, extra words after it, or two command blocks stay verbatim); linear-time parsing via an
  anchored tag loop — the first draft's `\s*(.*?)\s*` backtracked cubically (an unclosed
  `<command-name>` + 4k spaces: 31 s) and its unanchored replacement was quadratic on many
  unclosed tag starts (20k: 19.5 s); both caught in review and pinned by regression tests.
  Classification still reads the raw text.
- **Round-3 LOW closed.** The beyond-the-tail ask scan runs only on transcripts with record
  identity — a hand-built one (> 512 KB, no uuids, no Stop summaries) joined every ask in
  32 MB and repeated the chosen prompt.

**Verified:** Stop-time-faithful replay of every recorded Stop (957 across 173 transcripts;
the hook sees the file without the current summary and final assistant record, with the
`prompt_id`, `stop_hook_active` and `last_assistant_message` Claude Code sends): empty
`Claude:` lines **141 → 0**, the answer now visible in every turn (496 of 707 keep plan … answer),
0 lines over the cap, 0 secret fragments, raw-markup `User:` lines **12 → 0**, counting
unchanged (707 real turns, 707 counted, 0 missed). Fresh-context review, three passes: APPROVE
with 1 medium (backtracking) + 5 low → REQUEST CHANGES on the fixes (2 medium freeze paths: the
quadratic tag scan, uncapped fields into `_privacy`) → fixed and re-verified. End-to-end on the real 2.1.283 binary: a headless turn whose
answer is never on disk at Stop time logs `**Claude:** pong`. +14 tests (**661 total**), green in the Claude Code, CI-like and hostile runner envs;
`bin/test-install.sh` green.

## v4.7.5 — Private keys and `<private>` blocks can no longer leak into the vault (2026-09-28)

Four privacy gaps, surfaced by the v4.7.4 review and a follow-up security review. Every one
predates v4.7.4. A read-only scan of
the live vault (2,012 `.md` files + its git history) found none of them exploited: 0 key bodies,
0 unredacted private-key headers.

**Fixes:**

- **Whole private keys, not just their header.** `_privacy`'s `ssh-private` rule replaced only
  the `-----BEGIN … PRIVATE KEY-----` line, so the base64 body — the actual secret — and the END
  line survived in every synced write (it runs on every `safe_write`); PGP `… PRIVATE KEY BLOCK`
  never matched at all. Now a closed block (header, armor headers such as `Proc-Type`/`DEK-Info`/
  `Version`, a body of only base64/whitespace, and an END of the SAME kind) is removed whole;
  otherwise (no END — a key cut by a cap or pasted partially — or prose before the END) the
  header + armor headers + every whole base64 body line go, after the blank line RFC 4880/1421
  put between headers and body, and the prose after them survives. Line breaks may be real or
  JSON-escaped (`"private_key": "-----BEGIN PRIVATE KEY-----\nMIIE…"` from a service-account
  file; PHP-style `\/` too). A residue rule strips bodies that ≤ v4.7.4 machines left under
  `[REDACTED:ssh-private]` in the SHARED vault — it REQUIRES their END line: the marker is also
  what the new rule writes, and a first draft with an optional END deleted hash/fingerprint lines
  after a redacted key on every write (caught by the security review; now pinned by an
  idempotence test). Public keys and certificates are untouched.
- **Capture sanitizes raw text.** `capture_turn` collapsed every field to one line BEFORE
  sanitizing, so no line-based key rule could fire on the synced `User:` line (a 7 KB PGP key,
  legacy PEM and partial pastes leaked ~2,000 body chars — found by the security review; the
  first round of tests only exercised `sanitize()`). Fields are now sanitized raw, then collapsed,
  then capped.
- **`<private>` blocks larger than a capture window.** v4.7.4 sanitizes only `cap + 4 KB` of a
  field; a `<private>` block whose closing tag lay beyond it never matched and its first ~2,000
  chars leaked. Now a `<private>` still open in the window cuts the field when a closing tag
  follows beyond the window — or straddles its edge (a bare mention of the tag stays;
  Unicode-safe — `.lower()` changed lengths).
- **No more quadratic sanitizer paths.** `<private>…</private>` stripping was quadratic on
  unclosed tags (0.9 MB: **298 s**, on every whole-file `safe_write`) → a linear two-pointer
  scanner with the regex's exact semantics (3,000-case differential fuzz; reviewer: 40,000
  cases); `db-url-creds`' unbounded scheme run (100 KB of `a.a.a`: 18 s) → bounded `{2,31}`;
  `jwt` restarted a scan at every `eyJ` inside `eyJ-eyJ-…` (200 KB: 11.4 s — missed by the first
  probe, found by the review) → starts only at a token boundary. The key-body scan is unrolled
  (an alternation kept per-character state: 5 MB peaked at 1.1 GB, and a MemoryError bypasses
  sanitizing). Armor headers are matched ATOMICALLY (lookahead-capture + backreference — 3.9 has
  no atomic groups): once the residue rule could fail after its headers, a header value ending
  in a space split two ways and every combination was retried — exponential (20 lines: 4 s,
  ~30: over an hour; security review round 3) — now 50,000 lines in 122 ms, identical output on
  a 30,000-case differential fuzz. Every shape now ≤ ~150 ms on 200 KB.
  `reflection.max_prompt_chars` clamped to 8192.

**Verified:** 25 new tests — keys (closed / unclosed / legacy armor / blank-line armor / PGP /
JSON- and PHP-escaped / mismatched END / residue + idempotence / public untouched / linear time /
bounded memory / jwt / atomic armor headers) driven through BOTH
`sanitize()` and the real `capture_turn`; the window leak, Unicode cut, bare mention; the clamp;
the straddling tag; private-block linearity + differential fuzz. The old and new sanitizer over all 2,012 real vault
`.md` files: **identical outputs and redaction counts**, same total time — `safe_write`
re-sanitizes whole files, so the new rules cannot silently redact existing memory on the next
write. Stop-time-faithful replay of 959 real Stops: counting and capture unchanged (707 real
turns, 707 counted, 0 missed). Fresh-context security review: REQUEST CHANGES (1 high, 2 medium,
3 low) → REQUEST CHANGES on the fixes (1 medium: the residue rule eating memory; 1 low-medium:
the straddling tag; 1 low: memory) → REQUEST CHANGES (1 high: exponential backtracking in the
armor headers) → fixed and re-reviewed. +25 tests (**686 total**), green in
the Claude Code, CI-like and hostile runner envs; `bin/test-install.sh` green.

**Known limits (not covered, all pre-existing):** PuTTY `.ppk` and TSS2 keys; Kubernetes
base64-wrapped PEM (`LS0tLS1CRUdJTi…`); keys quoted with `> `; an unclosed PHP-escaped JSON key;
a < 16-char key fragment left at a capture-window cut.

## v4.7.6 — Topics are their folders; tags start at token starts; 3.9 is tested (2026-09-28)

Four open items from the v4.7.3–v4.7.5 sessions, each measured on the live vault first.

**Fixes:**

- **The router no longer mints junk topic folders.** `route()` / `derive_topic_slug()` took the
  topic slug from the best-matching file's frontmatter `slug:`. A dated aspect carries
  `slug: <topic>-<aspect>` (`_validate.fix_aspect`), so whenever an aspect out-scored its
  folder's README, `ensure_topic_folder(<topic>-<aspect>)` created a README-only sibling while
  the entry itself still landed in the right folder: **31 junk folders** in the live vault
  (devops 16, trade 13, personal 2), and `_lesson` — same slug — filed a real lesson inside one.
  On 120 sampled real entries the old selection would have minted a junk folder for **34**.
  One shared `_pick_topic()` now derives identity from where the file lives (`slug_for_path`);
  `_ensure_landing()` adds a skeleton README only inside the matched folder, never via a
  re-derived slug, and never inside a DOMAIN (a folder with topic folders anywhere below it — a
  README would hide them); a loose file inside a domain is no match candidate (entries matching
  it piled up where no MOC or list looks). `_lesson.py` decides with `plan_topic_folder()` and
  creates with `materialise_topic_folder()` (one vault walk); an explicit `--topic` that exists
  only nested resolves in place, and one naming a domain or several nested topics is refused
  with the candidates listed — as is one that resolves outside the workspace through a symlink
  (HEAD's path-escape guard; a first draft of this fix bypassed it for folders that already had a
  landing, sending lessons to the link target — never synced, never indexed). A new or promoted
  topic never takes a domain's name (a default `misc/` holding topics gets `misc-notes/`, then
  `-notes-2`…, within the 60-char limit), and a hand-edited `default_topic` that is no usable slug
  (`docs`, `Misc`, `../..` — the last one made planning scan the vault's parent) falls back to
  `misc`. The MOC rebuild no longer writes READMEs through a topic folder symlinked out of the
  vault (pre-existing: it overwrote hand-written notes kept in another repo). The same root cause had three more victims, reproduced
  on HEAD first: a NESTED topic got a top-level twin; a legacy `<dir>/<dir>.md` folder note was
  shadowed by a new skeleton README; and under a symlinked `GOWTH_MEM_HOME` (every macOS temp
  dir) a legacy flat `<ws>/<name>.md` match wrote the new aspect loose at the workspace root plus
  a `<ws>/<ws>/` folder (the walk yields unresolved paths; `route()` compared them to a resolved
  root). Loose root-level aspects are no longer treated as flat topics. Routing is
  deterministic across machines: equal-length keywords keep their order of first appearance
  (`sorted(set, key=len)` followed per-process str-hash order — three runs, three slugs; an
  alphabetical tie-break, tried first, made the gate-mandated `because` a systematic slug word)
  and the vault walk is sorted (`rglob` order differs between APFS and ext4). A reserved word as a new topic
  (`[exp] see the research`) used to crash the write; it now goes to the default topic.
- **Refused writes create nothing, and say why.** `append_entry` routed — creating folders —
  before its dedup check and the §1 gate, so a refused entry left a README-only folder no junk
  check can prove; `_lesson` did the same. `_plan()` (pure) now decides, the checks run, and only
  then `_materialise()` touches disk. `_topic.py --append` prints `written`, `duplicate` or
  `rejected:<gate rule>` — or `rejected:unroutable` when no safe topic exists (a gate refusal used
  to print `duplicate`, and the memory teammate then
  no-op'd an entry it could repair by adding its `Source:`); the teammate template now says to fix
  and retry. `_lesson.py` prints `not appended (…)` instead of `appended:` for a refused lesson and
  refreshes the MOC only after a real write; the `/mem-lesson` one-liner reports the status too.
  The `/mem-topic route` preview (`_topic.py --route`) no longer creates the folder it predicts —
  a previewed gate-reject used to leave permanent, unprovable junk.
- **Junk folders are repairable — explicitly.** `_validate.py --scan` reports
  `junk-topic-folder`; the new `--prune-junk` (NOT `--fix`) deletes one only when every condition
  holds: a real, non-symlink folder at the workspace root; nothing in it but a plain
  `00-README.md` (+ a tolerated `.DS_Store`); that README is the pristine DEFAULT skeleton,
  frontmatter included (only the dates may differ); and its name is the `slug:` of a file in
  another folder — ANY file: 6 of the 31 live junk folders came from slugs `fix_aspect` never
  wrote (a date-prefixed, a moved, a renamed and a research-imported aspect, a different clamp, a
  `lessons.md`). The README is renamed aside and re-verified before the unlink (an edit racing
  the prune is restored; if a newer README appeared meanwhile, an edited copy is kept visibly as
  `00-README.conflict-<pid>.md`), the folder is re-listed right before the delete and removed with
  `rmdir` (if even that loses a race, the README is written back from the verified bytes), and
  `_MAP.md` is rebuilt; the delete loop can only ever unlink a tolerated `.DS_Store`. An
  interrupt puts the README back, and an undeletable file restores it and moves on to the next
  folder. A hard kill's leftover `.00-README.md.pruning-<pid>` is reported by `--scan` and restored
  by the next prune once its process is gone or it is an hour old (pids repeat, and mean nothing
  for a leftover synced from another machine); `--prune-junk` backfills the vault's `.gitignore`
  with `.*.pruning-*`. With `--fix --prune-junk` the prune runs first, so a slug stamped
  by the same run is no evidence.
  Machines still on ≤ v4.7.5 keep minting junk until upgraded, hence a command. Live vault: the
  stranded lesson moved verbatim into `devops/service-health-alerting/lessons.md`, then **32
  folders removed**; re-checked against the final rule from the backup: no symlinks, all 31
  router-minted READMEs pristine including frontmatter; `_MAP.md` diffs drop exactly those 32
  topics. The 15 other README-only folders (deliberate placeholders like `trade/ema-cross`) did
  not match and were left alone.
- **Tags start at token starts, in linear time.** `_tags.py`'s identifier patterns could start
  inside a word: "Stop-hook" → `#top-hook`, "Port-forwarded" → `#ort-forwarded`, "FTS5-only" →
  `#5-only` — **85 mangled tags** in the vault; 193 mid-word fragments across 3,595 real entry
  lines, **0** now. Each start also re-scanned the rest of the run, so DOTTED/SNAKE/KEBAB/CAMEL
  were quadratic: one 40k-char base64/hex run made `extract_tags` take **52.6 s** (4× per
  doubling). Each pattern's lookbehind now rejects every character its body can consume — one
  attempt per run, ~2× per doubling, ~10 ms at 40k. KEBAB is case-insensitive (`stop-hook`,
  `esp32-s3`, `usb-jtag`); DOTTED may start after a `.` or on digits a letter/`_`/`-` follows,
  so `.claude.json`, `.gitlab-ci.yml`, `01-db-findings.md` stay whole while a numbered step
  `1.Install` is no identifier. Tags have two caps: 64 for prose and bigrams (a pasted blob had
  become one 40,000-char tag), 128 for identifiers (router aspect filenames reach 74 chars, FQNs
  and env vars run past 80); a longer "identifier" is not harvested and its words go to prose.
  Substring collapse was O(k²) over every candidate (88k chars of distinct words: 2.4 s); the
  pool is bounded at 512 per class — above the largest real entry (182 identifier / 385 prose
  candidates), so none of the 3,595 vault entries changes a tag (a 64 pool changed 37); 700k
  chars of adversarial input now take < 0.2 s. `v2.x` wildcards are dropped.
  `strip_tags()` was quadratic on whitespace runs (40k: 2.3 s) on every line `_dedup` /
  `_index` hash; it now matches the reversed line at its start, byte-identical to the old regex
  (400,000-case differential fuzz; the reviewer added 2.4 M exhaustive strings ≤ 7 chars and all
  29 `\s` code points on 3.9 and 3.14), so no stored dedup hash moves. Existing mangled tags are
  left as written (`/mem-retag` never rewrites entry lines); new writes are clean.
- **The drift notice names the right fix.** A session that started before the local update
  (SessionStart also fires on `/compact`, `/clear`, resume) printed "Claude Code left
  installed_plugins.json pinned … `claude plugin update`" while the registry already recorded the
  new version. `drift_nudge()` now reads every registry entry — the version at its `installPath`,
  which is what loads (bug #52218 can bump the `version` field alone, so the field never counts on
  its own; an `installPath` that is missing or gone is unprovable, never current — a reload from
  it would skip every hook):
  all current → "update not loaded in THIS session … `/reload-plugins`" (with the `/mem-doctor`
  escalation); stale, mixed scopes or unprovable → the old text.
- **Python 3.9 is tested.** `tests/test_tags.py` annotated `dict | None` without the
  `__future__` import, so on 3.9 the module failed to import and its 32 tests silently never ran
  (CI used `3.x` only). CI now runs 3.9 (pinned to `ubuntu-24.04`: 3.9.25 has no 26.04 build)
  and `3.x`; `tests/test_py39_compat.py` fails the build
  on 3.9-incompatible syntax or runtime-evaluated PEP 604 annotations (class bodies inside
  functions included) on any interpreter.

**Verified:** every fix reproduced on HEAD first — the routing tests fail there by reproducing
the bugs (the junk sibling, a lesson filed inside one, the symlinked-home flat match; the rest
exercise API that is new), and 10 of the 13 new tag tests fail (they take 70 s on HEAD: the
quadratic paths). On real data: 120 sampled entries → the old selection mints junk for 34; 3,595 entry
lines → 193 mid-word fragments become 0, dotted tags lost only as fragments now kept whole; the
bounded collapse pool changes 0 of 3,595 entries; every changed regex ≤ ~2.1× per doubling over
10k/20k/40k chars on 30+ adversarial shapes; `strip_tags` identical to the old regex on 400,000
fuzz cases (the reviewer added 2.4 M exhaustive strings). Delete path mutation-tested: every
protection's revert turns a test red (redundant layers removed pairwise). Live vault: 32 junk
folders removed (re-checked against the final rule from a backup), the stranded lesson moved
verbatim, 193 aspects' frontmatter repaired with 193/193 bodies byte-identical, scan: 0 issues.
Fresh-context review: 6 rounds — REQUEST CHANGES ×3, each with a HIGH in this release's own new
code (a same-run slug stamp deciding a delete; reload advice into a missing installPath; an
explicit `--topic` written through a symlink out of the vault), then APPROVE ×3 (4 + 2 LOWs
fixed in-release, 3 test/preview LOWs left as follow-ups). +78 tests (**764 total**), green on
Python 3.9.21 and 3.14.6; `bin/test-install.sh` green.

**Process lesson (now a Development Rule):** the live cleanup ran one minute BEFORE the review of
its own delete code, whose round 1 found a delete-outside-the-vault symlink defect in that path —
the backup and the re-check showed nothing was lost, but new delete paths now run only on a copy
until reviewed, then only with the user's go-ahead.

**Follow-ups (not in this release):** `ensure_topic` / `/mem-topic ensure` can still put a README
on a domain; `_forget.py --aspects` walks symlinked topic folders (manual path; auto-archive is off
by default); `[skill-ref]` writes follow a symlinked `skills/`; tests for the `_materialise`
wrap and `_ensure_landing`'s error type; the `--route` preview's traceback when all 21 `-notes`
names are taken; `_commitmsg` labelled the 32-README cleanup commit "2 lessons"; the 85 mangled
tags already in the vault stay as written.
