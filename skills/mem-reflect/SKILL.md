---
name: mem-reflect
description: Use periodically (weekly, end-of-sprint, post-incident) to generate high-level reflections from accumulated journal + docs/exp.md. Writes to docs/exp.md § Reflections. Generative-Agents-style importance × recency × novelty scoring.
---

# mem-reflect

Generative-Agents-inspired reflection: extract patterns across many entries that no single entry reveals.

## Inputs

- Optional date range. Default: last 7 days.
- Workspace root (`$CLAUDE_PROJECT_DIR` or `$PWD`).

## Sources

- `docs/journal/*.md` (last 7 days)
- `docs/exp.md` (existing lessons)

## Steps

1. Read all source files in date range.
2. Score each entry by:
   - **Importance** (1-5): how surprising / consequential is it?
   - **Recency**: more recent = higher weight
   - **Novelty**: penalize entries similar to existing reflections in `docs/exp.md`
   - Aggregate score = importance × recency_weight × novelty_factor
3. Pick the **top 3 entries** (or clusters of related entries).
4. For each, synthesize a reflection:
   - **Claim**: 1-line generalized insight (Andy Matuschak evergreen-style)
   - **Evidence**: 2-3 source entries (with file:line refs)
   - **Implication**: what should change next time?
5. Append under `docs/exp.md`:

```markdown
## Reflections

### YYYY-MM-DD: <reflection title>

**Claim**: <evergreen 1-line>

**Evidence**:
- docs/journal/2026-04-15.md:23 — <line>
- docs/exp.md § Lessons — <existing fact>

**Implication**: <1-line action / rule>
```

6. For any reflection that feels stable + portable to other projects → suggest user run claude-obsidian's `/save` to promote it to `wiki/concepts/`.

## Hard rules

- Maximum 3 reflections per run (avoid noise).
- Each reflection must cite ≥2 source entries with file:line refs (no hallucination).
- A reflection that duplicates an existing one in `docs/exp.md § Reflections` → SKIP (NOOP, mem0 pattern).
- Reflections never overwrite source entries; always additive.

## Why this saves tokens long-term

After N weeks, `docs/exp.md § Reflections` contains the high-signal generalizations. Future sessions read these reflections instead of replaying journal entries — 10× compression on patterns.
