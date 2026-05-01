# gowth-mem

A Claude Code plugin for **persistent, topic-organized memory** synced across machines via your own git remote. Hooks the chat lifecycle so memory saves itself.

Built on patterns from mem0, Letta/MemGPT, Zep, Cognee, MemPalace, Generative Agents, Voyager, Reflexion, Anthropic contextual retrieval, and SM-2 spaced repetition. See [`RESEARCH.md`](RESEARCH.md).

## Why v2.0

v1.0 stored memory in `<workspace>/.gowth-mem/` — one folder per project. That's wrong for how knowledge actually works:

- **Knowledge is cross-project.** A fact learned in `AI-trade/` is useful in `bot/`. Per-workspace silos force re-discovery.
- **You think in topics, not folders.** "EMA strategy", "Claude hooks", "Bash gotchas" — single global pool indexed by topic > N project pools.
- **Multiple Claude sessions run in parallel.** A global pool exposes write races; we need locks.
- **Compact is the natural sync point.** Pulling/pushing around `/compact` keeps every machine current with no manual `/mem-sync`.
- **Conflicts shouldn't break recall.** Plain `<<<<<<<` markers in markdown break FTS5; we want Claude to read both versions, ask the user, and apply.

v2.0 = single global memory at `~/.gowth-mem/`, organized by **topic**, safe under parallel sessions, auto-syncing every compact, AI-mediated conflict resolution.

## Architecture

```
~/.gowth-mem/                   single location across all projects
├── AGENTS.md                   operating rules (synced)
├── settings.json               plugin behavior (synced)
├── config.json                 remote+token (gitignored, per-machine)
├── state.json                  SRS data (gitignored, per-machine)
├── index.db                    FTS5+vec search (gitignored, per-machine)
├── .git/                       sync target
├── .locks/                     fcntl locks (gitignored)
├── topics/                     ★ topic-organized knowledge
│   ├── _index.md               topic registry
│   └── <slug>.md               one file per topic
├── docs/                       cross-topic registries
│   ├── handoff.md              session state (host:<name> prefix per line)
│   ├── secrets.md              POINTER only (env-var names, never values)
│   └── tools.md                cross-topic tool quirks
├── journal/<date>.md           raw daily logs (synced)
└── skills/<slug>.md            Voyager workflows (synced)
```

**7-type schema** at line level inside topic files: `[exp]`, `[ref]`, `[tool]`, `[decision]`, `[reflection]`, `[skill-ref]`, `[secret-ref]`. The `recall-active.py` hook auto-skips `(superseded)` and expired `valid_until: YYYY-MM-DD` entries.

**Topic routing** (`_topic.py`): extract ≥4-char keywords, count overlap against existing `topics/*.md`, pick the slug with most overlap (≥3 common words) or create a new one from top-2 distinctive keywords.

**Cross-references**: `[[other-slug]]` wikilinks; the recall hook follows one hop on the top match.

## Install

The repo is both a standalone plugin AND a single-plugin Claude Code marketplace, so two install paths are supported.

### A. Via Claude Code plugin manager

In Claude Code:

```
/plugin marketplace add OoOshisuiOoO/gowth-mem
/plugin install gowth-mem@gowth-mem
```

Restart Claude Code so the hooks register. To update later:

```
/plugin marketplace update gowth-mem
/plugin update gowth-mem@gowth-mem
```

To uninstall:

```
/plugin uninstall gowth-mem@gowth-mem
/plugin marketplace remove gowth-mem
```

**Caveat**: Claude Code clones via SSH (`git@github.com:...`) by default. If you don't have a GitHub SSH key registered, the install fails with `Permission denied (publickey)`. Either:
- Register an SSH key at https://github.com/settings/keys, or
- Force git to rewrite SSH→HTTPS for github.com:
  ```bash
  git config --global url."https://github.com/".insteadOf git@github.com:
  ```
