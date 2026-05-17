# SHIPPED FEATURES — gowth-mem roadmap log

History of what landed, in which version, and why. This is the audit trail for `RESEARCH.md`'s
catalog: any item with `✅ SHIPPED` in that catalog points here.

Live, unreleased items live in `RESEARCH.md` under the unshipped tiers; once shipped they migrate
here with a version tag.

## v0.4 roadmap (priority by ROI)

### Tier 1 — ship now (no new deps)

1. **mem0 ADD/UPDATE/DELETE/NOOP** in mem-distill skill — prevents dedup bloat, file-size wins.
2. **Contextual retrieval** in recall-active.py — prepend heading/breadcrumb to each match line. Reported 35-67% reduction in retrieval failures.
3. **MMR diversity** in recall-active.py — when 3 hits cluster in same file, pick across files.
4. **Voyager skill library** convention — `docs/skills/<name>.md` with description + steps. Auto-loaded by recall when intent matches.
5. **Generative Agents reflection** — `/mem-reflect` reads journal, produces 1-3 high-level summaries to docs/exp.md or wiki/concepts.

### Tier 2 — needs light infra (sqlite-vec, embedding API)

6. ✅ **SHIPPED v0.6**: Hybrid BM25 + vector recall via SQLite FTS5 + sqlite-vec + RRF fusion. Auto-detects `OPENAI_API_KEY` / `VOYAGE_API_KEY` / `GEMINI_API_KEY`. Graceful 3-tier fallback: vector hybrid → FTS5-only → grep. Build/refresh via `/mem-reindex`.
7. **SKIPPED**: Semantic response cache (GPTCache pattern). Stale-answer risk for evolving code work; limited ROI for our retrieval-only path.
8. ✅ **SHIPPED v0.5**: Spaced resurfacing — `.gowth-mem/state.json` SM-2-lite tracker; ~25% prob per prompt resurfaces files unseen ≥7 days.

### Tier 3 — architectural

9. ✅ **SHIPPED v0.5**: Temporal facts — `valid_until: YYYY-MM-DD` and `(superseded)` markers; recall auto-skips invalid lines.
10. ✅ **SHIPPED v0.6**: HyDE-lite — exposed as opt-in `/mem-hyde-recall <question>` skill rather than auto-on-prompt. Drafts hypothetical answer, embeds via index (or falls back to keyword grep), synthesizes against retrieved chunks.
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

15. ✅ **UserPromptSubmit intent → inline skill body** — `user-augment.py` detects intents (save / skillify / reflect / bootstrap; English + Vietnamese) and injects FULL skill instructions inline. Claude executes the skill behavior without `/mem-*` slash command.

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

### Tier 4 — out of scope

12. RAPTOR / GraphRAG / HippoRAG — handled by claude-obsidian's wiki-fold + lint, or future plugin.
13. AutoCompressor / gist tokens — needs custom model, defer.
14. ColBERT / ColPali — overkill for markdown vault.
