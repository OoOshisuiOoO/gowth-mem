---
description: Generative-Agents-style reflection over recent vault journal + docs/exp.md. Generates 1-3 high-level reflections (importance × recency × novelty) and appends them under docs/exp.md § Reflections.
---

Invoke the `mem-reflect` skill to produce high-level reflections from recent activity.

When to use:
- End of week / sprint / project phase.
- After several `/mem-distill` runs have accumulated entries.
- When you sense patterns are emerging but haven't named them.

The skill will:

1. Read `~/.gowth-mem/workspaces/<ws>/journal/*.md` from the last 7 days + current `~/.gowth-mem/workspaces/<ws>/docs/exp.md`.
2. Score entries by importance × recency × novelty (Generative Agents pattern).
3. Synthesize 1-3 high-level reflections (Andy Matuschak-style "evergreen" claims).
4. Append under `~/.gowth-mem/workspaces/<ws>/docs/exp.md § Reflections`.
5. Suggest `/save` (claude-obsidian) for any reflection worth promoting to `wiki/concepts/`.

## Why this beats raw distillation

`mem-distill` operates entry-by-entry. `mem-reflect` operates **across entries** to find patterns that no single entry shows. Generative Agents (Stanford) ran reflection at intervals to extract identity / relationships / plans from raw memory streams.
