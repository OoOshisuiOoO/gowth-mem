---
name: mem-distill
description: Use at end of day, end of session, or before /compact to chắt lọc raw journal entries into curated docs/exp.md / ref.md / tools.md / secrets.md. Strict schema with [type] prefixes, mempalace-inspired noise rejection, mem0 ADD/UPDATE/DELETE/NOOP semantics.
---

# mem-distill

Distill journal entries into the curated working layer using strict schema + noise rejection.

## Inputs

- Optional date or date range. Default: today + yesterday.
- Workspace root (`$CLAUDE_PROJECT_DIR` or `$PWD`).

## Strict schema (mempalace-inspired 5-type + 2 plugin-specific)

Every promoted entry MUST start with a `[type]` prefix from this fixed set:

| Prefix | Goes to | Use for |
|---|---|---|
| `[decision]` | `docs/exp.md` § Decisions | Choice + rationale |
| `[preference]` | `docs/exp.md` § Preferences | Recurring rule (always X / never Y) |
| `[milestone]` | `docs/exp.md` § Milestones | Working solution / breakthrough |
| `[problem]` | `docs/exp.md` § Problems | Bug / failure / fix |
| `[fact]` | `docs/ref.md` (Source REQUIRED) | Verified external fact |
| `[tool]` | `docs/tools.md` | Tool syntax / gotcha / version |
| `[secret-ref]` | `docs/secrets.md` | Resource pointer (env-var name only — NEVER value) |

No prefix → REJECT (don't promote). The prefix is searchable + drives downstream prune behavior.

## Quality gates (mempalace `general_extractor.py` pattern)

REJECT entries that fail ANY gate:

- **Length**: < 20 chars → DROP
- **Code-only**: line is shell/imports/operators with no prose → DROP
- **Source-required for `[fact]`**: missing `Source:` URL or `file:line` → either drop or downgrade to `[problem]` with `(needs source)` note
- **Confidence**: vague, hedged ("maybe", "I think") → DROP unless backed by Source
- **Prose-to-symbol ratio**: < 30% words → DROP
- **Duplicate**: Jaccard ≥ 0.85 with existing entry in target → NOOP (don't add)

## Write semantics — mem0 ADD / UPDATE / DELETE / NOOP

For each candidate that passes gates:

| Action | When | What |
|---|---|---|
| **ADD** | No similar existing entry | Append under correct section |
| **UPDATE** | Existing entry with same subject + new info | Replace old; keep one entry per fact |
| **DELETE** | New entry contradicts old (new is correct) | Remove old, then ADD new |
| **NOOP** | Exact dup or strict subset of existing | Skip |

Per user direction: contradicts → **DELETE** (do not keep both with `(superseded)`); audit lives in `git log`.

Exception: when in doubt about which is correct, mark old as `(superseded)` so `mem-prune` will delete on next run, and add new. This 2-step gives reviewer a chance to roll back via `git checkout`.

## Steps

1. Determine date range (default today + yesterday journal files).
2. Read each journal file. Parse entries under sections (Logs / Wins / Pains / Questions).
3. For each entry:
   a. Apply quality gates → DROP failures.
   b. Classify type (5-type schema above).
   c. Format with `[type]` prefix + Source.
4. For each formatted candidate:
   a. Read target docs/<name>.md.
   b. Decide ADD / UPDATE / DELETE / NOOP.
   c. Apply.
5. Mark distilled source journal entries with `(distilled)` suffix.
6. Run `mem-prune` (or `_prune.py`) to clean up any newly-marked superseded.
7. Report:
   - ADD: N
   - UPDATE: M
   - DELETE: K (old contradicting entries removed)
   - NOOP: J (already covered / dup)
   - DROP: D (noise removed)
   - LEFT IN JOURNAL: L (open questions / unverified facts)

## Hard rules

- Never invent facts not in journal.
- Never write secret values to `docs/secrets.md` — only env-var name + how to obtain.
- Refuse `[fact]` without Source — downgrade to `[problem]` with `(needs source)` instead, OR DROP.
- Conflict → DELETE old + ADD new (or mark `(superseded)` then prune). Never keep both.
- Do NOT delete the journal file (raw log is permanent).
- Do NOT promote entries that don't start with a recognized `[type]` prefix.

## Cadence

- Daily: end of session, fast.
- Weekly: larger pass, follow with `/mem-reflect` for cross-entry patterns.
- Pre-compact: mandatory.
- Always: follow with `/mem-prune` to clear superseded markers.
