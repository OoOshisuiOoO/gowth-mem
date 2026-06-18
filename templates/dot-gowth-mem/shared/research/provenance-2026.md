---
type: research
slug: provenance-2026
title: Provenance & Verification Canon — verified vs unverified knowledge, goals, and self-explaining commits
created: 2026-06-19
last_touched: 2026-06-19
sources:
  - gemini_deep_research 2026-06-19 (conv c_e3c1ffe63815bfd2) — deterministic epistemic state machine, [goal] as first-class, WHY-from-diff
  - perplexity_deep_research 2026-06-19 (backend 4da8b649) — epistemic enum, fact-validity, goal lifecycle, commit trailers
status: active
---

# Provenance & Verification Canon

Companion to `data-quality-2026.md` (what to keep) and `extraction-reuse-2026.md`
(how to write it). This file answers: **how do we mark whether knowledge is
VERIFIED ("chắc chắn đúng") vs UNVERIFIED ("chưa verify"), how do we persist the
user's GOALS, and how does each commit explain WHY/WHAT/WHEN** so `git log` alone
reconstructs the reasoning. Added v3.9.

> The strongest 2025-2026 convergence is NOT "the LLM says 0.83 confidence" — LLMs
> are poorly calibrated for absolute confidence. Production systems (Zep/Graphiti,
> Cognee, Engram) use a deterministic **epistemic state machine** + non-destructive
> invalidation + provenance, never a floating score. gowth-mem realizes this with
> the TYPE itself as the epistemic marker. (Gemini + Perplexity DR 2026-06-19)

## 1. Verified vs unverified is TYPE-ENCODED (deterministic, no drift)

Don't add a parallel `confidence:` field that can drift from reality. The entry
**type IS the epistemic status**, regex-checkable and impossible to desync:

| Epistemic status | gowth-mem type | Hard requirement (`_gate.py`) |
|---|---|---|
| **verified** — ground truth, cited | `[ref]` | `Source:` (URL / file:line / commit) — already enforced |
| **unverified** — belief / assumption / pending | `[hypothesis]` | `Verify:` path (how it will be confirmed/refuted) |
| **decided** — a committed choice | `[decision]` | `because/since/để/vì` rationale + alternative |
| **intent** — what the user wants | `[goal]` | `Status:` + success criterion |

The epistemic enum from the research (`hypothesis → probable → verified →
refuted → superseded`) collapses, for a deterministic markdown system, into:
- **unverified** = a live `[hypothesis]`,
- **verified** = a `[ref]` with `Source:`,
- **refuted / superseded** = marked `(refuted YYYY-MM-DD)` / moved to `## Superseded`.

## 2. The promotion path (unverified → verified) — git IS the audit trail

A `[hypothesis]` is **allowed to hedge** ("may", "I think") — uncertainty is its
nature, so it is *exempt* from the hedge gate. But it MUST say HOW it gets
resolved, or it is idle speculation = junk:

```markdown
## [hypothesis] EMA-50 cross may beat EMA-20 on gold in trending regimes
Verify: backtest 5y GC walk-forward, compare Sharpe; confirm on 2026 out-of-sample.
```

When the verification runs and confirms it, **promote the type**:

```markdown
## [ref] EMA-50 cross beats EMA-20 on gold in trending regimes (was hypothesis, verified 2026-06-19)
Source: Strategies/ema/backtest/2026-06-19-walkforward.json (Sharpe 1.4 vs 1.1)
```

The old `[hypothesis]` is moved to `## Superseded` or deleted — **git history is
the `status_history`** (transaction time = commit time; the `(was hypothesis,
verified <date>)` marker = valid time). Never silently rewrite; the diff is the
proof. If verification REFUTES it: mark `[hypothesis] ... (refuted 2026-06-19)`
and record why in a `[ref]`.

## 3. `[goal]` — the user's intent as first-class memory

Generative agents fail "goal persistence" (QGP): they declare false completion or
repeat work because intent was never durably stored (Gemini DR). Persist goals
separately from facts so a future session reconstructs *what the user is trying to
achieve* and *why each decision was made*.

```markdown
## [goal] Ship the gowth-mem provenance layer to production
Status: active
Done when: [goal]+[hypothesis] types shipped, gate enforces both, tests green, released.
Motivated-by: user request 2026-06-19
```

