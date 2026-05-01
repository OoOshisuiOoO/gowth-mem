# openclaw-bridge / gowth-mem

OpenClaw-inspired **4-layer memory pipeline** for Claude Code that mirrors human cognition. Pairs with [claude-obsidian](https://github.com/AgriciDaniel/claude-obsidian) for layers 3 & 4.

## Why

Claude Code's `CLAUDE.md` jams operating rules, voice, and memory into one file. OpenClaw splits role files. But role-based isn't enough — humans organize knowledge **by topic** with cross-references, and they **distill** raw observations into structured memory over time.

This plugin gives Claude Code that pipeline.

## 4-layer architecture

```
Layer 1 — Raw daily journal           docs/journal/<YYYY-MM-DD>.md
   ↓  /mem-distill   (chắt lọc, drop noise)
Layer 2 — Curated working memory       docs/exp.md  docs/ref.md  docs/tools.md
   ↓  /mem-promote <topic>   (gom theo chủ đề)
Layer 3 — Topic deep dive              wiki/topics/<Topic>.md   (claude-obsidian)
   ↓  /save   (claude-obsidian, when stable)
Layer 4 — Atomic concepts              wiki/concepts/<atom>.md  (claude-obsidian)

Always-on (state/config, not pipeline):
   docs/handoff.md   docs/secrets.md   docs/files.md
```

| Layer | Role | Owner | When read |
|---|---|---|---|
| 1 | Raw daily journal | gowth-mem | bootstrap (today + yesterday) |
| 2 | Curated working memory | gowth-mem | bootstrap (always) |
| 3 | Topic deep dive (with `[[wikilinks]]`) | claude-obsidian | on demand via `/wiki-query` |
| 4 | Atomic concepts | claude-obsidian | on demand via `/wiki-query` |

**How it mirrors human memory**:
- Layer 1 = sensory / working memory (today's everything)
- Layer 2 = short-term memory (this week's distilled lessons)
- Layer 3 = topic-organized long-term memory (how X relates to Y)
- Layer 4 = crystallized concepts (atomic facts that don't change)

Each layer **filters noise upward** — pure chatter dies in layer 1; unverified claims stay in layer 2; only stabilized topics reach layer 3.

## What you get

**Hooks** (5 in `hooks/hooks.json`):

- **SessionStart × 2** — bootstrap-load (AGENTS.md + 6 docs/* + 2 recent journal files), system-augment (cwd, git, OS, datetime, `.claude/directives.md`).
- **PreCompact** — precompact-flush (route reminder by type into right `docs/*`).
- **UserPromptSubmit × 2** — recall-active (grep `docs/**/*.md` and `wiki/**/*.md`, surface 3 matches), user-augment (`@today`, `@yesterday`, `@ws`, `@user`, `@hot` + intent prefix EN/VN).

**Slash commands**:

| Command | Purpose | Layer |
|---|---|---|
| `/mem-init` | Scaffold AGENTS.md + 6 docs/* + docs/journal/ + today's journal | setup |
| `/mem-journal` | Open today's journal (creates from template if missing) | 1 |
| `/mem-distill` | Promote signal entries from journal → exp / ref / tools | 1 → 2 |
| `/mem-promote <topic>` | Aggregate accumulated entries → `wiki/topics/<Topic>.md` | 2 → 3 |
| `/mem-bootstrap` | Read all bootstrap files, emit 3-line summary (đang làm gì / step kế / blocker) | summary |
| `/mem-flush` | Manually trigger PreCompact reminder | utility |

**Skills**:

- `mem-save` — route a single entry to the correct `docs/*.md` by type
- `mem-distill` — chắt lọc journal → curated layer 2
- `mem-promote` — gom layer 2 entries → layer 3 topic page with `[[wikilinks]]`

**Subagent**:

- `mem-recaller` (haiku) — deliberate recall across all 4 layers

## Install

```bash
git clone https://github.com/OoOshisuiOoO/gowth-mem ~/.claude/plugins/openclaw-bridge
```

If your Claude Code build supports plugin discovery from `~/.claude/plugins/`, restart Claude Code and the hooks load automatically. Otherwise add to `~/.claude/settings.json`:

```json
{
  "plugins": {
    "openclaw-bridge": { "enabled": true }
  }
}
```

**Recommended companion**: install [claude-obsidian](https://github.com/AgriciDaniel/claude-obsidian) for layers 3 + 4. The two plugins cooperate — claude-obsidian's SessionStart hook auto-loads `wiki/hot.md`; this plugin's SessionStart hook auto-loads `docs/*` + recent journal. No conflict.

## Bootstrap your workspace

```
/mem-init
```

Creates:

```
.
├── AGENTS.md              # operating rules + 4-layer pipeline doc
└── docs/
    ├── handoff.md         # session state (always-on)
    ├── exp.md             # curated episodic (layer 2)
    ├── ref.md             # verified facts (layer 2)
    ├── tools.md           # tool registry (layer 2)
    ├── secrets.md         # resource pointers (always-on, env-var names only)
    ├── files.md           # project structure (always-on)
    └── journal/
        └── 2026-05-02.md  # today's raw journal (layer 1)
```

For layers 3 + 4:

```
/wiki   (from claude-obsidian)
```

## Daily workflow

```
1. Throughout the day:
   /mem-journal               → append raw observations to docs/journal/<today>.md

2. End of session OR before /compact:
   /mem-distill               → chắt lọc journal entries to docs/exp.md / ref.md / tools.md
                                (drops pure chatter; keeps signal)

3. When a topic accumulates (3+ entries about same subject):
   /mem-promote "EMA Cross"   → creates wiki/topics/EMA Cross.md
                                with [[wikilinks]] to related topics/concepts

4. When a topic stabilizes:
   /save (claude-obsidian)    → promote to wiki/concepts/ (atomic, canonical)
```

## What this is not

- Not a SQLite or vector index. Active recall is grep-only by design.
- Not a knowledge graph engine — that's Obsidian's job (wikilinks, graph view, queries).
- Not a sandbox. Claude Code's host-trust model is unchanged.
- Not a system-prompt rewriter. Claude Code hooks cannot rewrite system / user prompt text directly — closest mechanism is `additionalContext`.

## License

MIT
