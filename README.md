# gowth-mem

A Claude Code plugin for **persistent, topic-organized memory** synced across machines via your own git remote. It hooks the chat lifecycle so memory can bootstrap, recall, journal, distill, and sync itself.

Built on patterns from mem0, Letta/MemGPT, Zep, Cognee, MemPalace, Generative Agents, Voyager, Reflexion, Anthropic contextual retrieval, and SM-2 spaced repetition. See [`RESEARCH.md`](RESEARCH.md).

## What it does

`gowth-mem` keeps a single global memory vault at `~/.gowth-mem/`, split into shared knowledge and per-workspace knowledge. Each Claude Code session resolves one active workspace, recalls relevant entries on each prompt, and syncs changes through a user-owned git remote around `/compact`.

## Current architecture

```text
~/.gowth-mem/
├── shared/                   cross-workspace knowledge
│   ├── AGENTS.md             global operating rules
│   ├── _MAP.md
│   ├── files.md
│   ├── secrets.md            pointers only; never real secret values
│   ├── tools.md
│   └── skills/<slug>.md
├── workspaces/<ws>/          active workspace-scoped knowledge
│   ├── AGENTS.md             workspace rules
│   ├── workspace.json
│   ├── _MAP.md
│   ├── docs/{handoff,exp,ref,tools,files}.md
│   ├── journal/<date>.md
│   ├── skills/<slug>.md
│   └── <slug>/<slug>.md      topic folder note
├── settings.json             synced behavior settings
├── config.json               remote/branch/token config; gitignored
├── state.json                SRS data; gitignored
├── index.db                  FTS5 + optional sqlite-vec index; gitignored
├── .locks/                   fcntl lock files; gitignored
└── .git/                     sync repository
```

Topic slugs are unique inside a workspace. `[[slug]]` resolves within the active workspace, `[[ws:slug]]` resolves cross-workspace, and `[[shared:secrets]]` resolves shared registries.

## Install

The repo is both a standalone plugin and a single-plugin Claude Code marketplace.

### Via Claude Code plugin manager

```text
/plugin marketplace add OoOshisuiOoO/gowth-mem
/plugin install gowth-mem@gowth-mem
```

Restart Claude Code so hooks register. To update later:

```text
/plugin marketplace update gowth-mem
/plugin update gowth-mem@gowth-mem
```

If Claude Code reports `source type your Claude Code version does not support`, update Claude Code or use a manual clone.

### Manual clone fallback

```bash
git clone https://github.com/OoOshisuiOoO/gowth-mem ~/.claude/plugins/gowth-mem
```

Restart Claude Code. If your build does not auto-discover plugins, enable it in `~/.claude/settings.json`.

### Then run the wizard

```text
/mem-install
```

The wizard scaffolds `shared/` plus a default workspace under `~/.gowth-mem/workspaces/default/`, asks for git remote + branch + token strategy, writes `settings.json` + `config.json`, then runs `_sync.py --init`.

After install:

```text
memx                  build the search index
/mem-migrate-global   import any older per-workspace .gowth-mem folders
```

## Self-heal user-level hook (recommended one-time setup)

Claude Code's `autoUpdate` for marketplace plugins suffers from issue #52218 — version metadata in `~/.claude/plugins/installed_plugins.json` gets bumped, but the cache dir at `~/.claude/plugins/cache/<m>/<p>/<v>/` is never materialized, so every gowth-mem hook is silently skipped after the next restart. Manual `/plugin install --path` workarounds also leak local absolute paths into the registry, breaking portability across machines.

