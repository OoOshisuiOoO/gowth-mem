---
description: Promote accumulated knowledge about a topic into wiki/topics/<Topic>.md (Obsidian page with [[wikilinks]]). Requires claude-obsidian vault. Cross-references other topics/concepts.
argument-hint: "<topic name>"
---

Invoke the `mem-promote` skill for a specified topic.

Pre-requisite: `wiki/` must exist (run `/wiki` from claude-obsidian first if not).

The skill will:

1. Verify `wiki/` exists. Abort if not, suggest `/wiki`.
2. Grep `docs/exp.md`, `docs/ref.md`, `docs/tools.md` for the topic (case-insensitive).
3. Read `wiki/index.md` to discover existing topic/concept pages.
4. Synthesize `wiki/topics/<Topic>.md` with sections:
   - `## What I know` — verified facts (cite Source from docs/ref.md)
   - `## Lessons` — episodic insights (cite from docs/exp.md)
   - `## Tools / Setup` — tool notes about the topic
   - `## Related` — `[[wikilinks]]` to relevant topic / concept pages
   - `## Sources` — list of Source URLs
5. Append to `wiki/log.md` (TOP of file): `- YYYY-MM-DD: promoted topic [[<Topic>]]`.
6. Suggest `/wiki-lint` after multiple promotions to clean cross-references.

Note: promotion is **additive**. Source entries in `docs/exp.md` / `docs/ref.md` / `docs/tools.md` remain — the topic page is a higher-layer aggregation.