- Or use **method B** below (manual clone, no SSH).

If Claude Code reports `source type your Claude Code version does not support`, update Claude Code or use method B.

### B. Manual clone (recommended fallback)

```bash
git clone https://github.com/OoOshisuiOoO/gowth-mem ~/.claude/plugins/gowth-mem
```

Restart Claude Code. If your build doesn't auto-discover plugins, add to `~/.claude/settings.json`:

```json
{
  "plugins": {
    "gowth-mem": { "enabled": true }
  }
}
```

### Then run the wizard

```
/mem-install
```

It scaffolds `~/.gowth-mem/`, asks for git remote + branch + token, writes `settings.json` + `config.json`, runs the initial `_sync.py --init`. Idempotent — re-running on an already-installed system does nothing destructive.

After install:

```
memx                  build the search index (FTS5 + optional sqlite-vec)
/mem-migrate-global   import any v1.0 per-workspace .gowth-mem/ folders
```

## Hooks

| Event | Hook | What it does |
|---|---|---|
| SessionStart | `bootstrap-load.py` | Inject AGENTS + topics/_index + docs/handoff + top-3 recent topics + today/yesterday journal (12k/file, 60k total) |
| SessionStart | `auto-sync.py --pull-only --quiet` | Rebase remote into local, no push |
| SessionStart | `system-augment.py` | cwd, git, OS, datetime |
| PreCompact | `precompact-flush.py` | **HARD-BLOCK**: distill into topics before compact summarizes |
| PreCompact | `auto-sync.py --commit-only` | Commit local changes without network |
| PostCompact | `auto-sync.py --pull-rebase-push` | Full sync; on conflict writes `SYNC-CONFLICT.md` |
| UserPromptSubmit | `conflict-detect.py` | Inject reminder if `SYNC-CONFLICT.md` is pending |
| UserPromptSubmit | `recall-active.py` | Hybrid FTS5 + vector + grep recall, MMR diversity, SRS resurfacing, wikilink follow |
| UserPromptSubmit | `user-augment.py` | Keyword shortcuts (`mems`, `memb`, `memT`, `memI`, `memC`, …) + intent matching |
| Stop | `auto-journal.py` | Every 10 turns: BLOCK with auto-distill instructions + active prune |

Disable any of them by editing `~/.claude/plugins/gowth-mem/hooks/hooks.json`.

## Slash commands & shortcuts

| Command | Shortcut | Purpose |
|---|---|---|
| `/mem-install` | `memI` | First-time setup wizard |
| `/mem-config` | `memg` | Change git remote/branch/token |
| `/mem-sync` | `memy` | Manual sync (auto runs on PostCompact) |
| `/mem-sync-resolve` | `memC` | AI-mediated conflict resolution |
| `/mem-migrate-global` | `memm` | v1.0 per-workspace → v2.0 global |
| `/mem-migrate` | — | (legacy) v0.9 → v1.0 |
| `/mem-init` | — | (deprecated stub; use `/mem-install`) |
| `/mem-topic` | `memT` | List / inspect / route topics |
| `/mem-save` (skill) | `mems` | Save entry to topic + apply mem0 op |
| `/mem-distill` | `memd` | Journal → topics |
| `/mem-reflect` | `memr` | Generate reflections |
| `/mem-skillify` | `memk` | Extract reusable workflow |
| `/mem-bootstrap` | `memb` | 3-line: doing/next/blocker |
| `/mem-hyde-recall` | `memh` | HyDE retrieval for conceptual queries |
| `/mem-journal` | `memj` | Open today's journal |
| `/mem-reindex` | `memx` | Rebuild SQLite FTS5+vec index |
| `/mem-cost` | `memc` | Estimate bootstrap token footprint |
| `/mem-prune` | `memp` | Active DELETE outdated/superseded/duplicate |
| `/mem-flush` | — | Manual PreCompact reminder |
| `/mem-promote` | — | Topic → split into subdir when >1500 lines |

