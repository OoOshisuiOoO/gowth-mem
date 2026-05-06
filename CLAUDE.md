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
| Dreaming (Light→REM→Deep consolidation) | `auto-journal.py` + `_prune.py` (simpler, gaps identified) |
| `memory_search` hybrid recall | `recall-active.py` BM25+vector+grep |
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

## Architecture (v2.7+)

```
~/.gowth-mem/
├── shared/                   cross-workspace knowledge
│   ├── AGENTS.md             global operating rules
│   ├── secrets.md            pointers only (env-var names)
│   ├── tools.md              tool quirks
│   └── skills/<slug>.md      reusable workflows
├── workspaces/<ws>/          per-workspace knowledge
│   ├── docs/{handoff,exp,ref,tools,files}.md
│   ├── journal/<date>.md
│   ├── skills/<slug>.md
│   └── <slug>/<slug>.md      topic files
├── settings.json             synced behavior config
├── config.json               local-only (gitignored): remote, branch, token
├── state.json                SRS data (gitignored)
├── index.db                  FTS5 + optional sqlite-vec (gitignored)
└── .locks/                   fcntl lock files (gitignored)
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