`bin/doctor.sh` self-heals both states. Because the plugin's own hooks can't run when its `installPath` is broken, the doctor must be invoked from a hook **outside** the plugin. Add this once to `~/.claude/settings.json` (merge under any existing `hooks` key):

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "bash -c '[ -f \"$HOME/.claude/plugins/marketplaces/gowth-mem/bin/doctor.sh\" ] && bash \"$HOME/.claude/plugins/marketplaces/gowth-mem/bin/doctor.sh\" --pull --quiet || true'"
          }
        ]
      }
    ]
  }
}
```

What `--pull --quiet` does each session:
- `git fetch + ff-only pull` the marketplace clone so the doctor sees the latest published version (network errors fall back silently to the local clone).
- Detect drift: `installPath` outside `~/.claude/plugins/cache/`, missing folder, or stale version vs. marketplace.
- Materialize `~/.claude/plugins/cache/<m>/<p>/<latest>/` from the marketplace clone, atomically rewrite the registry entry, exit 0.
- Idempotent — silent when healthy. Output (heal events) goes to stderr only, so it never poisons hook stdout.

Restart Claude Code (or `/reload-plugins`) once after a heal so the new `installPath` takes effect.

## Hooks

| Event | Hook | What it does |
|---|---|---|
| SessionStart | `bootstrap-load.py` | Inject shared rules, workspace rules, docs, recent topics, and journal snippets |
| SessionStart | `auto-sync.py --pull-only --quiet` | Rebase remote into local without pushing |
| SessionStart | `system-augment.py` | Inject cwd, git, OS, host, and datetime |
| PreCompact | `precompact-flush.py` | Hard-block with distill instructions before compact |
| PreCompact | `auto-sync.py --commit-only --quiet` | Commit local memory changes without network |
| PostCompact | `auto-sync.py --pull-rebase-push --quiet` | Pull, rebase, push; conflict writes `SYNC-CONFLICT.md` |
| UserPromptSubmit | `conflict-detect.py` | Remind when sync conflict resolution is pending |
| UserPromptSubmit | `recall-active.py` | Hybrid FTS5/vector/grep recall, MMR diversity, SRS resurfacing, wikilink follow |
| UserPromptSubmit | `user-augment.py` | Inject rules and shortcut intent context |
| Stop | `auto-journal.py` | Periodic journal/distill reminder |

## Slash commands & shortcuts

| Command | Shortcut | Purpose |
|---|---|---|
| `/mem-install` | `memI` | First-time setup wizard |
| `/mem-config` | `memg` | Change git remote, branch, token strategy, or workspace map |
| `/mem-sync` | `memy` | Manual sync |
| `/mem-sync-resolve` | `memC` | AI-mediated conflict resolution |
| `/mem-migrate-global` | `memm` | Import older per-workspace `.gowth-mem/` data |
| `/mem-topic` | `memT` | List, inspect, or route topics |
| `/mem-save` | `mems` | Save entry to a topic |
| `/mem-distill` | `memd` | Journal to topics |
| `/mem-reflect` | `memr` | Generate reflections |
| `/mem-skillify` | `memk` | Extract reusable workflows |
| `/mem-bootstrap` | `memb` | Print workspace / doing / next / blocker |
| `/mem-hyde-recall` | `memh` | HyDE retrieval for conceptual queries |
| `/mem-journal` | `memj` | Open today's journal |
| `/mem-reindex` | `memx` | Rebuild SQLite FTS5 + optional vector index |
| `/mem-cost` | `memc` | Estimate bootstrap token footprint |
| `/mem-prune` | `memp` | Remove outdated, superseded, or duplicate entries |
| `/mem-lesson` | `memL` | Append a 5-field bug/lesson entry |
| `/mem-doctor` | — | Self-heal plugin install path drift (issue #52218); pulls marketplace, materializes cache, patches registry |

## Multi-session safety

Concurrent Claude sessions writing to `~/.gowth-mem/` are protected by:

1. `fcntl.flock` advisory locks under `~/.gowth-mem/.locks/`.
2. Atomic markdown writes via temp file + `os.replace`.
3. SQLite WAL mode + `busy_timeout=5000` on `index.db`.

Windows lacks `fcntl`; assume single-session use there.

## Auto pull/push around compact

```text
SessionStart  → auto-sync.py --pull-only
PreCompact    → auto-sync.py --commit-only
PostCompact   → auto-sync.py --pull-rebase-push
```

If a pull/rebase conflicts, `_conflict.py` writes `~/.gowth-mem/SYNC-CONFLICT.md` instead of leaving raw conflict markers in markdown files. The next prompt reminds you to run `/mem-sync-resolve`.

## Recall

Each user prompt triggers `recall-active.py`:

1. Scope defaults to active workspace + `shared/` unless cross-workspace recall is enabled.
2. If `index.db` exists, use hybrid FTS5 BM25 + optional sqlite-vec.
3. Otherwise grep workspace/shared markdown files.
4. Skip `(superseded)` lines and expired `valid_until:` lines.
5. Apply MMR diversity, wikilink follow, and SRS resurfacing.

## Token security

- Best: set `GOWTH_MEM_GIT_TOKEN` in your shell environment.
- Fallback: `config.json["token"]` is supported but plaintext on disk and gitignored.
- Sync keeps the git remote URL public and passes HTTPS tokens through a per-command git HTTP header.
- Never commit real token/API-key/password values into synced memory files.
- `shared/secrets.md` stores pointers only: env var names and where to obtain credentials.

## Debugging hooks

Set `GOWTH_MEM_DEBUG=1` to write hook diagnostics to `~/.gowth-mem/logs/hooks.log`. Hooks still avoid spamming normal Claude prompt output.

## Settings

`~/.gowth-mem/settings.json` controls auto-sync, active workspace behavior, topic routing, recall limits, embedding provider, and conflict resolution mode. See `templates/dot-gowth-mem/settings.example.v2.json` for the current schema.

## What this is not

- Not a sandbox.
- Not a general knowledge graph engine.
- Not a Windows-first multi-session system.
- Not a replacement for project-local docs or tests.

## License

MIT
