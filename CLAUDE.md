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
| 7-type schema prefixes | `[decision]`, `[exp]`, `[ref]`, `[tool]`, `[reflection]`, `[skill-ref]`, `[secret-ref]` |
| Token via HTTP header per-command | `git -c http.<url>.extraHeader=AUTHORIZATION: basic <b64>` — never in remote URL |
| SM-2-lite SRS resurfacing | ~25% prob/prompt for files unseen ≥7 days; prevents knowledge rot |
| fcntl + atomic write | Multi-session safety; SQLite WAL for concurrent index access |
| Conflict → SYNC-CONFLICT.md | Raw `<<<<<<<` markers break FTS5 indexing; AI-mediated resolution instead |

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
- Tests in `tests/`. Run: `python3 -m unittest discover -s tests`.
- Compile check: `python3 -m py_compile hooks/scripts/*.py`.

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
