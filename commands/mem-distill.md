---
description: Chắt lọc raw journal entries (docs/journal/<date>.md) into curated working memory (docs/exp.md, docs/ref.md, docs/tools.md). Drops noise; keeps signal. Default range: today + yesterday.
---

Invoke the `mem-distill` skill to consolidate the most recent journal entries into the curated working layer.

Default range: today + yesterday. The user can specify other dates inline (e.g. "from 2026-04-28").

The skill will:

1. Read `docs/journal/<YYYY-MM-DD>.md` for the target date(s).
2. Classify each entry by type (lesson / verified fact / tool note / secret pointer / noise).
3. Promote signal entries to the right `docs/*.md` file with dedup.
4. Mark distilled entries in the journal with a `(distilled)` suffix.
5. Report: N kept, M dropped as noise, K conflicts resolved.

Use this command at end of day or before `/compact` to keep the curated layer clean.
