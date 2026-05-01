---
description: Scaffold AGENTS.md + 6 docs/ working-memory files (handoff, exp, ref, tools, secrets, files) in the current workspace from gowth-mem templates.
---

Scaffold the gowth-mem working-memory files in the current working directory.

Run these steps using the Bash tool:

1. Resolve workspace: `WS="${CLAUDE_PROJECT_DIR:-$PWD}"`.
2. `mkdir -p "$WS/docs"`.
3. Copy `AGENTS.md` if missing: `[ -f "$WS/AGENTS.md" ] || cp "${CLAUDE_PLUGIN_ROOT}/templates/AGENTS.md" "$WS/AGENTS.md"`.
4. For each `name` in `handoff exp ref tools secrets files`:
   - Source: `${CLAUDE_PLUGIN_ROOT}/templates/docs/<name>.md`
   - Destination: `$WS/docs/<name>.md`
   - Skip if destination exists (report `exists: docs/<name>.md`).
5. End with `ls -la "$WS" "$WS/docs"`.

Do not overwrite existing files. Report which files were created and which were skipped.

**Note**: this plugin handles **working memory** (`docs/*`). For long-term **knowledge base** (concepts, entities, domains), use claude-obsidian's `/wiki` to scaffold a `wiki/` vault — the two layers cooperate without conflict.
