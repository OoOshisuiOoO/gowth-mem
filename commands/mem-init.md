---
description: Scaffold AGENTS.md / SOUL.md / TOOLS.md / USER.md / MEMORY.md + memory/ in the current workspace from openclaw-bridge templates.
---

Scaffold the OpenClaw-style bootstrap files in the current working directory.

Run these steps using the Bash tool:

1. Resolve workspace: `WS="${CLAUDE_PROJECT_DIR:-$PWD}"`.
2. Ensure `mkdir -p "$WS/memory"`.
3. For each template under `${CLAUDE_PLUGIN_ROOT}/templates/`:
   - `AGENTS.md`, `SOUL.md`, `TOOLS.md`, `USER.md` -> copy to `$WS/<name>` if missing.
   - `memory-day.md` -> copy to `$WS/memory/$(date +%Y-%m-%d).md` if missing.
4. Touch `$WS/MEMORY.md` if missing.
5. Skip any file that already exists; report it as "exists: <path>".
6. End with `ls -la "$WS" "$WS/memory"`.

Do not overwrite existing files. Report which files were created and which were skipped.
