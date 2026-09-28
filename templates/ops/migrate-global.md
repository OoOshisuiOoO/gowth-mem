---
description: Migrate v1.0 per-workspace .gowth-mem/ folders into the v3.0 global ~/.gowth-mem/. Routes lines into workspaces/<ws>/<slug>/YYYY-MM-DD-<aspect>.md by keyword overlap; preserves provenance.
---

Migrate v1.0 per-workspace memory into the current v3.0 `shared/` + `workspaces/<ws>/<slug>/{00-README.md, YYYY-MM-DD-<aspect>.md, lessons.md}` layout.

Steps:

1. **Find sources**: ask user for paths (or scan `~/Git/**` two levels deep). Each candidate must contain `<ws>/.gowth-mem/AGENTS.md` (v1.0 marker).

2. **For each source workspace** `<ws>`:
   - Create a target workspace via `_workspace.py create <basename(ws)>` if it doesn't exist (creates the v3 scaffold: `docs/`, `journal/`, `skills/`, `research/`, `misc/00-README.md`).
   - For each line in `<ws>/.gowth-mem/docs/exp.md`, `ref.md`, `tools.md` that starts with `- [` (entry pattern):
     ```python
     import sys
     sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}/hooks/scripts")
     from _topic import route
     from _atomic import atomic_write
     # route() v3 returns the dated aspect file path:
     #   workspaces/<ws>/<slug>/YYYY-MM-DD-<aspect>.md
     # It auto-creates the topic folder + 00-README.md if missing (idempotent).
     # [secret-ref] / [skill-ref] lines side-channel to shared/secrets.md /
     # workspaces/<ws>/skills/<slug>.md respectively.
     aspect_path = route(line, ws=target_ws)
     existing = aspect_path.read_text() if aspect_path.is_file() else ""
     migrated = line.rstrip() + f"  (Source: {ws}/<file>)"
     atomic_write(aspect_path, existing + migrated + "\n")
     ```
   - For `<ws>/.gowth-mem/docs/handoff.md` lines: append into `~/.gowth-mem/workspaces/<target>/docs/handoff.md`.
   - For `<ws>/.gowth-mem/docs/secrets.md`: append unique env-var lines into `~/.gowth-mem/shared/secrets.md` (dedup by env-var name).
   - For `<ws>/.gowth-mem/journal/*.md`: copy into `~/.gowth-mem/workspaces/<target>/journal/`. On collision, suffix `-from-<ws>`.
   - For `<ws>/.gowth-mem/skills/*.md`: copy into `~/.gowth-mem/workspaces/<target>/skills/` (dedup by slug).

3. **Regenerate MOCs**: `python3 ${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_moc.py --ws <target>` (refreshes workspace MOC + every topic `00-README.md`).

4. **Optional**: rebuild search index: `python3 ${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_index.py --full`.

5. **Print summary**:
   - N topics created
   - M lines migrated
   - K skipped (duplicates already present)
   - Workspaces processed

6. **Important**: do NOT delete the per-workspace `.gowth-mem/` folders. The user removes them manually after verifying the migration.

7. **Suggest next**:
   - `memx` to embed migrated content
   - `/mem-sync` to push to remote
