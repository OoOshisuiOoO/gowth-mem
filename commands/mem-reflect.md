---
description: Generative-Agents-style reflection. Reads recent journal + docs/exp.md, generates 1-3 high-level reflections (importance × recency × novelty), writes to docs/exp.md § Reflections. Promote stable reflections to wiki/concepts via /save when ready.
---

Invoke the `mem-reflect` skill to produce high-level reflections from recent activity.

When to use:
- End of week / sprint / project phase.
- After several `/mem-distill` runs have accumulated entries.
- When you sense patterns are emerging but haven't named them.

The skill will:

1. Read `docs/journal/*.md` from the last 7 days + current `docs/exp.md`.
2. Score entries by importance × recency × novelty (Generative Agents pattern).
3. Synthesize 1-3 high-level reflections (Andy Matuschak-style "evergreen" claims).
4. Append under `docs/exp.md § Reflections`.
5. Suggest `/save` (claude-obsidian) for any reflection worth promoting to `wiki/concepts/`.

## Why this beats raw distillation

`mem-distill` operates entry-by-entry. `mem-reflect` operates **across entries** to find patterns that no single entry shows. Generative Agents (Stanford) ran reflection at intervals to extract identity / relationships / plans from raw memory streams.
