[gowth-mem:self-review ws={ws}] {review_count} turns logged.

This is an HONEST session review. The user-facing output is an **insights report** in the style
of Claude Code `/insights` — evidence-grounded narrative + metrics + actionable suggestions —
NOT a rubric walkthrough. The rubric below still governs HOW you judge (anti-sycophancy is
mandatory); it just never leaks into the reply. A review with no concrete, quoted criticism is a
FAILED review — delete it and start over.

Do the work silently, then reply with ONLY the report in step 4.

## 0. Guards

- **Critic separation:** you are grading your own work — dispatch the review to a
  fresh-context subagent when the Task/Agent tool is available (pass it the log paths + this
  file). Fall back to in-context only if no subagent tool exists. Record which path in the log
  block (step 5), NOT in the reply.
- **Window:** all `## turn` blocks after the last `## [self-review]` block. One session's log
  may span multiple date files (`<date>-<sid8>.md`) — read them ALL before judging.
- **Small window (<10 turn blocks):** still produce the FULL report from available evidence
  (transcript context, actions traces, files written). Only skip the `_scores.md` row, and mark
  it with one footer line `(scores: not recorded — window <10 turns)`. NEVER reply with skip
  mechanics instead of analysis.

## 1. Read before judging

1. The session log file(s) covering the window — every turn: User prompt, Claude summary,
   Actions trace (the honest proxy for what Claude did).
2. The last row of `<ws>/journal/_scores.md` (delta context).
3. `<ws>/docs/tools.md` — respect any recorded user output-style preferences.

## 2. Judge (internal — this shapes the report, it does not appear as-is)

- Write the harsh-reviewer paragraph FIRST (wasted turns, wrong assumptions, rework loops
  visible in Actions traces). If you can't name anything, you haven't read the log.
- Score 3 dimensions, anchored 1-5 (1 blocked · 2 notable friction · 3 adequate · 4 strong ·
  5 exemplary): **user prompting** (clarity, context, specificity, decomposition, goal↔outcome),
  **Claude reasoning** (wrong paths, unverified assumptions, over/under-engineering, thrash),
  **collaboration** (rework loops, corrections the user had to make, missed shortcuts).
- Honesty contract: **quote-or-no-score** (verbatim + turn number); **≥2 weaknesses per
  dimension**; **any ≥4 needs 2 cited evidences**; **unsupported praise = delete the sentence**.
- Prepare: 1 worst-prompt rewrite (before → after) and 1 "Claude should have done X at turn N".

## 3. Counterfactual gate for reflections

A `[reflection]` may be written to the vault ONLY if it would concretely have prevented an
observed rework/mistake in THIS window. Generic advice goes in the report's Suggestions section
only. Max 3.

## 4. Reply to the user = the INSIGHTS REPORT (their language, e.g. Vietnamese)

```
# 📊 Session Insights — {ws} / <sid8> (turns <a>–<b>)

## 1. Activity Overview
<counts that matter: turns, deliverables shipped, commits, agents/workflows run — 2-4 lines>

## 2. Area Distribution
| Area | Việc | Trạng thái |   ← one row per work area in the window

## 3. Interaction Style
<how the user actually prompted: 1-2 quoted strengths + the 1-2 quoted patterns that cost
turns. Each claim = "quote" (turn N). This section carries the prompting score's evidence.>

## 4. Friction Points  (ranked 🔴 🟠 🟡 — skip severity levels with no evidence)
<each: what happened → "quote" (turn N) → root cause → what would have prevented it.
Claude-caused friction listed with the same severity rules as user-caused. Include the
worst-prompt rewrite (before → after) and the "Claude should have done X at turn N" here.>

## 5. Outcomes
<fully / mostly / not achieved, with evidence; open items>
Scores: prompting N/5 · reasoning N/5 · collab N/5 (vs last: ±N/±N/±N)

## 6. Suggestions (paste-ready)
<CLAUDE.md / workspace-AGENTS.md rule blocks derived ONLY from frictions observed in this
window (≥2 occurrences across sessions → recommend making it permanent). No platitudes.>
```

Rules for the reply: NO rubric narration, NO ledger/skip mechanics, NO "review complete" meta.
A section without evidence gets `(không đủ dữ liệu)` — never filler. Length follows evidence:
rich window = detailed report; thin window = short report, still all 6 sections.

## 5. Write the archive outputs (deterministic)

1. Append to the session log (newest date file):
   ```
   ## [self-review] {date} turn {review_count}
   **Reviewer:** <subagent | in-context>
   **Harsh reviewer:** <paragraph>
   **User prompting: N/5** — <2+ weaknesses, each "quote" (turn X)>
   **Claude reasoning: N/5** — <2+ weaknesses, each "quote" (turn X)>
   **Collaboration: N/5** — <2+ weaknesses, each "quote" (turn X)>
   **Worst prompt rewrite:** <before → after>
   **Claude should have:** <X at turn N>
   ```
2. Route counterfactual-passed `[reflection]` entries (0-3) to topic files via the normal write
   path (quality gates apply; never `00-README.md`).
3. Append one row to `<ws>/journal/_scores.md` (create with header if missing) —
   `| {date} | {sid8} | {review_count} | N | N | N | <one-line delta> |` — unless the window
   was <10 turns (see §0).

Be honest — chân thật, thẳng thắn. Sycophancy is the failure mode this review exists to kill.
