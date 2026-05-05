# Architecture Decisions

Key decisions made during gowth-mem development, with rationale and alternatives considered.

## AD-001: Global vault at ~/.gowth-mem/ (v2.0)

**Decision**: Single global directory instead of per-workspace `.gowth-mem/`.
**Rationale**: Knowledge is cross-project. A fact learned in project A is useful in project B.
Per-workspace silos forced re-discovery.
**Trade-off**: Need workspace resolver (`_workspace.py`) to scope topic routing.
**Status**: Shipped v2.0. Refined to shared/ + workspaces/<ws>/ in v2.7.

## AD-002: shared/ + workspaces/<ws>/ split (v2.7)

**Decision**: Two-level layout — `shared/` for cross-workspace knowledge, `workspaces/<ws>/` for scoped.
**Rationale**: AGENTS.md, secrets.md, tools.md are genuinely global. But handoff, journal, topics
are workspace-specific. Flat global pool mixed concerns.
**Trade-off**: More complex path resolution; `_workspace.py` resolves active workspace from `$CLAUDE_PROJECT_DIR`.
**Status**: Shipped v2.7.

## AD-003: Topic-based organization over directory taxonomy

**Decision**: Users organize knowledge by topic slug, not by type folder (docs/exp.md, docs/ref.md).
**Rationale**: Users think "EMA strategy" not "which file type is this?". Topic routing via
keyword overlap (`_topic.py`) matches natural intent.
**Trade-off**: Need routing logic; fallback `misc` topic for unclassifiable entries.
**Status**: Shipped v0.9. Refined with 7-type schema prefixes in v0.9.

## AD-004: 7-type schema with line-level prefixes

**Decision**: Every entry in a topic file must have `[type]` prefix: decision, exp, ref, tool, reflection, skill-ref, secret-ref.
**Rationale**: Adapted from MemPalace's `general_extractor.py` (5 types). Dropped `emotional`,
added `[ref]`, `[tool]`, `[secret-ref]` for our docs/ taxonomy. Enables quality gates
(e.g., `[ref]` without Source → DROP).
**Trade-off**: Strict; entries without prefix are dropped by quality gates.
**Status**: Shipped v0.9.

## AD-005: Hybrid recall with 3-tier fallback

**Decision**: BM25 (FTS5) + vector (sqlite-vec) + grep fallback.
**Rationale**: No hard dependency on embedding API. Users without API keys still get functional recall.
RRF fusion at k=60 merges BM25 and vector results when both available.
**Trade-off**: Maintaining 3 code paths; grep path has lower quality.
**Status**: Shipped v0.6.

## AD-006: Token security via HTTP header

**Decision**: Pass git token via `git -c http.<url>.extraHeader=AUTHORIZATION: basic <b64>`.
**Rationale**: Never embed token in remote URL (shows in `git remote -v`, process list, logs).
HTTP header is per-command, not persisted in git config.
**Trade-off**: Slightly more complex git_cmd construction; base64 encoding needed.
**Status**: Shipped v2.5.

## AD-007: Auto-trigger hooks over manual skill invocation

**Decision**: Stop hook auto-distills every 10 turns. PreCompact hard-blocks until flush.
UserPromptSubmit detects intent and injects skill body.
**Rationale**: Adapted from MemPalace's `mempal_save_hook.sh` pattern. Users forget to run
`/mem-distill` manually. Auto-trigger ensures memory hygiene.
**Trade-off**: Hooks add latency to every interaction; can be disabled in settings.json.
**Status**: Shipped v0.7.

## AD-008: Conflict resolution via SYNC-CONFLICT.md

**Decision**: On git conflict, reset working copy to local side, write structured conflict
data to `SYNC-CONFLICT.md`, and let AI mediate resolution interactively.
**Rationale**: Raw `<<<<<<<` markers break FTS5 indexing and make topic files unparseable.
Structured conflict file preserves both sides cleanly.
**Trade-off**: Extra file; requires `/mem-sync-resolve` workflow.
**Status**: Shipped v2.0.

## AD-009: Active DELETE over invalidate-only

**Decision**: `_prune.py` actually removes superseded/expired/duplicate entries from disk.
**Rationale**: MemPalace and Zep use invalidate-only (`valid_to`, never delete).
We DELETE because: (a) git log preserves full history, (b) stale entries bloat bootstrap
token cost, (c) users expect pruning to free space.
**Trade-off**: No undo without git; audit trail depends on git log.
**Status**: Shipped v0.9.

## AD-010: Pure stdlib Python, no pip deps

**Decision**: All hooks use only Python 3.9+ stdlib. sqlite-vec is optional C extension.
**Rationale**: Plugin installs via `git clone` into `~/.claude/plugins/`. No `pip install` step.
Users on locked-down machines can't always install packages.
**Trade-off**: Can't use requests, aiohttp, etc. HTTP done via subprocess git.
**Status**: Policy since v0.1.
