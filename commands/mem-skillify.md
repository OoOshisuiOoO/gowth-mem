---
description: Extract a recurring workflow from the current session into a reusable skill file at docs/skills/<name>.md (Voyager pattern). Future sessions can invoke the skill by short reference instead of re-deriving the steps.
argument-hint: "<skill name> [description]"
---

Invoke the `mem-skillify` skill to convert a recurring workflow into a reusable skill file.

When to use:
- You've done the same multi-step workflow ≥2 times in this session (e.g. "add component → write test → update story").
- The workflow is well-defined and likely to repeat.
- You want future sessions to invoke it as one short reference instead of replaying instructions.

The skill will:

1. Ask the user (or infer from session) the skill name and description.
2. Identify the core steps from recent session activity.
3. Generalize parameters that varied across runs.
4. Write `docs/skills/<name>.md` with sections: Description / Steps / Inputs / Variations / Source.
5. Update `docs/files.md` to reference the new skill (so it's discoverable).
6. Suggest invocation pattern: `do <name> for <input>`.

## Why this saves tokens

Voyager-style skill reuse: invoking `do <name> for X` is ~50 tokens; replaying the full workflow each time is 500-2000 tokens. After 5 reuses, savings exceed 90%.
