---
description: Honest session self-review (v4.0 metacognition). Scores this session on user prompting, Claude reasoning, and collaboration (anchored 1-5 each) with verbatim-quote evidence from the captured turn log, writes the scores to journal/_scores.md, and routes counterfactual-passed reflections to topics. Anti-sycophancy by contract; prefers a fresh-context subagent judge. Use --history to render the score trend.
---

# /mem-review

Run an HONEST self-review of the current session, on demand — regardless of the
15-turn auto-trigger counter.

Modeled on the every-N-turn reflection loop (`.claude/research/v4.0-metacognition.md`
§4). The Stop hook captures each turn (user prompt + Claude's visible summary + the
tool-use **actions trace**) into `<ws>/journal/sessions/<date>-<sid8>.md`; this command
scores that log with a strict anti-sycophancy rubric so both the user's prompting and
Claude's reasoning improve over time. (Note: Claude Code's extended-thinking blocks are
signature-only/empty in transcripts, so the actions trace — not thinking — is the honest
proxy for "what Claude decided to do".)

## What it does

Reads the captured session log, then produces a review with three scores on an anchored
**1-5 scale** (1 = blocked progress … 5 = exemplary, cite-able) — **user prompting**
(5 sub-criteria: clarity, context-completeness, specificity, decomposition, goal↔outcome
alignment), **Claude reasoning**, **collaboration** — each backed by verbatim quotes from
the log. The rubric forces honesty: a harsh-reviewer paragraph written before any score,
≥2 quoted weaknesses per dimension, two cited evidences for any score ≥4, one rewrite of
the worst prompt, and one "Claude should have done X at turn N". Unsupported praise is
deleted, not softened. To counter self-preference bias, the review is dispatched to a
fresh-context subagent when the Task/Agent tool is available (in-context only as fallback).

Outputs (deterministic format):
- a `## [self-review] <date> turn <N>` block appended to the session log,
- 0-3 `[reflection]` entries — only those passing the **counterfactual gate** ("would this
  have prevented an observed rework/mistake in THIS log?") — routed to topic files through
  the normal write path (they pass the quality gate); generic advice stays in the reply,
- one row appended to `<ws>/journal/_scores.md` (`| date | sid | turn | prompting |
  reasoning | collab | delta-vs-last |`) — the improvement trend,
- a 3-line summary replied to the user, in the user's language.

## When to invoke

- End of a working session, before `/compact`, to bank the lessons while context is fresh.
- After a session that felt inefficient — surface the rework loops and vague asks.
- Any time you want a score without waiting for the 15-turn auto-trigger.
- With `--history` to see whether the scores are trending up.

Short sessions are skipped: if the log has fewer than 10 turns there is too little signal,
and the review reports that instead of scoring.

## Usage

**On-demand review (default):** follow `templates/self-review-instructions.md` now,
against the current session log, and write all outputs described there. Resolve the paths
first:

```bash
# Active workspace + today's session log + score ledger
WS="$(python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_workspace.py" active)"
echo "workspace: ${WS}"
ls -1 "${HOME}/.gowth-mem/workspaces/${WS}/journal/sessions/" 2>/dev/null | tail -5
```

Then read `${CLAUDE_PLUGIN_ROOT}/templates/self-review-instructions.md` and carry out
its steps against the most recent session log (the one for the current `session_id`).

**History mode (`--history`):** render the score trend instead of scoring. Read
`<ws>/journal/_scores.md` and report the direction over time — are prompting / reasoning /
collaboration scores rising, flat, or falling across the last rows?

```bash
WS="$(python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_workspace.py" active)"
cat "${HOME}/.gowth-mem/workspaces/${WS}/journal/_scores.md" 2>/dev/null \
  || echo "no _scores.md yet — run /mem-review first"
```

Summarize the trend in 2-3 lines: latest scores, the direction vs the earliest row, and
the one dimension most in need of work.

## Honesty contract

The full rubric lives in `templates/self-review-instructions.md`. A review with no
concrete, quoted criticism is a FAILED review — the anti-sycophancy mechanisms (harsh
paragraph first, quote-or-no-score, counterfactual reflection gate, fresh-judge dispatch)
are mandatory, not optional. The trigger cadence and settings (`reflection.turn_interval`,
`reflection.enabled`, `reflection.capture_thinking` — the last now gates only the
opportunistic thinking line; the actions trace is always captured) are in `settings.json`.

## Related

- `/mem-forget` — archives old session logs past `journal.raw_ttl_days`; salvages the
  `## [self-review]` blocks into `journal/_salvage.md` first.
- `/mem-distill` — route salvaged reflections / scores into durable topic files.
- `/mem-reflect` — Generative-Agents-style reflections over journal + exp (topic-level,
  not session-scoring).
