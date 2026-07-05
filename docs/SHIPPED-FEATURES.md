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
