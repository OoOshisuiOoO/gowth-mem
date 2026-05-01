---
description: Manually trigger the openclaw-bridge pre-compaction flush reminder. Use before /compact when context is heavy.
---

Manually print the openclaw-bridge flush reminder and act on it before context is compacted.

Run with the Bash tool:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/precompact-flush.py" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["hookSpecificOutput"]["additionalContext"])'
```

Then:

1. Review the reminder.
2. Save outstanding decisions / lessons / verified facts to the appropriate file:
   - `memory/<today>.md` for episodic items
   - `MEMORY.md` for durable facts / preferences
   - `docs/ref.md` (only if it exists) for verified external references
   - `docs/exp.md` (only if it exists) for debug / fix experiences
3. Confirm what was written and where.
