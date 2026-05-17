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

### Tier 4 — out of scope

12. RAPTOR / GraphRAG / HippoRAG — handled by claude-obsidian's wiki-fold + lint, or future plugin.
13. AutoCompressor / gist tokens — needs custom model, defer.
14. ColBERT / ColPali — overkill for markdown vault.
