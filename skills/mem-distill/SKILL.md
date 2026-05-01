---
name: mem-distill
description: Use at end of day, end of session, or before /compact to chắt lọc raw journal entries (docs/journal/<date>.md) into curated working memory (docs/exp.md, docs/ref.md, docs/tools.md). Uses mem0-style ADD/UPDATE/DELETE/NOOP rewrite logic to keep target files lean. Drops noise.
---

# mem-distill

Distill recent journal entries into the curated working layer using mem0-style write semantics.

## Inputs

- Optional date or date range. Default: today + yesterday.
- Workspace root (`$CLAUDE_PROJECT_DIR` or `$PWD`).

## Source files

- `docs/journal/<YYYY-MM-DD>.md` (raw observations under sections: Logs, Questions, Wins, Pains).

## Routing table

| Entry signal | Target file | Section |
|---|---|---|
| Lesson learned, fix, surprise, anti-pattern | `docs/exp.md` | `## Lessons` / `## Surprises` / `## Anti-patterns` |
| Verified external fact (HAS Source link) | `docs/ref.md` | `## API / SDK` / `## Specs / Standards` / `## Numbers / Limits` |
| Tool usage syntax / gotcha / version | `docs/tools.md` | `## Cú pháp đã work` |
| Resource pointer (env-var name, file path) | `docs/secrets.md` | `## Env vars` or `## Files (gitignored)` |
| Pure chatter, vague feeling, unrelated | DROP (do not promote) |
| Question without answer | LEAVE in journal (don't promote — still open) |

## Write semantics — mem0 ADD/UPDATE/DELETE/NOOP (CRITICAL)

For each candidate entry, before writing, decide one of four actions:

| Action | When | What to do |
|---|---|---|
| **ADD** | No similar existing entry in target | Append new entry under correct section |
| **UPDATE** | Existing entry with same subject but new info | Replace the old entry's content; keep one entry per fact |
| **DELETE** | New entry contradicts an old one (the new is correct) | Remove the old entry, then ADD the new one |
| **NOOP** | Exact duplicate or strict subset of an existing entry | Skip; do nothing |

This avoids endless append; target files stay lean and contradiction-free over time.

## Steps

1. Determine target date range (default today + yesterday).
2. For each journal file in range:
   a. Read the file. Parse entries under each section header.
   b. For each entry: classify by signal (use routing table).
   c. If DROP → skip.
   d. Else format per target convention (1-2 lines, with Source if available).
3. For each formatted candidate:
   a. Read the target `docs/<name>.md` file.
   b. **Decide ADD / UPDATE / DELETE / NOOP** by comparing the candidate against existing entries (string match for keys + LLM judgement for semantic conflict).
   c. Apply the action.
4. Mark each promoted source journal entry with `(distilled)` suffix.
5. Report:
   - ADD: N
   - UPDATE: M
   - DELETE: K (old entries removed)
   - NOOP: J (already covered)
   - DROP: D (noise removed before promotion)
   - LEFT IN JOURNAL: L (open questions, unverified facts)

## Hard rules

- Never invent facts not in the journal.
- Never write secret values into `docs/secrets.md` — only env-var name or path.
- Refuse to add to `docs/ref.md` if no Source link → promote to `docs/exp.md` instead with note `(needs source verification)`.
- Conflict → DELETE old, ADD new. Never leave both.
- Do NOT delete the journal file itself (the journal is a permanent log).

## Cadence guidance

- Daily distill (small): end of session, takes <1 minute.
- Weekly distill (larger): catches accumulated open questions; triggers `/mem-reflect` afterward.
- Pre-compact distill (mandatory): run before `/compact` to ensure nothing critical is lost.
