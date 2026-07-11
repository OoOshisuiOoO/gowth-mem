[gowth-mem:self-review ws={ws}] {review_count} turns logged.

This is an HONEST session self-review — not a status update, not praise. The whole
point is to find what was weak so both the user's prompting and Claude's reasoning
improve over time. A review with no concrete, quoted criticism is a FAILED review —
delete it and start over.

Do this WITHOUT writing the user a long preamble first — do the work silently, then
reply with the short summary in step 6.

## 0. Guards (check before scoring)

- **Signal floor:** if the session log has **fewer than 10 `## turn` blocks**, STOP —
  skip the review and tell the user in one line ("session too short for a meaningful
  retro — N turns, need 10"). Short-session retros produce noise, not signal.
- **Critic separation (anti-self-preference bias):** you are grading your own work, so
  prefer a fresh judge. If the Task/Agent tool is available, **dispatch the review to a
  fresh-context subagent**, passing it the session-log path and this rubric; only fall
  back to reviewing in-context if no subagent tool is available. State which path you used.

## 1. Read the session log

Open the session log at the path given in the reason (`<ws>/journal/sessions/<date>-<sid8>.md`).
Each turn records: **User** (the prompt), **Claude** (the visible reasoning summary),
**Actions** (the tool-use trace — `Read(x) → Edit(y) → Bash(…)` — the honest proxy for
what Claude decided to do). Read every turn before scoring anything. Also read the last
row of `<ws>/journal/_scores.md` (if it exists) so you can state the delta vs last review.

## 2. Write the harsh-reviewer paragraph FIRST

Before any number, write one paragraph answering: **"What would a harsh senior reviewer
say about this session?"** Be blunt — wasted turns, wrong assumptions, vague asks,
rework loops visible in the Actions trace. This paragraph goes at the top of the review
block. If you cannot name anything a harsh reviewer would criticize, you have not read
the log closely enough.

## 3. Score 3 dimensions on a 1-5 anchored scale

Use this exact scale for every dimension (cite the anchor you land on):

- **1 — blocked progress:** actively caused a failure / dead-end / had to be undone.
- **2 — notable friction:** rework, backtracking, or repeated clarification needed.
- **3 — adequate:** got there, but with avoidable inefficiency or minor gaps.
- **4 — strong:** efficient and correct, only small nits.
- **5 — exemplary:** cite-able as a model example; nothing a harsh reviewer would change.

Score each dimension and justify it with **verbatim quotes** from the log (copy the exact
text, wrap in quotes, cite the turn number). **A judge must quote the turns it scores —
no quote, no score.**

1. **User prompting** — evaluate these 5 sub-criteria explicitly:
   (a) clarity, (b) context-completeness, (c) specificity / constraints stated,
   (d) decomposition (one goal per ask, sized right), (e) goal↔outcome alignment
   (did the ask match what was actually needed?).
2. **Claude reasoning** — wrong paths taken, unverified assumptions, over/under-engineering,
   turns spent going nowhere (visible as thrash in the Actions trace).
3. **Collaboration** — rework loops, corrections the user had to make, shortcuts missed.

REQUIRED honesty mechanisms (the review is INVALID without every one of them):
- **≥2 concrete weaknesses per dimension**, each with a **verbatim quote** from the log.
- **Any score ≥4 needs 2 cited evidences.** Can't cite two? The score is not a 4 or 5.
- **Unsupported praise = delete the sentence.** No "great job", "solid work", "excellent"
  unless a quoted line proves it. Sycophancy is the failure mode this review exists to kill.
- **1 concrete rewrite of the worst user prompt** — show the actual better wording.
- **1 "Claude should have done X at turn N"** — a specific missed move, with the turn number.

## 4. Counterfactual gate for reflections

A `[reflection]` rule may be written to the vault ONLY if it passes:
**"Would this concretely have prevented an observed rework or mistake in THIS log?"**
If yes → route it (step 5.2). If it is generic advice with no failure in this log that it
would have caught → it goes in the summary reply only (step 6), **not** the vault. This
gate is what keeps the reflection ledger high-signal instead of platitudes.

## 5. Write the outputs (all deterministic format)

1. **Append a review block to the session log** (the same file from step 1):

   ```
   ## [self-review] {date} turn {review_count}

   **Reviewer:** <subagent | in-context>
   **Harsh reviewer:** <the paragraph from step 2>

   **User prompting: N/5** — <cover the 5 sub-criteria; 2+ weaknesses, each "quote" (turn X)>
   **Claude reasoning: N/5** — <2+ weaknesses, each "quote" (turn X)>
   **Collaboration: N/5** — <2+ weaknesses, each "quote" (turn X)>

   **Worst prompt rewrite:** <before → after>
   **Claude should have:** <X at turn N>
   ```

2. **Route the counterfactual-passed `[reflection]` entries** (0-3) through the normal
   topic write path (they must pass the quality gate — content-dense, no hedging, ≥20
   chars). One line each, e.g. `[reflection] <pattern that would have prevented turn N's
   rework>`. Route to the topic folder whose keywords overlap (≥3 words), else `misc`.
   NEVER append to `00-README.md`.

3. **Append one row to `<ws>/journal/_scores.md`** (create with the header if missing):

   ```
   | date | sid | turn | prompting | reasoning | collab | delta-vs-last |
   |------|-----|------|-----------|-----------|--------|---------------|
   | {date} | {sid8} | {review_count} | N | N | N | <one-line delta vs previous row> |
   ```

   Scores are on the 1-5 scale. The `delta-vs-last` cell states the direction vs the
   previous row (e.g. "prompting +1, reasoning flat — fewer vague asks this block").

## 6. Reply to the user (3 lines, their language)

Reply with a **3-line** summary in the USER'S language (Vietnamese if the session is in
Vietnamese): line 1 = the three N/5 scores, line 2 = the single biggest weakness this
block (quoted), line 3 = the one thing to change next block. Nothing longer — the detail
lives in the review block, not the chat.

This is automation, not a conversation step. Be honest — chân thật, thẳng thắn.
