---
name: mem-promote
description: Use when knowledge about a topic has accumulated across docs/exp.md / docs/ref.md / docs/tools.md and you want to consolidate into a single Obsidian topic page with cross-references via [[wikilinks]]. Requires claude-obsidian vault initialized.
---

# mem-promote

Aggregate distilled entries about a topic into a single Obsidian topic page.

## Pre-requisites

- claude-obsidian vault initialized (`wiki/` exists). Run `/wiki` first if not.
- Some accumulated entries in `docs/exp.md` / `docs/ref.md` / `docs/tools.md` mentioning the topic.

## Inputs

- Topic name (e.g. "EMA Cross", "Position Sizing", "Sierra Chart"). Case-insensitive grep.
- Workspace root (`$CLAUDE_PROJECT_DIR` or `$PWD`).

## Steps

1. **Verify vault**. If `wiki/` does not exist, ABORT and instruct user to run `/wiki`.

2. **Gather**. Grep `docs/exp.md`, `docs/ref.md`, `docs/tools.md` for the topic (case-insensitive, whole word preferred).

3. **Discover existing pages**. Read `wiki/index.md` to learn:
   - What topic / concept pages already exist
   - Naming convention (Title Case with spaces)
   - Tag taxonomy

4. **Read existing topic page if present**. `wiki/topics/<Topic>.md` may already exist — merge new content into it, don't overwrite.

5. **Synthesize**. Produce a topic page with these sections:

   ```yaml
   ---
   type: topic
   title: "<Topic Name>"
   created: YYYY-MM-DD
   updated: YYYY-MM-DD
   tags:
     - topic
     - domain/<inferred-domain>
   status: developing
   related:
     - "[[<Related Topic 1>]]"
     - "[[<Related Concept>]]"
   ---

   # <Topic Name>

   ## What I know

   - <verified fact, 1-2 lines> — Source: <URL>
   - ...

   ## Lessons

   - <episodic insight from docs/exp.md>
   - ...

   ## Tools / Setup

   - <tool note from docs/tools.md, if relevant>
   - ...

   ## Related

   - [[<Related Topic>]]
   - [[<Related Concept>]]

   ## Sources

   - <URL>
   - <URL>
   ```

6. **Write**. Save to `wiki/topics/<Topic>.md`. The folder `wiki/topics/` may need to be created.

7. **Log**. Prepend a line at the TOP of `wiki/log.md`: `- YYYY-MM-DD: promoted topic [[<Topic>]]`.

8. **Suggest follow-up**. If the user has promoted multiple topics, suggest running `/wiki-lint` to clean cross-references and detect orphans.

## Hard rules

- Use **flat YAML frontmatter only** (no nested keys) — Obsidian convention.
- Use `[[wikilinks]]` exclusively — never markdown links to `.md` files.
- Filename in Title Case with spaces (e.g. `EMA Cross.md`, not `ema-cross.md`).
- Promotion is **additive**. Do NOT delete the source entries in `docs/exp.md` / `docs/ref.md` / `docs/tools.md` — they remain working memory.
- Do NOT modify `.raw/` (claude-obsidian's immutable source folder).
- If a related page name doesn't exist yet in `wiki/index.md`, still write the `[[wikilink]]` — claude-obsidian's lint will flag it as a "wanted" link.

## When NOT to promote

- Topic has fewer than 3 entries — too thin, wait until more data.
- Entries lack Source links — promote with note "(needs verification)" or skip.
- Topic already has a stable concept page in `wiki/concepts/` — use claude-obsidian's `/save` to update that instead.
