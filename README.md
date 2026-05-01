# openclaw-bridge / gowth-mem

OpenClaw-inspired **5-tier memory pipeline** for Claude Code that mirrors human cognition. Pairs with [claude-obsidian](https://github.com/AgriciDaniel/claude-obsidian) for knowledge-graph layers.

Built on patterns from mem0, Letta/MemGPT, Zep, Cognee, Generative Agents, Voyager, Reflexion, Anthropic contextual retrieval, and SM-2 spaced repetition. See [`RESEARCH.md`](RESEARCH.md) for the full catalog.

## Why

Single-file `CLAUDE.md` mixes voice, rules, and memory. Role-based split is better but still doesn't reflect how humans consolidate knowledge: **observe → curate → organize by topic → crystallize**. This plugin gives Claude Code that pipeline plus a procedural-skill library, contextual recall, and forgetting-curve resurfacing.

## 5-tier architecture

```
Tier A — Procedural skill library          docs/skills/<name>.md
   (Voyager pattern: invoke skill ≪ replay long instructions)

Tier 1 — Raw daily journal                  docs/journal/<YYYY-MM-DD>.md
   ↓  /mem-distill   (mem0 ADD/UPDATE/DELETE/NOOP)
Tier 2 — Curated working memory             docs/exp.md  docs/ref.md  docs/tools.md
   ↓  /mem-reflect   (Generative Agents reflection)
docs/exp.md § Reflections                   (high-level patterns)
   ↓  /mem-promote <topic>
Tier 3 — Topic deep dive                    wiki/topics/<Topic>.md
   ↓  /save (claude-obsidian)
Tier 4 — Atomic concepts                    wiki/concepts/<atom>.md

Always-on (state/config):
   docs/handoff.md   docs/secrets.md   docs/files.md
```

Each tier filters noise upward. Procedural skills (Tier A) sidestep the pipeline for repeatable workflows.

## What you get

**Hooks** (6, registered in `hooks/hooks.json`):

- **SessionStart × 2**: `bootstrap-load.py` (AGENTS + 6 docs/* + 2 recent journal + skills index), `system-augment.py` (cwd, git, OS, datetime).
- **PreCompact**: `precompact-flush.py` — **HARD-BLOCKS** Claude until critical info is saved (v0.7 upgrade).
- **UserPromptSubmit × 2**: `recall-active.py`, `user-augment.py` — auto-injects inline skill instructions on intent match (save / skillify / reflect / bootstrap).
- **Stop** ✨ (v0.7): `auto-journal.py` — every 10 user turns, blocks Claude with auto-distill instructions (mempalace pattern). User never types `/mem-distill`.

**Slash commands** (9):

| Command | Purpose | Tier |
|---|---|---|
| `/mem-init` | Scaffold AGENTS + 6 docs/* + docs/journal/ + docs/skills/ | setup |
| `/mem-journal` | Open today's journal | 1 |
| `/mem-distill` | Chắt lọc journal → curated (ADD/UPDATE/DELETE/NOOP) | 1 → 2 |
| `/mem-skillify <name>` | Extract recurring workflow (Voyager) | A |
| `/mem-reflect` | High-level reflections across entries | 2 |
| `/mem-promote <topic>` | Gom → wiki/topics/<Topic>.md | 2 → 3 |
| `/mem-bootstrap` | 3-line summary: đang làm gì / step kế / blocker | summary |
| `/mem-flush` | Manual PreCompact reminder | utility |
| `/mem-cost` | Estimate bootstrap token footprint | utility |
| `/mem-reindex` ✨ | Build SQLite FTS5 + (opt) sqlite-vec index | setup |
| `/mem-hyde-recall <q>` ✨ | HyDE-pattern recall for conceptual queries | utility |

**Skills** (7): `mem-save`, `mem-distill`, `mem-skillify`, `mem-reflect`, `mem-cost`, `mem-reindex`, `mem-hyde-recall`.

**Subagent**: `mem-recaller` (haiku) deliberate recall across all tiers.

## v0.5 improvements (token efficiency + intelligence)

| Change | Source | Impact |
|---|---|---|
| **Temporal facts convention** | Zep `valid_at` pattern | recall auto-skips `(superseded)` and expired `valid_until: YYYY-MM-DD` entries — keeps stale facts from polluting context |
| **SM-2-lite spaced resurfacing** | Anki / forgetting-curve | `.gowth-mem/state.json` tracks `last_seen` per file; ~25% prob per prompt resurfaces a file unseen ≥7d. Counters the forgetting curve. |
| **Token cost estimator** `/mem-cost` | — | shows per-file char + token breakdown before `/compact`. Detects bloat early. |
| **Provider prompt caching guidance** in AGENTS.md | Anthropic cache | restructure AGENTS / SECRETS / TOOLS as stable prefix → 75-90% discount on cached portion |

## v0.4 improvements (recall quality)

| Change | Source | Impact |
|---|---|---|
| **Contextual heading prefix** | Anthropic contextual retrieval | -49 to -67% retrieval failures |
| **MMR diversity** | Carbonell & Goldstein | distinct files instead of clustered hits |
| **Skill library + auto index** | Voyager (MineDojo) | -50 to -90% on recurring workflows |
| **Generative-Agents reflection** | Stanford | 10× compression on patterns |
| **mem0 ADD/UPDATE/DELETE/NOOP** | mem0ai/mem0 | -30 to -50% file size over time |

## Install

```bash
git clone https://github.com/OoOshisuiOoO/gowth-mem ~/.claude/plugins/openclaw-bridge
```

If your Claude Code build supports plugin discovery from `~/.claude/plugins/`, restart Claude Code. Otherwise add to `~/.claude/settings.json`:

```json
{
  "plugins": {
    "openclaw-bridge": { "enabled": true }
  }
}
```

**Recommended companion**: install [claude-obsidian](https://github.com/AgriciDaniel/claude-obsidian) for tiers 3 + 4.

## Bootstrap your workspace

```
/mem-init
```

Add `.gowth-mem/` to your workspace `.gitignore` (the SRS tracker stores per-file `last_seen` here):

```
echo ".gowth-mem/" >> .gitignore
```

## Daily workflow

```
1. Throughout the day:
   /mem-journal              → append to docs/journal/<today>.md

2. Workflow repeated 2+ times:
   /mem-skillify build-bot   → extract to docs/skills/build-bot.md

3. End of session OR before /compact:
   /mem-distill              → chắt lọc journal → docs/exp/ref/tools (ADD/UPDATE/DELETE/NOOP)
   /mem-cost                 → verify bootstrap shrunk

4. Weekly:
   /mem-reflect              → 1-3 high-level reflections to docs/exp.md § Reflections

5. Topic accumulates 3+ entries:
   /mem-promote "EMA Cross"  → wiki/topics/EMA Cross.md with [[wikilinks]]

6. Topic stabilizes:
   /save (claude-obsidian)   → wiki/concepts/ (canonical)
```

## How recall works (v0.5)

Each user prompt triggers `recall-active.py`:

1. Extracts ≥5-char keywords.
2. Greps `docs/**/*.md` and `wiki/**/*.md`.
3. **Skips temporal-invalid lines**: `(superseded)` or expired `valid_until:`.
4. For each match, finds nearest preceding markdown heading → `§ heading | line`.
5. Tier-scores files: `journal/today (100)` > `journal/yesterday (80)` > `docs/* (60)` > `journal/older (50)` > `wiki/* (30)`.
6. Applies MMR-style word-overlap penalty (Jaccard >0.6 skipped).
7. **Spaced resurfacing**: with ~25% prob, appends 1 file unseen ≥7 days.
8. Updates `.gowth-mem/state.json` with `last_seen` for surfaced paths.
9. Outputs up to 3-4 distinct files (3 lines each).

## v0.7 improvements (auto-trigger — no more manual skill invocation)

Inspired by [MemPalace](https://github.com/MemPalace/mempalace)'s `mempal_save_hook.sh` (auto-mine every 15 messages) and `mempal_precompact_hook.sh` (block before compact). Adapted for the gowth-mem 4-tier markdown architecture.

| Change | Source | Impact |
|---|---|---|
| **Stop hook auto-journal** every 10 user turns | mempalace | replaces manual `/mem-distill`. Hook BLOCKS with detailed inline instructions for Claude to scan recent turns + apply mem0 ADD/UPDATE/DELETE/NOOP to docs/exp.md / ref.md / tools.md. |
| **PreCompact upgraded to BLOCK** (was advisory) | mempalace | enforces save before compact instead of just suggesting. Claude can't proceed until docs/* are flushed. |
| **UserPromptSubmit intent → inline skill body** | (this plugin) | when user types "save / lưu / nhớ" → injects mem-save body inline. "skillify / lặp lại workflow" → mem-skillify. "tổng kết / reflect" → mem-reflect. "where am I / đang làm gì" → mem-bootstrap. User no longer types `/mem-*`. |

**Net effect**: 90%+ of memory operations happen automatically. The user rarely types a `/mem-*` command — hooks catch intent + enforce discipline.

**Disable a hook** if too aggressive: edit `~/.claude/plugins/openclaw-bridge/hooks/hooks.json` and remove the offending entry, or comment out by renaming the script.

## v0.6 improvements (hybrid recall + HyDE)

| Change | Source | Impact |
|---|---|---|
| **SQLite FTS5 + sqlite-vec hybrid recall** | Anthropic contextual retrieval / Cognee triple-store | RRF fusion of BM25 + vector. Graceful 3-tier fallback: vector → FTS5 → grep. Opt-in via `/mem-reindex`. |
| **Auto embedding-provider detection** | — | `OPENAI_API_KEY` / `VOYAGE_API_KEY` / `GEMINI_API_KEY` env var; uses `text-embedding-3-small` / `voyage-multilingual-2` / `gemini-embedding-001` accordingly. Stdlib `urllib`, no `openai` / `voyageai` packages required. |
| **HyDE-lite** `/mem-hyde-recall` | Gao et al. 2022 (HyDE) | Opt-in deliberate command for conceptual queries. Drafts hypothetical answer, retrieves via index/grep, synthesizes against original question. |
| **mtime tiebreak in tier sort** | — | When tier scores tie, newer files surface first. Fixes index-path edge case where boilerplate-heavy files outrank the actual relevant entry. |

## Roadmap

**Shipped through v0.6**: 4-tier pipeline + skill library + reflection + contextual recall + MMR + temporal facts + SM-2-lite SRS + token cost + prompt caching guidance + FTS5/vector hybrid + HyDE-lite.

**Skipped** (limited ROI for our use case):
- GPTCache semantic response cache — stale risk for evolving code.

**Out of scope** (custom-model territory, see `RESEARCH.md` Tier 4):
- LongLLMLingua / AutoCompressor / gist tokens — finetuned models needed.
- ColBERT / ColPali — overkill for markdown vault.
- RAPTOR / GraphRAG / HippoRAG — defer to claude-obsidian's wiki-fold.

## What this is not

- Not a SQLite or vector index (yet).
- Not a knowledge graph engine — that's Obsidian's job.
- Not a sandbox.
- Not a system-prompt rewriter — closest mechanism is `additionalContext`.

## License

MIT
