---
name: mem-skillify
description: Use when a multi-step workflow has been done 2+ times in the session and looks reusable. Extract the workflow into docs/skills/<name>.md with description / steps / inputs / variations sections. Future sessions invoke the skill by short reference (Voyager pattern), saving tokens vs replaying instructions.
---

# mem-skillify

Convert a recurring workflow into a reusable skill file.

## Pre-conditions

- The workflow has been executed ≥2 times in the current session (or user confirms it's reusable).
- Steps can be generalized over inputs.

## Inputs

- `name` — short kebab-case identifier (e.g. `add-component`, `release-patch`).
- Optional `description` — one-line purpose.

## Steps

1. Ensure `docs/skills/` exists. If not, `mkdir -p` it.
2. Identify the workflow's core steps from recent session activity. Generalize variable parts (file paths, names, flags) into parameters.
3. Write `docs/skills/<name>.md` with this structure:

```markdown
---
name: <name>
description: <one line>
created: YYYY-MM-DD
inputs:
  - <param-1>: <description>
  - <param-2>: <description>
---

# <Name>

## Description

<2-3 lines: what this skill does and when to invoke it>

## Steps

1. <step 1, parameterized>
2. <step 2>
3. <step 3>

## Variations

- <variation 1, when applicable>

## Token cost

Invoking: ~50 tokens. Replaying without skill: ~<estimate> tokens.

## Source

Distilled from session: <YYYY-MM-DD>. Original runs: <list of contexts>.
```

4. Update `docs/files.md` to mention `docs/skills/` if it doesn't already.
5. Confirm path written. Suggest invocation: `do <name> for <input>` or `apply skill <name> with <inputs>`.

## Hard rules

- Skill name must be kebab-case, ≤30 chars.
- One skill per file. Don't bundle.
- Steps must be deterministic and parameterized — never hardcode file paths from the originating session.
- If skill already exists at the target path, prompt user: UPDATE or skip.
