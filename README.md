# openclaw-bridge / gowth-mem

OpenClaw-inspired **5-tier memory pipeline** for Claude Code that mirrors human cognition. Pairs with [claude-obsidian](https://github.com/AgriciDaniel/claude-obsidian) for knowledge-graph layers.

Built on patterns from mem0, Letta/MemGPT, Zep, Cognee, Generative Agents, Voyager, Reflexion. See [`RESEARCH.md`](RESEARCH.md) for the full catalog.

## Why

Single-file `CLAUDE.md` mixes voice, rules, and memory. Role-based split is better but still doesn't reflect how humans actually consolidate knowledge: **observe → curate → organize by topic → crystallize**. This plugin gives Claude Code that pipeline plus a procedural-skill library so recurring workflows don't re-burn tokens.

## 5-tier architecture

```
Tier A — Procedural (skill library)        docs/skills/<name>.md
   (Voyager pattern: invoke skill ≪ replay long instructions)

Tier 1 — Raw daily journal                  docs/journal/<YYYY-MM-DD>.md
   ↓  /mem-distill   (mem0 ADD/UPDATE/DELETE/NOOP semantics)
Tier 2 — Curated working memory             docs/exp.md  docs/ref.md  docs/tools.md
   ↓  /mem-reflect   (Generative Agents reflection: importance × recency × novelty)
docs/exp.md § Reflections                   (high-level patterns across many entries)
   ↓  /mem-promote <topic>   (gom theo chủ đề)
Tier 3 — Topic deep dive                    wiki/topics/<Topic>.md   (claude-obsidian, [[wikilinks]])
   ↓  /save                                 (claude-obsidian, when stable)
Tier 4 — Atomic concepts                    wiki/concepts/<atom>.md  (Zettelkasten)

Always-on (state/config, not pipeline):
   docs/handoff.md   docs/secrets.md   docs/files.md
```

| Tier | Role | Owner | Token signature |
|---|---|---|---|
| A | Procedural skill library (Voyager) | gowth-mem | invoke ≪ replay; saves 50-90% on recurring workflows |
| 1 | Raw daily journal | gowth-mem | append-only, low filter |
| 2 | Curated working memory | gowth-mem | mem0-style rewrite keeps files lean |
| 3 | Topic deep dive (`[[wikilinks]]`) | claude-obsidian | on-demand via `/wiki-query` |
| 4 | Atomic concepts | claude-obsidian | on-demand |

Each tier **filters noise upward**: pure chatter dies in Tier 1, unverified claims stay in Tier 2, only stabilized topics reach Tier 3. Procedural skills (Tier A) sidestep the pipeline entirely for repeatable workflows.

## What you get

**5 hooks** registered in `hooks/hooks.json`:

- **SessionStart × 2** — `bootstrap-load.py` (AGENTS + 6 docs/* + 2 recent journal + skills index), `system-augment.py` (cwd, git, OS, datetime, `.claude/directives.md`).
- **PreCompact** — `precompact-flush.py` (route reminder by type into right `docs/*`).
- **UserPromptSubmit × 2** — `recall-active.py` (**v0.4: contextual heading prefix + MMR diversity**, scans `docs/**/*.md` and `wiki/**/*.md`), `user-augment.py` (`@today`, `@yesterday`, `@ws`, `@user`, `@hot` + intent EN/VN).

**Slash commands** (8 total):

| Command | Purpose | Tier |
|---|---|---|
| `/mem-init` | Scaffold AGENTS + 6 docs/* + docs/journal/ + docs/skills/ | setup |
| `/mem-journal` | Open today's journal entry | 1 |
| `/mem-distill` | Chắt lọc journal → curated (mem0 ADD/UPDATE/DELETE/NOOP) | 1 → 2 |
| `/mem-skillify <name>` | Extract recurring workflow → `docs/skills/<name>.md` (Voyager) | A |
| `/mem-reflect` | Generative-Agents reflection: pattern across entries | 2 |
| `/mem-promote <topic>` | Gom → `wiki/topics/<Topic>.md` with `[[wikilinks]]` | 2 → 3 |
| `/mem-bootstrap` | 3-line summary: đang làm gì / step kế / blocker | summary |
| `/mem-flush` | Manual PreCompact reminder | utility |

**Skills** (4 total) — `mem-save`, `mem-distill`, `mem-skillify`, `mem-reflect`, `mem-promote`.

**Subagent** — `mem-recaller` (haiku) deliberate recall across all tiers.

## v0.4 improvements (token efficiency + smartness)

Based on research in `RESEARCH.md`:

| Change | Source | Token impact |
|---|---|---|
| **mem0 ADD/UPDATE/DELETE/NOOP** in mem-distill | mem0ai/mem0 | -30 to -50% on target file size over time (no contradiction bloat) |
| **Contextual retrieval** (§ heading prefix) in recall | Anthropic contextual retrieval | +50% precision on relevant snippets; -49 to -67% retrieval failures |
| **MMR diversity** in recall | Carbonell & Goldstein 1998 | distinct files instead of clustered hits; same token, more info |
| **Skill library** convention + auto-loaded index | MineDojo/Voyager | -50 to -90% on repeated workflows after 5 reuses |
| **Generative-Agents reflection** | joonspk-research/generative_agents | -10× compression on patterns vs replaying journal |

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

**Recommended companion**: install [claude-obsidian](https://github.com/AgriciDaniel/claude-obsidian) for tiers 3 + 4. Their SessionStart hook auto-loads `wiki/hot.md`; ours auto-loads `docs/*` + recent journal + skills index. No conflict.

## Bootstrap your workspace

```
/mem-init
```

Creates:

```
.
├── AGENTS.md              # operating rules + 5-tier pipeline doc
└── docs/
    ├── handoff.md         # session state (always-on)
    ├── exp.md             # curated episodic + § Reflections (tier 2)
    ├── ref.md             # verified facts (tier 2)
    ├── tools.md           # tool registry (tier 2)
    ├── secrets.md         # resource pointers (always-on)
    ├── files.md           # project structure (always-on)
    ├── journal/
    │   └── 2026-05-02.md  # today's raw journal (tier 1)
    └── skills/            # tier A — Voyager skill library
        └── .gitkeep
```

For tiers 3 + 4:

```
/wiki   (from claude-obsidian)
```

## Daily workflow

```
1. Throughout the day:
   /mem-journal              → append raw observations to docs/journal/<today>.md

2. After repeating a workflow ≥2×:
   /mem-skillify build-bot   → extract reusable skill to docs/skills/build-bot.md
                               (next time invoke with: "do build-bot for X")

3. End of session OR before /compact:
   /mem-distill              → chắt lọc journal → docs/exp.md / ref.md / tools.md
                               using mem0 ADD/UPDATE/DELETE/NOOP rewrite logic

4. Weekly / end-of-sprint:
   /mem-reflect              → generate 1-3 high-level reflections (importance × recency × novelty)
                               appended to docs/exp.md § Reflections

5. When a topic accumulates (3+ entries):
   /mem-promote "EMA Cross"  → creates wiki/topics/EMA Cross.md with [[wikilinks]]

6. When a topic stabilizes:
   /save (claude-obsidian)   → promote to wiki/concepts/ (canonical, atomic)
```

## How recall works (v0.4)

Each user prompt triggers `recall-active.py`. It:

1. Extracts ≥5-char keywords from the prompt.
2. Greps `docs/**/*.md` and `wiki/**/*.md` for matches.
3. For each matched line, finds the **nearest preceding markdown heading** (`§ heading | line` — Anthropic contextual retrieval pattern).
4. Scores files by tier: `journal/today (100)` > `journal/yesterday (80)` > `docs/* (60)` > `journal/older (50)` > `wiki/* (30)`.
5. Applies **MMR-style word-overlap penalty**: skip files that are >60% Jaccard-similar to already-selected files.
6. Returns up to 3 distinct files (3 lines each) as `additionalContext`.

Result: relevant snippets with structural context, no near-duplicate noise.

## What this is not

- Not a SQLite or vector index (yet — see `RESEARCH.md` § Tier 2 roadmap).
- Not a knowledge graph engine — that's Obsidian's job.
- Not a sandbox.
- Not a system-prompt rewriter — closest mechanism is `additionalContext`.

## License

MIT
