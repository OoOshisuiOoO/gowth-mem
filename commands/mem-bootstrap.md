---
description: Read AGENTS.md + 6 docs/* and emit a 3-line summary (đang làm gì / step kế / blocker) per AI-trade workflow rule.
---

Read the gowth-mem bootstrap files and produce a 3-line summary.

Run this with the Bash tool:

```bash
WS="${CLAUDE_PROJECT_DIR:-$PWD}"
for f in "$WS/AGENTS.md" "$WS/docs/handoff.md" "$WS/docs/exp.md" "$WS/docs/ref.md" "$WS/docs/tools.md" "$WS/docs/secrets.md" "$WS/docs/files.md"; do
  [ -f "$f" ] && echo "=== ${f#$WS/} ===" && cat "$f" && echo
done
```

Then synthesize EXACTLY 3 lines for the user:

1. **đang làm gì**: (1 line — current task from `docs/handoff.md` § Current)
2. **step kế**: (1 line — next step from `docs/handoff.md` § Next step)
3. **blocker**: (1 line — from `docs/handoff.md` § Blocker, or `không` if empty)

If `docs/handoff.md` is missing or empty, say so and suggest running `/mem-init` first.
