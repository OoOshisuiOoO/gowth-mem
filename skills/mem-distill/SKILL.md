---
name: mem-distill
description: Use at end of day, end of session, or before /compact to chắt lọc raw journal entries (docs/journal/<date>.md) into curated working memory (docs/exp.md, docs/ref.md, docs/tools.md). Reduces noise — only signal entries with proper format are promoted; pure chatter is dropped.
---

# mem-distill

Distill recent journal entries into the curated working layer.

## Inputs

- Optional date or date range. Default: today + yesterday.
- Workspace root (`$CLAUDE_PROJECT_DIR` or `$PWD`).

## Source files

- `docs/journal/<YYYY-MM-DD>.md` (raw observations under sections: Logs, Questions, Wins, Pains).

## Routing table

| Entry signal | Target file | Section |
|---|---|---|
| Lesson learned, fix, surprise, anti-pattern | `docs/exp.md` | `## Lessons` / `## Surprises` / `## Anti-patterns` |
| Verified external fact (HAS Source link) | `docs/ref.md` | `## API / SDK` / `## Specs / Standards` / `## Numbers / Limits` (pick best) |
| Tool usage syntax / gotcha / version | `docs/tools.md` | `## Cú pháp đã work` |
| Resource pointer (env-var name, file path) | `docs/secrets.md` | `## Env vars` or `## Files (gitignored)` |
| Pure chatter, vague feeling, unrelated | DROP (do not promote) |
| Question without answer | LEAVE in journal (don't promote — it's still open) |

## Steps

1. Determine target dates. Default: today + yesterday's journal files. Skip files that don't exist.
2. Read each journal file. Parse entries under each section.
3. For each entry:
   a. Classify by signal (use routing table).
   b. If DROP → skip.
   c. Else → format per target file's convention (1-2 lines, with Source if available).
4. **Deduplicate against existing entries in the target file**. If exact duplicate → skip. If contradicts → delete old, keep new (per AGENTS.md guardrail).
5. Append distilled entries to the right `docs/*.md` files under the right section.
6. Mark the source journal entry with `(distilled)` at end of line, OR move the entire journal file to `docs/journal/.distilled/` if user requested archival.
7. Report:
   - Entries kept: N
   - Entries dropped (noise): M
   - Entries left in journal (open questions, unverified facts): K
   - Conflicts resolved: J

## Hard rules

- Never invent facts not in the journal.
- Never write secret values into `docs/secrets.md` — only env-var name or path.
- Refuse to add to `docs/ref.md` if no Source link → promote to `docs/exp.md` instead with note "(needs source verification)".
- Conflict with existing entry → delete old, keep new.
- Do NOT delete the journal file. The journal is a permanent log.

## Cadence guidance

- Daily distill (small): end of session, takes <1 minute.
- Weekly distill (larger): catches accumulated open questions.
- Pre-compact distill (mandatory): run before `/compact` to ensure nothing critical is lost.
