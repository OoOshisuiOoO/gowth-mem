---
name: mem-migrate
description: Use once per workspace to migrate v0.9 layout (workspace-rooted AGENTS.md + docs/) to v1.0 centralized .gowth-mem/. Idempotent.
---

# mem-migrate

One-time migration from workspace-rooted layout (v0.9) to centralized `.gowth-mem/` layout (v1.0).

## Inputs

- Workspace root (`$CLAUDE_PROJECT_DIR` or `$PWD`).

## Steps

1. Create `.gowth-mem/` + `docs/journal/` + `docs/skills/` if missing.
2. Move workspace `AGENTS.md` → `.gowth-mem/AGENTS.md`.
3. Move workspace `docs/{handoff,exp,ref,tools,secrets,files}.md` → `.gowth-mem/docs/`.
4. Move workspace `docs/journal/*` → `.gowth-mem/docs/journal/`.
5. Move workspace `docs/skills/*` → `.gowth-mem/docs/skills/`.
6. Remove now-empty `docs/` dir at workspace root.
7. Create `.gowth-mem/settings.json` from template if missing.
8. Create `.gowth-mem/.gitignore` if missing.

## Idempotency

Safe to re-run. Each move guards against existing target — won't overwrite.

## After migration

- Run `/mem-config` to set git remote (optional, only if you want sync).
- Run `/mem-sync --init` to push initial state (only if remote configured).
- Run `memx` (`/mem-reindex`) to rebuild search index for new layout.
- Verify with `memb` (`/mem-bootstrap`) — should still print 3-line status.

## Hard rules

- Do NOT touch files outside `.gowth-mem/` and the original `AGENTS.md` + `docs/`.
- Do NOT delete project-related files (e.g. workspace `docs/architecture.md` is preserved if not in our 6-file fixed set).
- If the migration is already done (target exists), report `exists: <path>` and skip.
