# openclaw-bridge / gowth-mem

OpenClaw-inspired **working-memory layer** for Claude Code. Pairs with [claude-obsidian](https://github.com/AgriciDaniel/claude-obsidian) (knowledge base) for a 2-layer memory architecture.

## Why

Claude Code's `CLAUDE.md` tends to grow into one large file mixing operational rules, voice, user profile, and tool guidance. OpenClaw splits these into role files. This plugin distills the pattern into **6 working-memory files** under `docs/` matching the AI-trade taxonomy, plus 5 hooks for bootstrap injection / pre-compaction flush / active recall / runtime context / shortcut expansion.

## Architecture — 2 layers

```
docs/   (this plugin)              wiki/  (claude-obsidian)
─────────────────────────          ─────────────────────────
Working memory                     Knowledge base
6 fixed files, plain text          N pages, Obsidian wikilinks
Bootstrap auto-load every session  wiki/hot.md auto-load
Recent / volatile                  Durable / cross-session
```

**Promotion path**: `docs/exp.md` (recent debug) → `docs/ref.md` (verified) → `wiki/concepts/<topic>.md` (durable, cross-session) via claude-obsidian's `/save`.

## What you get

**Hooks** (5 total, registered in `hooks/hooks.json`):

- **SessionStart × 2**:
  - `bootstrap-load.py` — assembles `AGENTS.md` + `docs/handoff.md` + `docs/exp.md` + `docs/ref.md` + `docs/tools.md` + `docs/secrets.md` + `docs/files.md`. Caps: 12k char/file, 60k total. Skips blanks, marks truncations.
  - `system-augment.py` — injects runtime context: workspace path, git branch + dirty state, host, OS, current date / time / timezone, and `.claude/directives.md` if present.

- **PreCompact**:
  - `precompact-flush.py` — reminds Claude to flush critical info to the right `docs/*` file before context is compacted.

- **UserPromptSubmit × 2**:
  - `recall-active.py` — extracts ≥5-char keywords, greps `docs/*.md` and `wiki/**/*.md` (if vault exists), surfaces up to 3 matching files.
  - `user-augment.py` — expands shortcuts (`@today`, `@yesterday`, `@ws`, `@user`, `@hot`) and detects intent prefixes (review / fix / save / research / plan; English + Vietnamese).

**Slash commands**:

- `/mem-init` — scaffold `AGENTS.md` + 6 `docs/*.md` from templates.
- `/mem-flush` — manually trigger the PreCompact reminder.
- `/mem-bootstrap` — read the 6 docs/* and emit a 3-line summary: **đang làm gì / step kế / blocker**.

**Skill**:

- `mem-save` — route a memory entry to the correct `docs/*.md` file by type.

**Subagent**:

- `mem-recaller` (haiku) — deliberate memory recall across `docs/` and `wiki/`.

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

**Recommended companion**: install [claude-obsidian](https://github.com/AgriciDaniel/claude-obsidian) for the long-term knowledge layer. The two plugins cooperate — claude-obsidian's SessionStart hook auto-loads `wiki/hot.md`; this plugin's SessionStart hook auto-loads `docs/*`. No conflict.

## Bootstrap your workspace

In the project directory where you'd put `CLAUDE.md`:

```
/mem-init
```

Creates:

```
.
├── AGENTS.md          # operating rules
└── docs/
    ├── handoff.md     # session state
    ├── exp.md         # episodic experience
    ├── ref.md         # verified facts (with Source links)
    ├── tools.md       # tool registry
    ├── secrets.md     # resource pointers (env-var names; never values)
    └── files.md       # project structure map
```

For long-term knowledge:

```
/wiki    (from claude-obsidian)
```

## How the hooks work

| Hook | When | Effect |
|------|------|--------|
| `SessionStart` (bootstrap-load) | session begins / resumes | Concatenates AGENTS.md + 6 docs/* under a 60 000-char total cap. |
| `SessionStart` (system-augment) | session begins / resumes | Injects runtime: cwd, git branch + dirty, host, OS, datetime, `.claude/directives.md`. |
| `PreCompact` | before context compaction | Reminder to save critical info into the right `docs/*`. |
| `UserPromptSubmit` (recall-active) | every user prompt | Greps `docs/*.md` and `wiki/**/*.md` for keyword matches; surfaces up to 3 files. |
| `UserPromptSubmit` (user-augment) | every user prompt | Expands `@today` / `@yesterday` / `@ws` / `@user` / `@hot`; detects intent prefix (English + Vietnamese). |

All hooks fail silently — if the workspace has no `docs/` or `wiki/`, hooks return nothing rather than erroring.

## What this is not

- Not a SQLite or vector index. Active recall is grep-only by design.
- Not a knowledge base — that's claude-obsidian's job (`wiki/`, wikilinks, queries, canvas).
- Not a sandbox. Claude Code's host-trust model is unchanged.
- Not a system-prompt rewriter. Claude Code hooks cannot rewrite system / user prompt text directly; the closest mechanism is `additionalContext`.

## License

MIT
