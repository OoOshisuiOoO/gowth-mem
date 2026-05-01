---
name: claw-memory-save
description: Use when the user says "save this", "remember this", "note this", or after a debug session ends, to format and append a memory entry into memory/<today>.md or MEMORY.md following the openclaw-bridge convention.
---

# claw-memory-save

Save a memory entry into the right file with the right structure.

## File routing

| Type of info | File |
|---|---|
| Today's decisions, lessons, surprises | `memory/<YYYY-MM-DD>.md` |
| Long-term fact, preference, durable rule | `MEMORY.md` |
| Verified external reference | `docs/ref.md` (only if it exists) |
| Debug / fix experience | `docs/exp.md` (only if it exists) |

## Entry format

Each entry is 1–2 lines. No noise. Include source / link when relevant.

```markdown
- <fact / decision / lesson> — <why or context, ≤1 line>
  Source: <URL | file:line | session date>
```

## Steps

1. Read the existing target file. If today's daily file is missing, copy from `${CLAUDE_PLUGIN_ROOT}/templates/memory-day.md`.
2. Append under the correct section (`### Decisions`, `### Lessons / surprises`, `### References`, `### Open threads`).
3. If a previous entry contradicts the new one, delete the old entry — never keep both.
4. Confirm the file path written.