- **Status** enum (lifecycle): `active | paused | achieved | abandoned | blocked | superseded`.
- **Done when** MUST be externally verifiable (a test, a count, a released tag) —
  vague wishes ("make it better") are rejected (`goal_without_status`).
- **Link work back to intent**: `[decision]`/`[hypothesis]`/`[exp]` entries that a
  goal motivated carry `Motivated-by: [[<goal-slug>]]` (or the goal title). This
  builds the causal graph — "why did we choose X?" walks decision → goal without
  hallucination.
- Never delete: mark `achieved`/`abandoned`/`superseded` (audit trail).

## 4. Body type order (current-truth-on-top, trust-descending)

```
[goal] → [decision] → [ref] → [tool] → [hypothesis] → [exp] → [reflection] → [skill-ref] → [secret-ref] → ## Superseded
```

Intent frames everything (top); committed decisions; verified facts/tools; THEN
unverified hypotheses (visually below the verified line = lower trust); episodic
lessons; meta reflections; pointers. Superseded history trails at the bottom.

## 5. Self-explaining commits — WHY / WHAT / WHEN (deterministic, no LLM)

A good commit explains **WHY** (the diff already shows WHAT). `_commitmsg.py`
derives all three from the staged knowledge diff — no LLM, so no hallucination:

```
add(trade): +1 [goal] +2 [hypothesis] in ema-cross

Why: capture/track objective — Ship the gowth-mem provenance layer to production

- 3 files changed, +41 / -2 lines
- Focus: trade/ema-cross
- Largest: ema-cross/2026-06-19-design.md (+33/-0)
When: 2026-06-19 (knowledge date)

Workspace: trade
Topics: ema-cross
Entries: +1 goal +2 hypothesis
Files: 3 (+1 ~2 -0)
Why-Code: capture-objective
Machine: mac
Context: stop-sync
```

**WHY derivation (field → rationale mapping, deterministic):**

| Diff signal | Why | Why-Code |
|---|---|---|
| `[hypothesis]` removed + `[ref]` added | promote unverified → verified after confirmation | `verify-claim` |
| `[goal]` title added | capture/track objective — `<title>` | `capture-objective` |
| `[decision]` title (+ because-clause) | `<title> (<rationale>)` | `record-decision` |
| `[hypothesis]` title added | log unverified claim pending verification | `log-hypothesis` |
| `[ref]` added | record verified finding (cited Source) | `record-finding` |
| journal deletes ≥3 / handoff-archive | forget stale raw past 7d TTL | `forget-stale` |
| line-level deletions dominate | remove superseded / duplicate entries | `prune-stale` |
| MOC regen only | regenerate topic MOC index | `rebuild-moc` |

Grep the history: `git log --grep 'Why-Code: verify-claim'` (every promotion),
`git log --grep 'Workspace: trade'`, `git log --grep '^add('`. The subject is
WHAT, the `Why:` line is WHY, `When:` is the knowledge date (vs commit date).

## 6. Content taxonomy → memory class (what to distill, signal vs noise)

| Captured content | Type | Memory class | Keep when | Noise |
|---|---|---|---|---|
| user goal / intent | `[goal]` | working / control | multi-step, future-relevant | one-turn subtask |
| decision made | `[decision]` | semantic + procedural | a choice future work depends on | a mere suggestion |
| verified fact / deep-research finding | `[ref]` | semantic | has a Source, generalizes | no provenance |
| unverified claim / assumption | `[hypothesis]` | episodic-staging | drives a pending test/watch | idle speculation |
| learned lesson | `[exp]` / `lessons.md` | episodic | specific cause, reusable | "tried stuff, worked" |
| tool quirk | `[tool]` | procedural | version + working command | deprecated/untested |
| pattern (≥2×) | `[reflection]` | semantic (meta) | seen ≥2× with examples | one-off opinion |

Heuristic (Perplexity): if an item can't answer one of **"what is true / what are
we trying to do / what did we decide / how should we operate"** → it stays raw in
the journal, don't distill it.

## 7. Retention (adds to `data-quality-2026.md` §3)

| Prefix | Memory class | TTL | Delete trigger | importance |
|---|---|---|---|---|
| `[goal]` | working/reflective | ∞ | mark achieved/abandoned/superseded (never delete) | 1.0 |
| `[hypothesis]` | episodic-staging | 30d | refuted, OR age>30d AND never promoted to `[ref]` | 0.4 |
