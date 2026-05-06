---
description: List research topics in the active workspace + their state (pending / in-progress / distilled), with raw-note count and distilled.md word count. Use after a research session to see what's next.
---

List all research topics in the active workspace and their state.

Run with the Bash tool:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_research.py" --status
```

Output format:
```
research topics in workspace 'openclaw-bridge':
  distilled     dreaming  (3 raw) [746w]
  in-progress   memory_search  (4 raw)
  pending       lint  (0 raw)
```

States:
- **pending**: directory exists but no `raw/*.md` files yet
- **in-progress**: ≥1 raw note but no `distilled.md`
- **distilled**: both raw/ and `distilled.md` exist

For per-topic quality gate, run `/mem-research-distill <topic>` or pass `--lint` directly:
```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_research.py" --lint <topic>
```
