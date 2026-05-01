# AGENTS.md

Operating rules — the rational layer. Hard constraints, workflow, must / never.

## Identity & role

- (one line: who is this agent, what is its primary objective)

## Hard rules (non-negotiable)

- (rule 1)
- (rule 2)

## v2.0 layout: global ~/.gowth-mem/, organized by topic

```
~/.gowth-mem/
├── AGENTS.md                  operating rules (this file, synced)
├── settings.json              plugin behavior (synced)
├── config.json                remote+token (gitignored, per-machine)
├── state.json                 SRS data (gitignored, per-machine)
├── index.db                   FTS5+vec search (gitignored, per-machine)
├── .locks/                    flock files (gitignored)
├── topics/                    ★ topic-organized knowledge
│   ├── _index.md              topic registry
│   └── <slug>.md              one file per topic
├── docs/                      cross-topic registries
│   ├── handoff.md             session state (host: prefix per line)
│   ├── secrets.md             POINTER only (env-var names, never values)
│   └── tools.md               cross-topic tool quirks
├── journal/<date>.md          raw daily logs (synced)
└── skills/<slug>.md           Voyager workflows (synced)
```

## 7-type schema (line-level prefix inside topic files)

```
- [exp]         episodic experience (debug, fix, lesson)
- [ref]         verified fact (Source: REQUIRED)
- [tool]        tool quirk specific to this topic
- [decision]    architectural choice + rationale
- [reflection]  pattern / takeaway / cluster
- [skill-ref]   pointer to skills/<slug>.md
- [secret-ref]  pointer to docs/secrets.md (env-var name)
```

## Workflow

1. **Bootstrap** (every session, automatic via SessionStart hook): read AGENTS.md + topics/_index.md + docs/handoff.md + docs/secrets.md + docs/tools.md + top-3 recently-touched topics + today/yesterday journal. Summarize 3 lines: **đang làm gì / step kế / blocker**.

2. **Throughout session**: log raw observations to `journal/<today>.md` (`memj` or `/mem-journal`). Append-only, fast, no schema enforcement.

3. **After repeating a workflow ≥2×**: `memk` / `/mem-skillify <name>` — extract reusable workflow into `skills/<name>.md` (Voyager pattern).

4. **Before `/compact`** (handled by PreCompact hook + auto-sync commit-only):
   - Distill journal/<today>.md into topic-bound entries (use `_topic.py route` to pick the slug).
   - Apply mem0 ADD/UPDATE/DELETE/NOOP. Conflict with existing → DELETE old.

5. **After `/compact`** (handled by PostCompact hook): auto-sync runs `pull-rebase-push` under `file_lock("sync")`. On conflict, writes `SYNC-CONFLICT.md` and the next prompt's UserPromptSubmit hook reminds you to run `/mem-sync-resolve`.

6. **Weekly**: `memr` / `/mem-reflect` — generate 1-3 high-level reflections from recent entries; append as `[reflection]` in the relevant topic.

7. **Research-first**: no evidence → no implementation. Save findings as `[ref]` in the matching topic, with a Source link. Conflict cũ → xóa.

8. **Tools-first**: before writing scripts, check `docs/tools.md` and the relevant topic. Tool exists → use it.

9. **Verify before claim**: no screenshot / log / test pass → no "done".

## Topic routing

Each `[exp]/[ref]/[tool]/[decision]/[reflection]` line goes into `topics/<slug>.md`. The router (`_topic.py`):

1. Extracts ≥4-char keywords from the entry, drops stopwords.
2. Counts overlap against each existing `topics/*.md`.
3. If max overlap ≥ 3 → that slug.
4. Else → new slug from top-2 distinctive keywords (kebab-case, ≤40 chars).
5. Else → `misc` (default fallback, configurable in settings.json).

Cross-topic registries (`docs/handoff.md`, `docs/secrets.md`, `docs/tools.md`) stay flat — they don't fit any single topic.

When a topic exceeds 1500 lines, `/mem-promote` splits it into `topics/<slug>/{exp,ref,tools}.md`.

## Cross-references

- Inside a topic file, reference another topic with `[[other-slug]]`.
- The recall hook follows one wikilink hop deep when a topic file is the top hit.
- Provenance line for migrated entries: `Source: <ws-name>/<original-file>`.

## Multi-session safety

- `state.json` writes use `file_lock("state")` — parallel Claude sessions queue rather than corrupt the file.
- Sync operations use `file_lock("sync")` — only one git op runs at a time across sessions.
- All markdown writes are atomic (`_atomic.atomic_write` = tempfile + os.replace).
- `index.db` opens with WAL mode + busy_timeout — concurrent readers don't block writes.

## Auto-sync flow

```
SessionStart  → auto-sync.py --pull-only        (rebase remote into local; quiet)
PreCompact    → auto-sync.py --commit-only      (snapshot before compact summarizes)
PostCompact   → auto-sync.py --pull-rebase-push (full sync; AI conflict on collision)
```

If `SYNC-CONFLICT.md` is present, every UserPromptSubmit hook reminds the user to run `/mem-sync-resolve`. The skill walks each file with the user (keep-local / keep-remote / merge / skip / abort), applies via atomic_write, then `git rebase --continue` + push under `file_lock("sync")`.

## Token efficiency (provider prompt caching)

- **Stable prefix** (rare changes): AGENTS.md, docs/secrets.md, docs/tools.md, topics/_index.md → cached by provider (75-90% discount on Anthropic).
- **Volatile suffix** (per session): docs/handoff.md, journal/<today>.md, retrieved snippets → never cached, low cost anyway.
- Bootstrap caps: 12k chars/file, 60k total.

If you find yourself editing AGENTS.md every session → batch the changes; cache misses cost real tokens.

## Temporal facts

For entries that may go stale:

```markdown
- [ref] ANTHROPIC_API_KEY format: starts with `sk-ant-` — Source: docs.anthropic.com — valid_until: 2026-12-31
- [ref] (old) Use `claude-3-opus` model — Source: ... — (superseded by claude-opus-4)
```

The `recall-active.py` hook **automatically skips** lines containing:
- `(superseded)` (case-insensitive)
- `valid_until: YYYY-MM-DD` past today

Conflict resolution: when adding a new entry that supersedes an old one → mark old as `(superseded)` (audit trail) or DELETE outright. The active prune (`_prune.py`) deletes both kinds on its next run.

## Spaced resurfacing

`recall-active.py` tracks `last_seen` per file in `state.json`. With ~25% probability per prompt, surfaces 1 file unseen ≥7 days. Counters the forgetting curve.

## Guardrails

- KHÔNG commit value thật của API key / token vào git.
- KHÔNG skip bootstrap rồi viết code "luôn cho nhanh".
- KHÔNG giữ entry mâu thuẫn — entry mới đúng → DELETE cũ (or mark `(superseded)`).
- KHÔNG promote `[ref]` không có Source.
- KHÔNG write secret value to ANY file (synced or not). docs/secrets.md is POINTER only.
- KHÔNG resolve `SYNC-CONFLICT.md` by editing markers manually — use `/mem-sync-resolve` so the AI flow stays consistent.
- Mỗi update knowledge → commit `knowledge([slug]): mô tả` (auto-handled by sync hook).
