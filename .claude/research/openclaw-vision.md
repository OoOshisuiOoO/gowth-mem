# OpenClaw — The Dream

OpenClaw is a **self-hosted AI agent gateway** (368k GitHub stars, MIT license).
One daemon connects messaging apps (WhatsApp, Telegram, Slack, Discord, Signal, iMessage, etc.)
to AI coding agents. Users message their AI assistant from anywhere while keeping control of
infrastructure and data.

Tagline: **"Any OS gateway for AI agents"**

## Why gowth-mem Studies OpenClaw

gowth-mem (`openclaw-bridge`) is named after OpenClaw because it bridges the same gap:
persistent memory for AI agents across sessions and machines. OpenClaw's memory system
(memory-core + dreaming + memory-wiki) is the most mature open-source implementation of
exactly what gowth-mem does — so we study it to adopt proven patterns and avoid reinventing.

## Core Architecture

```
Gateway (single daemon, single source of truth)
├── Channels: WhatsApp, Telegram, Slack, Discord, Signal, iMessage, WebChat, ...
├── Agent: Pi runtime (models, tools, prompt handling)
├── Sessions: isolated per-sender conversations
├── Workspace: ~/.openclaw/workspace/
│   ├── AGENTS.md      operating instructions
│   ├── SOUL.md        persona, boundaries, tone
│   ├── TOOLS.md       tool usage conventions
│   ├── MEMORY.md      long-term durable memory
│   ├── DREAMS.md      dreaming consolidation output
│   ├── memory/YYYY-MM-DD.md   daily notes
│   └── skills/<slug>/SKILL.md
├── Nodes: macOS, iOS, Android (voice, canvas, camera)
└── Control UI: browser dashboard
```

## Memory System (memory-core)

File-based, no hidden model state. Agent retains only what is written to disk.

| File | Purpose | Loaded when |
|---|---|---|
| `MEMORY.md` | Long-term durable facts, preferences, decisions | Every DM session start |
| `memory/YYYY-MM-DD.md` | Daily notes, running context | Today + yesterday auto-loaded |
| `DREAMS.md` | Dreaming summaries, dream diary | Human review surface |

### Memory Tools
- `memory_search` — hybrid semantic + keyword search
- `memory_get` — read specific file or line range

### Memory Backends
- **Builtin** — SQLite: keyword + vector + hybrid (default)
- **QMD** — local sidecar: reranking, query expansion
- **Honcho** — cross-session, user modeling, multi-agent
- **LanceDB** — auto-recall, auto-capture, local Ollama

### Auto Embedding Detection
`OPENAI_API_KEY` → OpenAI, `GEMINI_API_KEY` → Gemini, `VOYAGE_API_KEY` → Voyage, `MISTRAL_API_KEY` → Mistral

## Dreaming — Background Memory Consolidation

Opt-in cron job (default `0 3 * * *`) that decides what short-term material deserves
promotion to long-term `MEMORY.md`. Disabled by default.

### 3-Phase Sweep: Light → REM → Deep

**Light phase** — Stage & organize:
- Ingests recent daily memory signals + recall traces
- Deduplicates material
- Stages candidate lines
- May read redacted session transcripts
- Records reinforcement signals for deep ranking
- **Never writes to MEMORY.md**

**REM phase** — Reflect & pattern-extract:
- Extracts themes from recent short-term traces
- Creates reflection summaries
- Records reinforcement signals
- **Never writes to MEMORY.md**

**Deep phase** — Promote to long-term:
- Ranks candidates using 6 weighted signals
- Applies threshold gates (minScore, minRecallCount, minUniqueQueries)
- Rehydrates snippets from current daily files
- Skips stale or deleted snippets
- **Only phase that writes to MEMORY.md**

### Deep Ranking Signals

| Signal | Weight | Meaning |
|---|---:|---|
| Frequency | 0.24 | How often entry appears in short-term signals |
| Relevance | 0.30 | Average retrieval quality |
| Query diversity | 0.15 | Distinct query or day contexts |
| Recency | 0.15 | Time-decayed freshness |
| Consolidation | 0.10 | Recurrence across multiple days |
| Conceptual richness | 0.06 | Concept-tag density |

Light + REM phase hits add recency-decayed boost from `phase-signals.json`.

### Dream Diary
After each phase, background subagent appends a narrative diary entry to `DREAMS.md`.
Diary is for human review only — never used as promotion source.

### Storage Separation
- Machine state: `memory/.dreams/` (recall store, phase signals, ingestion checkpoints, locks)
- Human-readable: `DREAMS.md` + optional `memory/dreaming/<phase>/YYYY-MM-DD.md`
- Durable output: `MEMORY.md` only via deep phase promotion

## memory-wiki — Structured Knowledge Vault

Companion plugin that sits alongside memory-core. Does NOT replace it.

| Layer | Responsibility |
|---|---|
| memory-core | Recall, search, promotion, dreaming |
| memory-wiki | Wiki pages, claims, provenance, dashboards |

### Vault Structure
```
<vault>/
├── WIKI.md, index.md, inbox.md
├── entities/          people, systems, projects
├── concepts/          abstractions, patterns, policies
├── syntheses/         compiled summaries
├── sources/           imported source material
├── reports/           generated dashboards
│   ├── contradictions.md
│   ├── open-questions.md
│   ├── low-confidence.md
│   ├── stale-pages.md
│   └── claim-health.md
└── .openclaw-wiki/cache/
    ├── agent-digest.json
    └── claims.jsonl
```

### Structured Claims & Evidence
Pages have `claims` frontmatter with: id, text, status, confidence, evidence[].
Claims can be: supported, contested, stale, low-confidence, unresolved.
Evidence links back to sources with kind, weight, confidence, privacyTier.

### Wiki Tools
- `wiki_search` — provenance-aware search with modes (auto, find-person, route-question, source-evidence, raw-claim)
- `wiki_get` — read page by ID or path
- `wiki_apply` — narrow synthesis or metadata updates
- `wiki_lint` — check structure, provenance gaps, contradictions

## What gowth-mem Can Learn

| OpenClaw Pattern | gowth-mem Equivalent | Gap / Opportunity |
|---|---|---|
| 3-phase dreaming (Light→REM→Deep) | `auto-journal.py` + `_prune.py` | gowth-mem has no staged consolidation pipeline |
| 6-signal deep ranking | SM-2 SRS + tier-score | Could adopt frequency + query diversity signals |
| `MEMORY.md` as sole promotion target | Topics scattered across `<slug>/<slug>.md` | Could add a "promoted" tier for high-signal entries |
| Daily notes `YYYY-MM-DD.md` auto-loaded | `journal/<date>.md` auto-loaded | Already aligned |
| Dream diary for human review | No equivalent | Could add reflection log |
| memory-wiki structured claims | 7-type schema `[ref]` with Source | Claims have confidence + evidence — richer |
| Contradiction tracking | No equivalent | Could detect conflicting `[ref]` entries |
| Stale page detection | `valid_until:` + `(superseded)` | Already partial; could add dashboard |
| Auto embedding detection | `_embed.py` auto-detect from env | Already aligned |
| Commitments (short-lived follow-ups) | No equivalent | Could add `[commitment]` type |
| Pre-compaction memory flush | `precompact-flush.py` HARD-BLOCK | Already aligned |
