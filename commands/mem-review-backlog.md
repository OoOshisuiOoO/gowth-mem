---
description: Auto-review past conversations from ~/.claude/projects that were never self-reviewed (ended before the 15-turn cadence, or predate v4.0). A machine-local ledger (review-ledger.json) marks reviewed vs unreviewed; this command works through the backlog oldest-first, scoring each with the v4.0 anti-sycophancy rubric and routing lessons to the vault.
---

Work through the unreviewed-conversation backlog. Default batch: **3 conversations** per run (token-aware); `$ARGUMENTS` may override, e.g. `/mem-review-backlog 5` or `/mem-review-backlog --stats`.

## 0. Status

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_review_ledger.py" --stats
```

If `unreviewed` is 0, report "backlog clean" and stop.

## Per-conversation loop (repeat up to the batch size)

1. **Pick the next candidate** (oldest first; thin sessions auto-skip):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_review_ledger.py" --next --json
```

2. **Read the transcript** at the returned `path` — it is JSONL; extract the user messages and the assistant's visible text/actions. For long transcripts read the first ~200 and last ~200 lines; the goal is honest assessment, not full replay.

3. **Review it honestly** using the same contract as live self-review — read `${CLAUDE_PLUGIN_ROOT}/templates/self-review-instructions.md` and apply it to this transcript:
   - anchored 1-5 on user prompting / Claude reasoning / collaboration,
   - harsh-reviewer-first, ≥2 verbatim-quoted weaknesses per dimension, quote-or-no-score,
   - prefer dispatching a fresh-context subagent as the judge (pass it the transcript path + rubric),
   - counterfactual gate before writing anything to the vault.

4. **Record the outcome** in the vault (synced):
   - scores → the active workspace's `journal/_scores.md`, same table format as live reviews, with `sid=<sid8> project=<project>` noted;
   - reflections that pass the counterfactual gate → route via `_topic.append_entry` / `_lesson.append_lesson` as `[reflection]` (never raw-dump the transcript).

5. **Mark it reviewed** (machine-local ledger, gitignored):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_review_ledger.py" --mark <sid> --status reviewed --note "u=X c=Y x=Z"
```

## Wrap-up

Report: how many reviewed this run, score summary, backlog remaining (`--stats`), and any cross-conversation pattern noticed (3+ similar weaknesses = candidate for a new rule/skill — say so explicitly).