Shortcuts are matched at start of prompt, e.g. `mems decided to use EMA cross strategy`.

## Multi-session safety

Concurrent Claude sessions writing to `~/.gowth-mem/` are protected by:

1. **`fcntl.flock` advisory locks** under `~/.gowth-mem/.locks/`:
   - `sync.lock` serializes git operations across sessions (timeout 30s).
   - `state.lock` serializes `state.json` (SRS) updates (timeout 5s).
2. **Atomic markdown writes** via `_atomic.atomic_write` (tempfile + `os.replace`) — no half-written files.
3. **SQLite WAL mode** + `busy_timeout=5000` on `index.db` — concurrent readers don't block writes.

Hook variants of sync skip silently if the lock is held (don't fail the hook). The CLI `/mem-sync` waits up to 30s.

Windows note: `fcntl` unavailable → locks are no-ops; assume single-session use.

## Auto pull/push around compact

```
SessionStart  → auto-sync.py --pull-only         (rebase remote into local; quiet)
PreCompact    → auto-sync.py --commit-only       (snapshot before compact summarizes)
PostCompact   → auto-sync.py --pull-rebase-push  (full sync; AI conflict on collision)
```

So the typical flow:

1. You work; entries get saved into `topics/<slug>.md` via `mems` or auto-distill on Stop hook.
2. You run `/compact`. PreCompact commits local. Compact summarizes the conversation.
3. After compact, PostCompact pulls latest from remote, rebases your commit on top, pushes. Other machines see your changes on their next SessionStart.
4. If two machines committed to the same line, PostCompact writes `SYNC-CONFLICT.md` and the next prompt nudges you to run `/mem-sync-resolve`.

Toggle each step in `settings.json` under `auto_sync`.

## AI-mediated conflict resolution

When `git pull --rebase` hits a conflict, `_conflict.py` does **not** leave raw `<<<<<<<` markers in your topic files (which would corrupt FTS5 indexing). Instead:

1. Resets the working copy to the local side so files stay parseable.
2. Writes `~/.gowth-mem/SYNC-CONFLICT.md` with structured diffs per file (local / remote / common ancestor).
3. Exits with code 2.

The `conflict-detect.py` hook then reminds you on every prompt: "run `/mem-sync-resolve`". The skill walks each file, asks **keep-local | keep-remote | merge | skip | abort**, applies your choice via `atomic_write`, then `git rebase --continue` + push under `file_lock("sync")`.

You stay in control of every keep/merge decision; AI handles diff presentation, merge proposal, and mechanical git steps.

## Migration

### v1.0 per-workspace → v2.0 global

```
/mem-migrate-global
```

The skill scans `~/Git/**` (or paths you provide) for `<ws>/.gowth-mem/AGENTS.md` markers. For each found:

- `docs/{exp,ref,tools}.md` lines → routed to `~/.gowth-mem/topics/<slug>.md` via `_topic.py` (with `Source: <ws>/<file>` provenance).
- `docs/handoff.md` lines → `~/.gowth-mem/docs/handoff.md` prefixed `host:<ws>`.
- `docs/secrets.md` → dedup by env-var name.
- `journal/`, `skills/` → copied (collisions renamed `-from-<ws>`, dedup by slug).

Per-workspace `.gowth-mem/` folders are **left intact**; you remove them manually after verifying.

### v0.9 → v1.0 (legacy)

`/mem-migrate` still works for the older single-tier layout but is rarely needed.

## Recall

Each user prompt triggers `recall-active.py`:

1. Extract ≥5-char keywords (max 8).
2. **If `index.db` exists**: hybrid FTS5 BM25 + (optional) sqlite-vec, RRF-merged at k=60.
3. **Else**: grep `topics/**/*.md` and `docs/*.md` (skips `journal/`).
4. Skip lines with `(superseded)` or expired `valid_until:`.
5. Tier-score: `journal/today (100)` > `topics/* (80)` > `journal/yesterday (70)` > `docs/* (60)` > `skills/ (40)` > everything else.
6. Anthropic contextual prefix: `§ <heading> | <line>` for each match.
7. MMR diversity: skip files whose top match has Jaccard >0.6 with already-selected.
8. **Wikilink follow**: if top hit references `[[other-slug]]`, also surface that topic's top match.
9. **Spaced resurfacing**: ~25% probability/prompt, surface a file unseen ≥7 days.
10. Update `state.json` `last_seen` for surfaced paths (under `file_lock("state")`).

## Token efficiency

- **Stable prefix** (rarely changes): AGENTS.md, docs/secrets.md, docs/tools.md, topics/_index.md → cached by Anthropic at 75-90% discount.
- **Volatile suffix**: docs/handoff.md, journal/today.md, retrieved snippets → low cost anyway.
- Caps: 12k chars/file, 60k total.

## Token security

- Best: `export GOWTH_MEM_GIT_TOKEN=ghp_xxxx` in shell rc.
- OK: `config.json["token"]` (gitignored, plaintext on disk; use a fine-scoped GitHub PAT).
- Never: commit a token into a synced file.
- Secrets in topics/docs: POINTER only (env-var names + how to obtain). The `secrets.md` file is synced — never put real values there.

## Layout cooperation with claude-obsidian

If you also use [claude-obsidian](https://github.com/AgriciDaniel/claude-obsidian), it owns `<workspace>/wiki/`. The two layers don't conflict:

- gowth-mem owns `~/.gowth-mem/` (global, per-user).
- claude-obsidian owns `<workspace>/wiki/` (per-project knowledge graph).
- Both inject SessionStart context; their additionalContext blocks compose without collision.

For long-term, project-bound knowledge: `/save` (claude-obsidian) into `wiki/concepts/`. For cross-project knowledge: stays in `~/.gowth-mem/topics/`.

## Settings

`~/.gowth-mem/settings.json` (synced — change once, applies on every machine):

```json
{
  "version": "2.0",
  "auto_sync": {
    "on_session_start": true,
    "on_pre_compact": true,
    "on_post_compact": true,
    "on_stop_every_n_turns": 10
  },
  "topic_routing": {
    "min_keyword_overlap": 3,
    "default_topic": "misc"
  },
  "embedding": {
    "provider": "openai",
    "model": "text-embedding-3-small"
  },
  "recall": {
    "max_chars_per_file": 12000,
    "max_total_chars": 60000,
    "wikilink_follow": true
  },
  "conflict_resolution": { "mode": "ai-mediated" }
}
```

## Changelog (high level)

- **v2.0** — Global `~/.gowth-mem/`, topic organization, multi-session locks, AI-mediated conflict resolution, auto-sync around compact.
- **v1.0** — Centralized `<workspace>/.gowth-mem/`, git sync via user-owned remote, conflict reporting.
- **v0.9** — Strict 7-type schema, active auto-DELETE pruning.
- **v0.8** — English-only 4-char keyword shortcuts (`mems`, `memb`, …).
- **v0.7** — Auto-trigger via PreCompact block + Stop-hook auto-distill (mempalace pattern).
- **v0.6** — Hybrid FTS5 + sqlite-vec recall + HyDE.
- **v0.5** — Temporal facts, SM-2-lite SRS, token cost estimator.
- **v0.4** — Anthropic contextual prefix, MMR diversity, Voyager skills, Generative-Agents reflection, mem0 ADD/UPDATE/DELETE/NOOP.

## What this is not

- Not a sandbox.
- Not a knowledge graph engine — claude-obsidian's `wiki/` is.
- Not a system-prompt rewriter — closest mechanism is hook `additionalContext`.
- Not a Windows-first plugin — `fcntl` locks are POSIX; multi-session safety degrades there.

## License

MIT
