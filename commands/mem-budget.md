---
description: Show a 4-tier weighted context plan for a query (working/episodic/semantic/procedural) within a token budget. Deterministic Jaccard + tier weights + Ebbinghaus recency decay. No LLM.
---

Preview which files the v3.3 budget planner would load for a query, within a char budget.

Run with the Bash tool:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_budget.py" --query "$*" "$@"
```

## Flags

- `--ws <name>` — workspace (default: active).
- `--query "text"` — query text; empty = neutral 0.5 lexical score per file.
- `--budget N` — char budget (default 15000).
- `--json` — JSON output (path, chars, score per file + total).

## Score formula

```
score = lex_jaccard * tier_weight * (0.5 + 0.5 * recency_decay)
```

- **lex_jaccard**: char-trigram Jaccard between query and file head (4000c).
- **tier_weight**: from settings `context_budget.tier_weights`. Default `{working:1.0, episodic:0.7, semantic:0.8, procedural:0.6}`.
- **recency_decay**: `exp(-ln(2) * age_days / half_life_days)`. Default half-life 14 days.

## Tier classification

| Tier | Maps to |
|---|---|
| working | today's journal, `docs/handoff.md` |
| episodic | older journal files |
| semantic | docs, topic folders, research |
| procedural | shared `skills/` |

## Stable prefix (always loaded first)

`shared/AGENTS.md` → `shared/secrets.md` → `shared/tools.md` → `workspaces/<ws>/AGENTS.md` → `workspaces/<ws>/docs/handoff.md` → today's journal.

These slots maximise Anthropic prompt-cache hits across sessions.

## Wire-up

The planner is OPT-IN. Enable in `~/.gowth-mem/settings.json`:

```json
{
  "retrieval": { "use_budget_planner": true }
}
```

When enabled, `SessionStart` (`bootstrap-load.py`) uses the planner instead of the hard-coded stable prefix.
