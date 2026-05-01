---
description: Scaffold AGENTS.md + 6 docs/ working-memory files + docs/journal/ + docs/skills/ + today's journal entry, in the current workspace from gowth-mem templates.
---

Scaffold the gowth-mem working-memory files in the current working directory.

Run these steps using the Bash tool:

1. Resolve workspace: `WS="${CLAUDE_PROJECT_DIR:-$PWD}"`.
2. `mkdir -p "$WS/docs" "$WS/docs/journal" "$WS/docs/skills"`.
3. Copy `AGENTS.md` if missing: `[ -f "$WS/AGENTS.md" ] || cp "${CLAUDE_PLUGIN_ROOT}/templates/AGENTS.md" "$WS/AGENTS.md"`.
4. For each `name` in `handoff exp ref tools secrets files`:
   - Source: `${CLAUDE_PLUGIN_ROOT}/templates/docs/<name>.md`
   - Destination: `$WS/docs/<name>.md`
   - Skip if destination exists (report `exists: docs/<name>.md`).
5. Today's journal: `TODAY=$(date +%Y-%m-%d); JOURNAL="$WS/docs/journal/$TODAY.md"`.
   - If missing: copy `${CLAUDE_PLUGIN_ROOT}/templates/journal-day.md` → `$JOURNAL`, then replace `YYYY-MM-DD` with `$TODAY`.
6. Touch `$WS/docs/skills/.gitkeep` so the directory tracks in git when empty.
7. End with `ls -la "$WS" "$WS/docs" "$WS/docs/journal" "$WS/docs/skills"`.

Do not overwrite existing files. Report which were created and which were skipped.

**Note**: This plugin handles **layers 1, 2 + procedural skills** (raw journal + curated docs + skill library). For **layers 3 + 4** (topic deep dive + atomic concepts), use claude-obsidian's `/wiki` to scaffold a `wiki/` vault.

After scaffolding, the typical flow:

1. Throughout the day → `/mem-journal` to log raw observations.
2. End of session / before `/compact` → `/mem-distill` (uses mem0 ADD/UPDATE/DELETE/NOOP semantics).
3. When a workflow repeats ≥2× → `/mem-skillify <name>` to extract a reusable skill (Voyager pattern).
4. Periodically → `/mem-reflect` to generate high-level reflections (Generative Agents pattern).
5. When a topic accumulates → `/mem-promote <topic>` to gom into `wiki/topics/<Topic>.md`.
