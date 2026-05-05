# Product Architecture — OpenClaw vs gowth-mem

How OpenClaw's architecture maps to gowth-mem's design, and where the two diverge.

## Scope Comparison

| Dimension | OpenClaw | gowth-mem |
|---|---|---|
| **What** | Full AI agent gateway + memory | Memory-only plugin for Claude Code |
| **Runtime** | Node.js daemon (TypeScript) | Python hooks (stdlib only) |
| **Agent** | Pi (embedded runtime) | Claude Code (host) |
| **Channels** | 20+ messaging apps | Claude Code terminal only |
| **Memory storage** | `~/.openclaw/workspace/` | `~/.gowth-mem/` |
| **Sync** | Local-only (no remote sync) | Git remote sync across machines |
| **License** | MIT | MIT |

gowth-mem solves a narrower problem (memory for Claude Code) but adds cross-machine sync
that OpenClaw doesn't have.

## Memory Architecture Comparison

### File Layout

| OpenClaw | gowth-mem | Notes |
|---|---|---|
| `MEMORY.md` | `shared/AGENTS.md` + topic files | OpenClaw: single file. gowth-mem: distributed topics |
| `memory/YYYY-MM-DD.md` | `workspaces/<ws>/journal/<date>.md` | Same pattern |
| `DREAMS.md` | (none) | Consolidation review surface |
| `AGENTS.md` | `shared/AGENTS.md` | Same name, same purpose |
| `SOUL.md` | (none) | Persona/tone — N/A for plugin |
| `TOOLS.md` | `shared/tools.md` + `workspaces/<ws>/docs/tools.md` | Same purpose, split by scope |
| `skills/<slug>/SKILL.md` | `shared/skills/<slug>.md` + `workspaces/<ws>/skills/` | Same pattern |

### Recall Pipeline

| Stage | OpenClaw | gowth-mem |
|---|---|---|
| **Keyword search** | FTS via SQLite | FTS5 via `_index.py` |
| **Vector search** | Embedding (auto-detect provider) | sqlite-vec via `_embed.py` (auto-detect) |
| **Hybrid fusion** | Built into memory_search | RRF at k=60 in `recall-active.py` |
| **Diversity** | (not documented) | MMR Jaccard >0.6 skip |
| **Resurfacing** | Dreaming deep-phase recall frequency | SM-2-lite SRS in `state.json` |
| **Fallback** | (no fallback documented) | grep when no index.db |

### Consolidation / Promotion

| Aspect | OpenClaw Dreaming | gowth-mem |
|---|---|---|
| **Trigger** | Cron job (default 3 AM) | Stop hook every 10 turns + PreCompact |
| **Staging** | Light phase: ingest, dedup, stage candidates | `auto-journal.py`: distill journal → topics |
| **Reflection** | REM phase: extract themes, reflection summaries | `/mem-reflect`: generate reflections |
| **Promotion** | Deep phase: 6-signal ranking → MEMORY.md | `_prune.py`: DELETE outdated, no promotion tier |
| **Scoring** | Frequency 0.24, Relevance 0.30, Diversity 0.15, Recency 0.15, Consolidation 0.10, Richness 0.06 | Tier-score: journal-today(100) > topics(80) > yesterday(70) > docs(60) > skills(40) |
| **Threshold** | minScore + minRecallCount + minUniqueQueries | (none — all entries in topics are "promoted") |
| **Human review** | DREAMS.md diary entries | (none) |

### Knowledge Structure

| Aspect | OpenClaw memory-wiki | gowth-mem |
|---|---|---|
| **Organization** | entities/, concepts/, syntheses/, sources/, reports/ | Topic slug directories `<slug>/<slug>.md` |
| **Metadata** | YAML frontmatter: claims, confidence, evidence[], provenance | Line-level `[type]` prefix: 7-type schema |
| **Contradiction** | Tracked in claims, dashboard report | (not tracked) |
| **Freshness** | Tracked per claim, stale-pages report | `valid_until:` + `(superseded)` markers |
| **Dashboards** | Auto-generated reports/ directory | (none) |
| **Cross-references** | Wiki links, entity relationships | `[[slug]]` wikilinks with 1-hop follow |

## Gaps Worth Closing

Priority-ordered by impact on gowth-mem recall quality:

1. **Staged consolidation pipeline** — OpenClaw's Light→REM→Deep gives each phase a clear
   job. gowth-mem's `auto-journal` + `_prune` conflates staging with deletion.
   Could add a `_consolidate.py` that stages candidates before promoting/pruning.

2. **Multi-signal ranking for recall** — OpenClaw uses 6 weighted signals.
   gowth-mem's tier-score is coarse. Could add frequency and query-diversity signals
   to `recall-active.py` using data already in `state.json`.

3. **Contradiction detection** — When two `[ref]` entries conflict, gowth-mem silently
   keeps both. Could add a lint pass (like OpenClaw's `wiki_lint`) that flags conflicts.

4. **Dream diary / consolidation log** — Human-readable record of what was promoted/pruned
   and why. Aids debugging when recall quality drops.

5. **Commitment type** — Short-lived follow-up entries that auto-expire.
   OpenClaw infers these from conversation; could add `[commitment]` to the 7-type schema.

## Patterns Already Aligned

These OpenClaw patterns are already implemented in gowth-mem:

- File-based memory (no hidden state)
- Daily journal auto-loaded at session start
- AGENTS.md as operating instructions
- Skills as reusable workflows
- Pre-compaction memory flush
- Hybrid BM25 + vector recall
- Auto-detect embedding provider from env vars
- Workspace-scoped knowledge separation
