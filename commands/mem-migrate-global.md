---
description: Migrate v1.0 per-workspace .gowth-mem/ folders into the v2.0 global ~/.gowth-mem/. Routes lines into topics/<slug>.md by keyword overlap; preserves provenance.
---

Migrate v1.0 per-workspace memory into the v2.0 global pool.

Steps:

1. **Find sources**: ask user for paths (or scan `~/Git/**` two levels deep). Each candidate must contain `<ws>/.gowth-mem/AGENTS.md` (v1.0 marker).

2. **For each source workspace** `<ws>`:
   - For each line in `<ws>/.gowth-mem/docs/exp.md`, `ref.md`, `tools.md` that starts with `- [` (entry pattern):
     ```python
     import sys
     sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}/hooks/scripts")
     from _topic import route, ensure_topic
     from _atomic import atomic_write
     slug = route(line)        # picks existing or proposes new
     topic_path = ensure_topic(slug)
     # append migrated line with provenance suffix
     existing = topic_path.read_text()
     migrated = line.rstrip() + f"  (Source: {ws}/<file>)"
     atomic_write(topic_path, existing + migrated + "\n")
     ```
   - For `<ws>/.gowth-mem/docs/handoff.md` lines: append into `~/.gowth-mem/docs/handoff.md` with prefix `host:<basename(ws)>` (so multiple workspaces' handoffs coexist).
   - For `<ws>/.gowth-mem/docs/secrets.md`: append unique env-var lines (dedup by env-var name).
   - For `<ws>/.gowth-mem/journal/*.md`: copy into `~/.gowth-mem/journal/`. On collision, suffix `-from-<ws>`.
   - For `<ws>/.gowth-mem/skills/*.md`: copy into `~/.gowth-mem/skills/` (dedup by slug).

3. **Regenerate index**: `python3 ${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_topic.py --regen-index`.

4. **Optional**: rebuild search index: `python3 ${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_index.py --full`.

5. **Print summary**:
   - N topics created
   - M lines migrated
   - K skipped (duplicates already present in global pool)
   - Workspaces processed

6. **Important**: do NOT delete the per-workspace `.gowth-mem/` folders. The user removes them manually after verifying the migration.

7. **Suggest next**:
   - `memx` to embed migrated content
   - `/mem-sync` to push to remote
