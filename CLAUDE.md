# gowth-mem — Plugin Development Rules

## Identity

Claude Code plugin for **persistent, topic-organized memory** synced via git remote.
Named after **OpenClaw** (self-hosted AI agent gateway, 368k stars) — gowth-mem is the
"bridge" that brings OpenClaw's memory architecture patterns to Claude Code users.

Built on research from 12 LLM memory systems + OpenClaw's memory-core/dreaming — see
`RESEARCH.md` for full catalog, `.claude/research/` for distilled insights.

## OpenClaw Lineage

gowth-mem studies and adapts patterns from [OpenClaw](https://github.com/openclaw/openclaw):

| OpenClaw Pattern | gowth-mem Adaptation |
|---|---|
| `MEMORY.md` long-term memory | `shared/AGENTS.md` + topic files |
| `memory/YYYY-MM-DD.md` daily notes | `workspaces/<ws>/journal/<date>.md` |
| Dreaming (Light→REM→Deep consolidation) | `auto-journal.py` + `_prune.py` + `_forget.py` (v3.6 active forgetting: journal raw-TTL → gzip archive) |
| `memory_search` hybrid recall | DROPPED v3.2 (token cost > benefit); `index.db` now slug-only via `_wikilink` |
| `AGENTS.md` operating instructions | `shared/AGENTS.md` |
| `skills/<slug>/SKILL.md` | `shared/skills/<slug>.md` |
| Pre-compaction memory flush | `precompact-flush.py` HARD-BLOCK |
| Auto-detect embedding provider | `_embed.py` auto-detect from env |

Key gap vs OpenClaw: no staged consolidation pipeline, no multi-signal deep ranking,
no contradiction detection. See `.claude/research/product-architecture.md` for full comparison.

## Priority Order (highest first)

1. **Data safety** — Never lose user memory. Atomic writes, fcntl locks, conflict resolution over raw markers.
2. **Recall quality** — Right memory at the right time. Hybrid BM25+vector, MMR diversity, SRS resurfacing.
3. **Token efficiency** — Bootstrap ≤60k chars. Stable prefix (AGENTS/secrets/tools) for Anthropic prompt cache hits.
4. **Simplicity** — Pure stdlib Python 3.9+ in hooks. SQLite for indexing. No pip deps in runtime path.
5. **Cross-machine sync** — Git-based, conflict-aware, token-secure via HTTP header (never in URL).

## Architecture (v3.0+)

```
~/.gowth-mem/
├── shared/                         cross-workspace knowledge
│   ├── AGENTS.md                   global operating rules
│   ├── secrets.md                  pointers only (env-var names)
│   ├── tools.md                    tool quirks
│   └── skills/<slug>.md            reusable workflows
├── workspaces/<ws>/                per-workspace knowledge
│   ├── docs/{handoff,exp,ref,tools,files}.md
│   ├── journal/<date>.md
│   ├── skills/<slug>.md
│   ├── research/<topic>/           deep-research scratch (raw/ + distilled.md)
│   └── <slug>/                     v3 topic folder
│       ├── 00-README.md            MOC (auto-rebuilt)
│       ├── YYYY-MM-DD-<aspect>.md  dated aspect (route() writes here)
│       └── lessons.md              per-topic ledger
├── settings.json                   synced behavior (layout_version: 3)
├── config.json                     local-only (gitignored): remote, branch, token
├── state.json                      SRS data (gitignored)
├── index.db                        FTS5 + optional sqlite-vec (gitignored)
├── .backup/v2-pre-v3-<utc>/        migration snapshots, rolling-2 (gitignored)
└── .locks/                         fcntl lock files (gitignored)
```

## Key Decisions

| Decision | Rationale |
|---|---|
| Topic-based over folder-based | Users think in topics ("EMA strategy"), not directories |
| `shared/` + `workspaces/<ws>/` split | Cross-project knowledge (secrets, tools) vs project-specific (handoff, topics) |
| Hybrid recall (BM25 + vector + grep) | 3-tier graceful degradation; no hard dep on embedding API |
| mem0 ADD/UPDATE/DELETE ops | Prevents dedup bloat vs blind append (mem0, Generative Agents pattern) |
| 9-type schema prefixes | `[decision]`, `[exp]`, `[ref]`, `[tool]`, `[reflection]`, `[skill-ref]`, `[secret-ref]`, `[goal]`, `[hypothesis]` |
| Token via HTTP header per-command | `git -c http.<url>.extraHeader=AUTHORIZATION: basic <b64>` — never in remote URL |
| SM-2-lite SRS resurfacing | ~25% prob/prompt for files unseen ≥7 days; prevents knowledge rot |
| fcntl + atomic write | Multi-session safety; SQLite WAL for concurrent index access |
| Conflict → SYNC-CONFLICT.md | Raw `<<<<<<<` markers break FTS5 indexing; AI-mediated resolution instead |

## Commands

```bash
python3 -m unittest discover -s tests                 # full suite (351 tests)
python3 -m unittest tests.test_gate                   # single module
python3 -m unittest tests.test_gate.TestClass.test_x  # single case
python3 -m py_compile hooks/scripts/*.py              # compile check
bin/test-install.sh                                   # clean-room install+upgrade+hook smoke test (temp GOWTH_MEM_HOME)
bin/doctor.sh --dry-run                               # diagnose plugin registration (installed_plugins.json)
bin/release.sh [patch|minor|major]                    # bump plugin.json+marketplace.json in lockstep, commit, tag, push
```

CI (`.github/workflows/ci.yml`) runs exactly `py_compile` + `unittest discover` — green locally means green in CI.

To exercise anything against a scratch vault, set `GOWTH_MEM_HOME=/tmp/...` — all scripts resolve the vault root through `_home.py` (this is how tests and `bin/test-install.sh` isolate themselves from the real `~/.gowth-mem/`).

## Plugin Anatomy (this repo)

The repo is both a standalone Claude Code plugin and a single-plugin marketplace (`.claude-plugin/{plugin,marketplace}.json`; versions kept in lockstep by `bin/release.sh`).

```
hooks/hooks.json          event → script wiring (see below)
hooks/scripts/_*.py       importable library modules (underscore prefix = shared lib, unit-tested)
hooks/scripts/*.{py,sh}   hook entrypoints (no underscore): read JSON event on stdin, ALWAYS exit 0
commands/mem-*.md         37 slash commands (YAML frontmatter + instructions; frontmatter `description:` must not contain a bare `: `)
skills/<name>/SKILL.md    13 auto-trigger skills (subset of commands that also fire on description match)
templates/                vault-file scaffolds + externalized hook instruction blocks (auto-journal, self-review)
bin/                      operational shell: release, doctor, test-install, migrate-v3/rollback-v3
tests/                    unittest suite + fixtures
docs/SHIPPED-FEATURES.md  version-by-version audit trail (RESEARCH.md roadmap ✅ markers point here)
```

Hook wiring (`hooks/hooks.json`):

| Event | Script | Role |
|---|---|---|
| `SessionStart` | `session-start.sh` | vault bootstrap context injection (branches on `source` field) |
| `UserPromptSubmit` | `conflict-detect.sh` | pure-bash pre-check; Python only runs if `SYNC-CONFLICT.md` exists |
| `Stop` | `auto-journal.py` | journal cadence, session capture, prune/consolidate/forget, 15-turn self-review |
| `PreCompact` | `precompact.sh` | deterministic transcript raw-dump — must NEVER block `/compact` |
| `PostCompact` | `auto-sync.py --pull-rebase-push` | git sync after compaction |

Shell wrappers exist to dodge Python startup cost on hot paths: keep cheap pre-checks in bash, branch into Python only when work exists.

## Research Lineage

Full research archive: `RESEARCH.md` (12 systems, retrieval algorithms, token techniques, PKM patterns).
Distilled insights in `.claude/research/`:
- `openclaw-vision.md` — OpenClaw dream, dreaming 3-phase consolidation, memory-wiki, what gowth-mem can learn
- `product-architecture.md` — OpenClaw vs gowth-mem architecture comparison, gaps worth closing
- `architecture-decisions.md` — 10 ADRs with rationale and trade-offs
- `memory-systems.md` — what we adopted from 12 systems and why
- `retrieval-algorithms.md` — 8 implemented algorithms, 3-tier degradation, embedding model choice
- `v3.4-brain-memory.md` — biology→arch translation (CLS theory, pattern separation, schema theory, reconsolidation, Ebbinghaus, sleep consolidation)
- `v3.4-llm-memory-systems.md` — 2025-2026 survey of mem0, Zep, HippoRAG, Letta, A-MEM, Cognee, LangMem; 7 convergent best practices
- `v3.4-hook-patterns.md` — Claude Code hook efficiency canon (claude-mem reference + 5 actionable patterns)
- `v3.6-brain-storage.md` — storage-structure deep research (Gemini+Perplexity): file-size caps (≤500 lines), 4-tier brain layout, the capture→distill→**forget** pipeline; companion to `shared/research/data-quality-2026.md` (what-to-keep)
- `v3.7-supremor-comparison.md` — study of the TrueProfit `supremor` team vault vs gowth-mem: supremor's **file-level schema validator** (vault-keeper) is the biggest transferable win; adopted as `_validate.py`. Also: deliberate taxonomy, themed changelog, work-board handoff, SSOT router (recommended next)

**5 convergent patterns** across mem0, Letta, Zep, Cognee, Anthropic, LangMem, LlamaIndex, MemoRAG, HippoRAG, Generative Agents, Voyager, Reflexion:

1. 2-3 tier hierarchy (working / mid / archival)
2. Async extraction offline, lightweight retrieval online
3. Semantic + episodic + procedural taxonomy
4. ADD/UPDATE/DELETE rewrite beats blind append
5. Multi-signal scoring (semantic + BM25 + entity + recency) at retrieval

## Development Rules

- `hooks/scripts/*.py` — pure stdlib Python 3.9+. Zero pip deps in runtime path.
- Templates live at `templates/`. Never hardcode `~/.gowth-mem/` paths — use `_home.py` resolver.
- Every hook must handle missing `~/.gowth-mem/` gracefully: `exit(0)`, no traceback.
- All file writes to `~/.gowth-mem/` go through `_atomic.atomic_write` (tempfile + `os.replace`).
- Concurrent access protected by `_lock.py` (`fcntl.flock`, timeout).
- Topic routing goes through `_topic.py` — never write directly to topic files.
- New entries must pass `_gate.py` (content) and `_validate.py` (file structure) — the gate/validator are the enforcement layer; docs alone proved insufficient.
- Before claiming done: full suite + compile check (see Commands); `bin/test-install.sh` for anything touching hooks, install, or migration.

## Anti-patterns

- Embedding tokens in git remote URLs (use HTTP header instead).
- Leaving raw `<<<<<<<` conflict markers in markdown (breaks FTS5 indexing).
- Writing to topic files without `_topic.py` routing.
- Skipping `atomic_write` for any file under `~/.gowth-mem/`.
- Adding pip dependencies to hooks — stdlib only.
- Hardcoding `~/.gowth-mem/` — use `_home.py` with `GOWTH_MEM_HOME` env override.
- Syncing real secret values — `secrets.md` stores pointers only (env-var names).

## Shipped Features

See `RESEARCH.md` § F for roadmap with `✅` markers. Key shipped items:

- **v0.5**: SRS resurfacing, temporal facts (`valid_until:`), token cost estimator, prompt caching guidance
- **v0.6**: Hybrid BM25+vector recall (FTS5 + sqlite-vec + RRF), HyDE-lite retrieval
- **v0.7**: Auto-trigger hooks (Stop/PreCompact/UserPromptSubmit), MemPalace-inspired
- **v0.9**: 7-type strict schema, quality gates, active auto-DELETE via `_prune.py`
- **v2.7**: `shared/` + `workspaces/<ws>/` dual-load, workspace resolver
- **v2.8**: Topic-type templates (10 vocabulary), `ensure_topic` dispatch
- **v2.9**: OpenClaw dreaming pipeline (staged consolidation + multi-signal recall + contradiction lint)
- **v2.10**: Deep-research workflow commands (`/mem-research-start`, `/mem-research-distill`, `/mem-research-status`), AGENTS.md rules moved to SessionStart-only (was duplicated every turn), cleanup stale files (mem-init stub, mem-migrate skill, mem-recaller agent, v1.0 settings template)
- **v3.0**: Topic-folder + dated-aspect layout (`<ws>/<slug>/{00-README.md, YYYY-MM-DD-<aspect>.md, lessons.md}`); `_topic.route()` always returns a dated aspect path; 6-tier wikilink fallback for multi-machine partial migrations; F16 layer_score buckets (90 today / 80 MOC / 75 lessons / 70 older / 65 research / 40 shared-skills); 7-step `_migrate_v3.py` pipeline with microsecond-resolution backups, `origin/<branch>` short-circuit, fetch+ff-only safety; rolling-2 backup window; `bin/rollback-v3.sh` non-destructive restore; `research/` added to reserved subdirs
- **v3.2**: deterministic-only retrieval (no LLM in vector path), 4-tier weighted context planner, rtk-style pre-storage compression, heuristic contradiction lint, char-trigram Jaccard fuzzy fallback
- **v3.4**: hook consolidation (shell pre-check on UserPromptSubmit, merged SessionStart/PreCompact, externalized auto-journal REASON, settings-tunable cadence, subagent-skip via env+hook_event_name+in_loop+agent_type); tag-aware FTS5 (`tag TEXT` indexed column + idempotent migration under `file_lock("index-migrate")` + cross-file SHA-1 dedup wired into `_lesson.append_lesson` and `_topic.append_entry`); `/mem-dream` skill wrapping `_consolidate.py` three phases (Light/REM/Deep) with per-workspace state filtering; command surface pruning (33→28 — removed `mem-bootstrap`, `mem-flush`, and 4 `mem-workspace-*` subcommand stubs; added `mem-recall`); `/mem-recall` slash command + `_query.query_by_type(ws, tag, query)` API. Grounded in `.claude/research/v3.4-{brain-memory, llm-memory-systems, hook-patterns}.md`.
- **v3.5**: deterministic transcript raw-dump replaces force-LLM block on PreCompact (`precompact-flush.py` writes recent user/assistant turns into `<ws>/journal/<today>.md`; classification deferred to `/mem-distill`).
- **v3.5.1**: precompact hook NEVER blocks `/compact` (fixes v3.5 fallback that still emitted `decision:block` JSON when raw-dump failed — auto-compact firing on context overflow is no longer brickable). All failure paths log via `_debug` and pass-through with exit 0 and empty stdout. `bin/doctor.sh` also detects stale pre-v3.5 `$HOME/.claude/hooks/precompact-force-memsave.sh` artifact and prints exact cleanup commands.
- **v3.6**: **active forgetting** — journals are now the *ephemeral hippocampal buffer*, not permanent. `_forget.py` enforces the canon §3 raw-TTL (default 7d): salvages curated `- [type]` entries into `journal/_salvage.md`, then gzip-archives old/oversized journals into `.archive/` (gitignored) + memory-repo git history (recoverable). Wired into the Stop hook beside `_prune`/`_consolidate` (gated by `settings.journal.auto_forget_enabled`); `/mem-forget` command + `tests/test_forget.py` (10 tests). precompact raw-dump cap **80 KB → 20 KB** (keeps the bootstrap-loaded today-journal cheap). Fixed broken `/mem-prune` (`--workspace` flag `_prune.py` rejected). One-time vault cleanup: **workspaces/ 14 MB → 1.9 MB**, `index.db` 29 MB → 3 MB, 28 raw journals archived. Grounded in `.claude/research/v3.6-brain-storage.md`. Root cause closed: a memory system that *captured but never consolidated* (79% of data was unread raw transcript).
- **v3.6 (hard rules + extraction canon)**: **`_gate.py`** — deterministic write-time quality gate enforcing canon §1 in CODE (not just docs, which the bloat proved insufficient): rejects empty/placeholder/`<20 chars`/hedged-no-evidence/`[ref]`-no-Source/`[decision]`-no-rationale/`[tool]`-no-version/secret-leak. Wired into `_topic.append_entry` + `_lesson.append_lesson` (gated by `settings.gate.enabled/strict`); `/mem-gate --scan` finds existing junk (found 18 in the live vault). New shipped canon `shared/research/extraction-reuse-2026.md` (companion to `data-quality-2026.md`): capture→extract→consolidate→forget lifecycle, the 12-rule write gate, ADD/MERGE/DEDUP/SUPERSEDE/NOOP matrix, reusable-metadata fields, and the named target architecture **Bi-Temporal Agentic Zettelkasten (B-TAZ)** (A-MEM + Zep bi-temporal + LangMem debounce + Letta agentic). Fixed broken YAML frontmatter in 3 commands (`mem-compress/mem-distill/mem-install` — mid-value `: ` mis-parsed the description = a real "skill không rõ" cause). +25 tests (234 total). Deep research: 2× Gemini + 2× Perplexity (Grok unavailable).
- **v3.6 (descriptive auto-commits)**: the memory repo's git history is now a real audit trail. **`_commitmsg.py`** generates a structured Conventional-Commits message **deterministically from the staged diff** (no LLM) — `type(scope): summary` + body (file/line counts, focus, largest edits) + grep-able trailers (`Workspace:`, `Topics:`, `Entries: +2 decision -1 ref`, `Files:`, `Machine:`, `Context:`). Types: `add/update/prune/archive/consolidate/sync` by path-bucket + tag classification. Wired into `auto-sync.commit_local` (hook path) + `_sync.py` (manual `/mem-sync`); replaces the useless `"auto-sync from <host>"`. Also fixed a latent crash: `auto-sync.py` called `log_debug` without importing it (a lock-timeout would traceback the hook). +8 tests (246 total). Grounded in deep research (Perplexity backend dcc8cb10, Gemini conv c_c23d12acdbbec4a3).

- **v3.7 (file-level validator, learned from supremor)**: studied the TrueProfit `supremor` team-knowledge vault (1295 .md, vault-keeper-validated, BOARD.md kanban, themed changelog). Its **file-level schema validator** is the standout gap gowth-mem had — `_gate.py` validated entry *content* but nothing validated file *structure*, so **121 topic files** had missing/partial/wrong frontmatter (invisible to wikilinks/recall/MOC). Adopted as **`_validate.py`** (frontmatter required-fields + naming + reserved-path per v3 type; `--fix` deterministically repairs aspect frontmatter from the path, content-preserving). Reorganized live vault: **124 aspect files fixed + MOCs rebuilt → 0 schema issues** (was 121), accounts/configs preserved, gate still 0 junk. `/mem-validate` command + 7 tests (253 total). Full comparison + the other transferable learnings (deliberate taxonomy, themed `/mem-changelog`, work-board handoff, SSOT router) in `.claude/research/v3.7-supremor-comparison.md`.
- **v3.9 (provenance & verification)**: TYPE-encoded epistemic status — VERIFIED("chắc chắn đúng")=`[ref]`(Source), UNVERIFIED("chưa verify")=new `[hypothesis]`(Verify: path, exempt from hedge gate, promotes to `[ref]` when confirmed). New `[goal]` type (user intent: `Status:` lifecycle + verifiable `Done when:`, never deleted, Motivated-by links). 7→9 types across `_gate`/`_index`/`_consolidate`/`_commitmsg`/`_forget`/`_contradict`/`_query`. `_commitmsg.py` now derives WHY/WHAT/WHEN deterministically from the diff (Why: line + When: knowledge-date + Why-Code: trailer; hypothesis→ref promotion = "verify-claim"). Canon: `shared/research/provenance-2026.md`. Grounded in Gemini+Perplexity deep research 2026-06-19.
- **v4.1 (coverage + portability)**: (A) **`_review_ledger.py` + `/mem-review-backlog`** — conversation-review coverage over `~/.claude/projects`: machine-local gitignored ledger marks reviewed/unreviewed transcripts; `--next` surfaces oldest substantive candidate (substance = ASSISTANT turns — tool-heavy autonomous sessions have few user prompts; `"type"` is not the first JSON key in live transcripts, substring-match only); Stop-hook self-review reason now appends backlog count. Closes the v4.0 gap where only live sessions at 15-turn cadence got reviewed (live: 1058 transcripts, 119 substantive unreviewed). (B) **`_setup.py` + `/mem-setup`** — one-shot machine portability: plugins.json (marketplace URLs + installed plugins), mcp.global.json (env values → `<env:NAME>` pointers), settings/CLAUDE.md/skills sanitized via `_privacy` into synced `shared/setup/` + generated `restore.sh`/`RESTORE.md` (new machine = clone vault → 1 script → 1 paste block). (C) **`_handoff.py` bullet-level rotation** — archives stale `- host:` `[done]` bullets >14d (keeps `[doing]/[blocker]/[thread]/[next]` any age); section-based rotation never fired on the real-world flat-bullet format (trade 57.7→37.3KB, devops 33.3→23.8KB, ~7.4k tokens/bootstrap saved). +25 tests (**386 total**).
- **v4.1 (retention & language policy)**: (D) **`_gate.py english_only`** (settings `gate.english_only`, default off) — curated entries must be stored in ENGLISH (>2 Vietnamese diacritics → `not_english` reject; journal stays bilingual; legacy files migrate translate-on-touch per vault `AGENTS.md` §7-LANG). (E) **`_forget.py --aspects`** — the ">3 months → archive" mechanism `topic_layout.archive_threshold_days` never had: aspects older than the threshold (default 90d, age = FILENAME date not mtime) are salvaged (`- [type]` blocks → topic `lessons.md`, deduped + provenance) then gzip-archived to `.archive/topics/`; every topic keeps its newest 3 aspects; `00-README.md`/`lessons.md` never touched; Stop-hook applies it when `topic_layout.auto_archive_enabled`. (F) **aspects born schema-conformant** — `append_entry` now calls `_validate.fix_aspect` on new files (routed writes used to create tags-only frontmatter, invisible to wikilinks/recall/MOC; 13 files had accumulated in the live vault). +13 tests (**399 total**).
- **v4.0 (metacognition)**: **deterministic auto-tagging + session self-review loop.** (A) `_tags.py` YAKE-lite (stdlib, no LLM): priority identifier harvest + scored prose (freq×position×casing, noun-phrase bigrams), UPPER≥5 emphasis demotion, EN+VI(+ascii-VI) stopwords, substring collapse, 2-slot prose reservation — typical 3-5 tags/entry. Inline `#tags` at write time (`_topic.append_entry`+`_lesson.append_lesson`) + frontmatter `tags:` union + `chunks.keywords` FTS5 column (weighted `bm25(…,5,3,1)` = boost by default) + `/mem-recall --keyword/--topic/--days` + `/mem-retag` backfill (live vault: 153/190 files tagged). Dedup hashes tag-STRIPPED content (stability proven). Data-value guards: topic auto-create denylist (kills `akia…placeholder` topics) + `validate_workspace()` before any mkdir (junk-dir bug found by dogfooding). `mem-recall.md` honesty fix (removed never-implemented 5-signal formula). (B) `_capture.py` on Stop hook: per-turn `**User:**`/`**Claude:**`/`**Actions:**` (tool-use trace) into `journal/sessions/<date>-<sid8>.md` — **thinking is ENCRYPTED in transcripts** (verified 24/24 + 681/681 across 40 files), Actions trace = honest observable proxy; opportunistic thinking extractor kept. TTL-managed; `_scores.md`/`_*` exempt from forgetting; `[self-review]` blocks salvaged. (C) every 15 turns (independent `review_count`; combined block with journal at collisions) → `templates/self-review-instructions.md`: anchored 1-5, harsh-reviewer-first, ≥2 verbatim-quoted weaknesses/dimension, quote-or-no-score, fresh-context critic subagent preferred, counterfactual gate before vault writes, `<10`-turn skip; score trend via `/mem-review --history`. +100 tests (**351 total**). Design+research: `.claude/research/v4.0-metacognition.md`.
