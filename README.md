# openclaw-bridge

OpenClaw-inspired memory + bootstrap layer for Claude Code.

Splits the monolithic `CLAUDE.md` into role-specific files (the OpenClaw bootstrap pattern), wires daily episodic memory, runs a pre-compaction flush reminder, and surfaces relevant past notes on every prompt via a grep-based active recall hook.

## Why

Claude Code's `CLAUDE.md` tends to grow into one large file mixing operational rules, voice, user profile, and tool guidance. OpenClaw separates these so each axis can iterate independently and stay under a size cap. This plugin ports that pattern (no harness changes required) on top of Claude Code hooks.

What you get:

- **SessionStart hook** — assembles `AGENTS.md` + `SOUL.md` + `TOOLS.md` + `USER.md` + `MEMORY.md` + `memory/<today>.md` + `memory/<yesterday>.md` into one block, capped at 12 000 chars per file and 60 000 chars total, with truncation markers.
- **PreCompact hook** — injects a reminder to flush critical info to memory files before the model summarizes context.
- **UserPromptSubmit hook** — runs a quick grep over `memory/*.md` and surfaces up to 3 matching files as additional context (proactive recall, not on demand).
- **/claw-init command** — scaffolds the role files + `memory/` directory in the current workspace from templates.
- **/claw-flush command** — manual trigger for the flush reminder.
- **claw-memory-save skill** — instructions for Claude on how to format daily entries.
- **memory-recaller subagent** — invokable for deeper, deliberate memory search.

Inspired by [openclaw/openclaw](https://github.com/openclaw/openclaw)'s bootstrap files (`AGENTS.md`, `SOUL.md`, `TOOLS.md`, `IDENTITY.md`, `USER.md`, `MEMORY.md`), active memory plugin, and pre-compaction flush.

## Install

```bash
git clone https://github.com/<you>/openclaw-bridge ~/.claude/plugins/openclaw-bridge
```

If your Claude Code build supports plugin discovery from `~/.claude/plugins/`, restart Claude Code and the hooks load automatically. Otherwise add to `~/.claude/settings.json`:

```json
{
  "plugins": {
    "openclaw-bridge": { "enabled": true }
  }
}
```

## Bootstrap your workspace

In the project directory where you'd normally put `CLAUDE.md`:

```
/claw-init
```

Creates:

```
.
├── AGENTS.md         # operating rules (rational layer)
├── SOUL.md           # voice / tone / opinions (persona layer)
├── TOOLS.md          # tool-specific guidance
├── USER.md           # user profile (addressing, preferences)
├── MEMORY.md         # long-term curated memory
└── memory/
    └── <today>.md    # daily episodic log
```

Edit each file to taste. From this point, every session starts with the assembled bootstrap as additional context.

## How the hooks work

| Hook | Trigger | Effect |
|------|---------|--------|
| `SessionStart` | session begins | Concatenates role files + recent daily memory under a 60 000-char total cap and adds it as `additionalContext`. |
| `PreCompact` | Claude Code is about to compact context | Injects a reminder to save decisions / lessons into `memory/<today>.md`, `MEMORY.md`, or your `docs/` files. |
| `UserPromptSubmit` | every user prompt | Extracts ≥5-char keywords, greps `memory/*.md`, and surfaces up to 3 matching files (top 3 lines each) as additional context. Silent if nothing matches. |

All hooks fail silently — if your workspace has no role files or no `memory/` directory, the hook returns nothing rather than erroring.

## What this is not

- Not a SQLite or vector search index. Active recall is grep-only by design (zero deps, deterministic). For hybrid BM25 + vector see OpenClaw's builtin memory engine.
- Not a multi-agent isolation layer. Claude Code's subagent model already exists; this plugin only adds memory + bootstrap conventions.
- Not a sandbox. Claude Code's host-trust model is unchanged.

## Research notes

This plugin is a Claude-Code-native distillation of OpenClaw concepts:

- **Bootstrap injection order + size caps** → `bootstrap-load.py`
- **Pre-compaction memory flush** → `precompact-flush.py`
- **Active memory blocking sub-agent** (lite version, grep-based) → `recall-active.py` + `memory-recaller` agent
- **Daily episodic file convention** (`memory/YYYY-MM-DD.md`) → templates + skill
- **Role separation** (`AGENTS.md` / `SOUL.md` / `TOOLS.md` / `USER.md` / `MEMORY.md`) → templates + `/claw-init`

## License

MIT
