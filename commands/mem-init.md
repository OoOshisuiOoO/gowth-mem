---
description: Scaffold AGENTS.md + 6 docs/ working-memory files + docs/journal/ + today's journal entry, in the current workspace from gowth-mem templates.
---

Scaffold the gowth-mem working-memory files in the current working directory.

Run these steps using the Bash tool:

1. Resolve workspace: `WS="${CLAUDE_PROJECT_DIR:-$PWD}"`.
2. `mkdir -p "$WS/docs" "$WS/docs/journal"`.
3. Copy `AGENTS.md` if missing: `[ -f "$WS/AGENTS.md" ] || cp "${CLAUDE_PLUGIN_ROOT}/templates/AGENTS.md" "$WS/AGENTS.md"`.
4. For each `name` in `handoff exp ref tools secrets files`:
   - Source: `${CLAUDE_PLUGIN_ROOT}/templates/docs/<name>.md`
   - Destination: `$WS/docs/<name>.md`
   - Skip if destination exists (report `exists: docs/<name>.md`).
5. Today's journal: `TODAY=$(date +%Y-%m-%d); JOURNAL="$WS/docs/journal/$TODAY.md"`.
   - If missing: copy `${CLAUDE_PLUGIN_ROOT}/templates/journal-day.md` → `$JOURNAL`, then replace `YYYY-MM-DD` with `$TODAY` in the file.
   - Else: report `exists: docs/journal/$TODAY.md`.
6. End with `ls -la "$WS" "$WS/docs" "$WS/docs/journal"`.

Do not overwrite existing files. Report which files were created and which were skipped.

**Note**: this plugin handles **layer 1 + 2** (raw journal + curated working memory in `docs/*`). For **layer 3 + 4** (topic deep dive + atomic concepts), use claude-obsidian's `/wiki` to scaffold a `wiki/` vault — the two layers cooperate without conflict.

After scaffolding, the typical flow:

1. Throughout the day → `/mem-journal` to log raw observations.
2. End of session / before `/compact` → `/mem-distill` to chắt lọc journal → `docs/exp.md` / `ref.md` / `tools.md`.
3. When knowledge accumulates on a topic → `/mem-promote <topic>` to gom into `wiki/topics/<Topic>.md` with `[[wikilinks]]`.
