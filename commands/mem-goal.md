---
description: "Record or update a [goal] entry — the user's objective with a Status and verifiable Done-when"
---

Record the user's current objective as a `[goal]` entry in the active workspace topic. Goals are first-class memory: they give `[decision]`, `[hypothesis]`, and `[exp]` entries a reason to exist.

## When to use

- User states an intent: "I want to…", "My goal is…", "I'm trying to…"
- User updates or closes an objective: achieved, abandoned, blocked, superseded
- You want to link a decision or lesson back to the reason it was made

## Entry format

Write into the appropriate topic aspect file via `_topic.append_entry`:

```
- [goal] <title>
  Status: active
  Done when: <verifiable criterion — a test passes, a metric is hit, a file exists>
  Motivated-by: (optional — another goal this serves)
  <one-line description of the objective>
```

The gate (`_gate.py`) rejects a `[goal]` entry that is missing `Status:` or `Done when:`. Both fields are mandatory.

## Status lifecycle

| Status | Meaning |
|---|---|
| `active` | In progress |
| `paused` | Temporarily set aside |
| `achieved` | Done-when criterion is verifiably met |
| `abandoned` | Dropped — record why in a `[exp]` entry |
| `blocked` | Waiting on something external — note the blocker |
| `superseded` | Replaced by a newer goal — add `Superseded-by: [[<new-slug>]]` |

Never delete a goal. Mark it `superseded` or `abandoned` so the decision trail stays intact.

## Linking back

When writing a `[decision]`, `[hypothesis]`, or `[exp]` entry that was driven by a goal, add:

```
  Motivated-by: [[<goal-topic-slug>]]
```

This makes `/mem-recall --type decision "goal-slug"` surface all decisions made in service of that objective.

## Usage

```bash
# Append a new goal entry to the matching topic aspect
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_topic.py" \
  --ws gowth-mem --topic "v3.9-provenance" \
  "- [goal] ship provenance layer\n  Status: active\n  Done when: _gate.py enforces goal/hypothesis types + tests green\n  add verified origin tracking for every new entry type"

# Close an achieved goal (edit the entry in place)
# Change  Status: active  →  Status: achieved
# The git diff is the audit trail.
```

## Gate rules

`_gate.py` enforces (when `settings.gate.enabled: true`):

- `goal_without_status` — rejects entry with no `Status:` line
- `goal_without_criterion` — rejects entry with no `Done when:` line
- Standard hedge gate does NOT apply to goals (intent is inherently forward-looking)

## Canon

Goals are the top of the motivational hierarchy:

```
[goal] → motivates → [decision] / [hypothesis] / [exp]
[hypothesis] → when verified → promotes to [ref]
[ref] → when outdated → pruned by /mem-prune
```

This chain is the provenance layer introduced in v3.9.
