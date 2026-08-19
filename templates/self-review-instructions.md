[gowth-mem:self-review ws={ws}] {review_count} turns logged.

This is an HONEST session self-review — not a status update, not praise. The whole
point is to find what was weak so both the user's prompting and Claude's reasoning
improve over time. A review with no concrete, quoted criticism is a FAILED review —
delete it and start over.

No long preamble to the user — the judge does the work silently; the ONLY
user-facing output is the 3-line summary from step 6.

## 0. Dispatch protocol (MAIN session — this is your ONLY step)

**You ARE the dispatched judge if your prompt hands you a session-log (or
transcript) path together with this rubric file — no one has to tell you "you
are the judge". In that case SKIP this section, start at 0b, and never
dispatch further subagents.** (This payload test is the recursion guard: a
judge that re-reads §0 as if it were the main session would dispatch another
judge, forever.)

The review must NOT run in the main context: it dilutes the conversation with
review tool calls, and a judge grading its own work in its own context is the
self-preference bias this rubric exists to kill. The main session's whole job:

1. **Dispatch ONE fresh-context background subagent** (Task/Agent tool — NOT a
   context-inheriting fork; the judge must be independent) with: the ABSOLUTE
   session-log path(s) you were given (hook reason, or resolved by
   `/mem-review`) + this rubric file + the ABSOLUTE `_scores.md` path from the
   hook reason. A fresh judge has no other context — never make it guess a
   path (see 0c Anchors). It executes steps 0b-6 below.
2. **Continue your work.** When the judge completes, relay its 3-line summary
   (step 6) to the user verbatim — nothing longer.

Fallback (ONLY when no subagent tool exists in this harness): run steps 0b-6
yourself, inline, and state "Reviewer: in-context" in the review block.
(There is no missing-log fallback: when no session log exists the hook never
emits this directive at all — it pauses the review until capture produces one.)

## 0b. Guards (the judge checks before scoring)

- **Signal floor:** if the session log has **fewer than 10 `## turn` blocks**, STOP —
  skip the review and tell the user in one line ("session too short for a meaningful
  retro — N turns, need 10"). Short-session retros produce noise, not signal.
- **Backlog mode:** when the turn source is a raw `.jsonl` transcript (dispatched by
  `/mem-review-backlog`), there are no `## turn` blocks — a "turn" for the floor is a
  user record with non-empty text; quote from the JSON `message.content` text and cite
  turn indexes instead of `## turn` numbers. Everything else in this rubric applies
  unchanged.
- **State which reviewer path was used** (`subagent` or `in-context`) in the review block.

## 0c. Anchors (fresh judge — derive, don't guess; NEVER resolve against your cwd)

- **Vault root** = the directory above `workspaces/` in your session-log path
  (this respects `GOWTH_MEM_HOME`; never assume `~/.gowth-mem/`).
- **`<ws>`** = the path segment right after `workspaces/` in that same path.
- **Score ledger** = `<vault-root>/workspaces/<ws>/journal/_scores.md` — must match
  the absolute path in the hook reason when one was given. Writing it relative to
  your cwd lands scores in the user's code repo: unsynced, unindexed, invisible to
  `/mem-review --history`.
- **Plugin scripts** (`_capture.py`, `_topic.py`) live at
  `<directory of this rubric>/../hooks/scripts/`.

## 1. Read the session log

Open the session log at the path given in the reason (`<ws>/journal/sessions/<date>-<sid8>.md`).
When TWO log paths were given (session split across midnight), read BOTH, older first —
they are one window. Each turn records: **User** (the prompt), **Claude** (the visible
reasoning summary), **Actions** (the tool-use trace — `Read(x) → Edit(y) → Bash(…)` — the
honest proxy for what Claude decided to do). Read every turn before scoring anything.
Also read the last row of the score ledger (0c Anchors) so you can state the delta vs
last review.

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

1. **Append a review block to the session log** (the newest file from step 1) —
   **via the locked appender, NEVER with a raw Write/Edit**: the main session keeps
   working while you review, and capture rewrites this log under a lock on every
   Stop — an unlocked write races it and silently loses a turn or your entire
   review block. Run (paths from 0c Anchors):

   ```bash
   GOWTH_MEM_HOME=<vault-root> python3 <plugin-scripts>/_capture.py \
     --append-review <session-log-path> <<'EOF'
   ## [self-review] {date} turn {review_count}
   ...the block below...
   EOF
   ```

   Block format:

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
   topic write path — one call per entry:
   `GOWTH_MEM_HOME=<vault-root> python3 <plugin-scripts>/_topic.py --append "[reflection] <pattern that would have prevented turn N's rework>" --ws <ws>`
   (the interface applies the quality gate, tags, dedup, and routing — entries must be
   content-dense, no hedging, ≥20 chars). NEVER hand-write topic files and NEVER append
   to `00-README.md`.

3. **Append one row to `<ws>/journal/_scores.md`** (create with the header if missing):

   ```
   | date | sid | turn | prompting | reasoning | collab | delta-vs-last |
   |------|-----|------|-----------|-----------|--------|---------------|
   | {date} | {sid8} | {review_count} | N | N | N | <one-line delta vs previous row> |
   ```

   Scores are on the 1-5 scale. The `delta-vs-last` cell states the direction vs the
   previous row (e.g. "prompting +1, reasoning flat — fewer vague asks this block").

## 6. Return the 3-line summary (the main session relays it verbatim)

End with a **3-line** summary in the USER'S language (Vietnamese if the session is in
Vietnamese): line 1 = the three N/5 scores, line 2 = the single biggest weakness this
block (quoted), line 3 = the one thing to change next block. Nothing longer — the detail
lives in the review block, not the chat. If you are the dispatched judge, these 3 lines
are your entire final report; the main session passes them to the user unchanged.

This is automation, not a conversation step. Be honest — chân thật, thẳng thắn.
