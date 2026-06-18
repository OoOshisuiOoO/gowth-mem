---
description: "Promote a verified [hypothesis] to a [ref] with Source, or mark it refuted"
---

Close the loop on an unverified claim. A `[hypothesis]` is a holding type — it bypasses the hedge gate because the claim is explicitly flagged as unconfirmed. Once the Verify step has been carried out, this command promotes the entry to `[ref]` (confirmed) or marks it `(refuted <date>)` (disproven).

The git diff of this operation is the audit trail: the before/after shows exactly when an unverified claim became a verified fact.

## When to use

- A test run, commit, benchmark, or URL confirms a hypothesis was correct
- A backtest or experiment shows the hypothesis was wrong
- Before `/mem-distill` to ensure promoted refs carry a real Source

## Hypothesis entry format (before verification)

```
- [hypothesis] <title>
  Verify: <how it will be confirmed or refuted — test name / metric / file:line / URL>
  <one-line claim>
```

The hedge gate (`_gate.py`) exempts `[hypothesis]` entries — forward-looking claims are allowed to lack evidence.

## Promotion: confirmed

Edit the entry in place:

```
- [ref] <title> (was hypothesis, verified 2026-06-19)
  Source: <evidence — test output path / commit SHA / file:line / URL>
  <same one-line claim, now a verified fact>
```

Then move or delete the original `Verify:` line (it is superseded by `Source:`).

If the hypothesis lived in a separate aspect file, you may also add it to the current topic's aspect via `_topic.append_entry` so the promoted ref is indexed.

Confirm the promoted entry passes the gate:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_gate.py" \
  --check "- [ref] FTS5 tag column speeds filtered recall (was hypothesis, verified 2026-06-19)\n  Source: tests/test_query.py::test_tag_filter line 42\n  FTS5 tag-indexed query is 3× faster than full-scan on 10k chunks"
```

The gate requires `Source:` on every `[ref]`. A ref without a source is rejected.

## Promotion: refuted

Mark the entry in place — do not delete it; a disproven hypothesis is evidence too:

```
- [ref] <title> (refuted 2026-06-19)
  Source: <what showed it was wrong — test output / file:line / URL>
  Claim was wrong because: <one line>
  <original claim for context>
```

Using `[ref]` (not a dead `[hypothesis]`) ensures the refutation is indexed and surfaced by `/mem-recall --type ref`.

## Commit convention

Use `Why-Code: verify-claim` in the commit body so the promotion is grep-able in git history:

```
knowledge(topic): verify FTS5 tag-filter hypothesis

Why-Code: verify-claim
Source: tests/test_query.py line 42
```

## Gate rules

- `[hypothesis]` — exempt from hedge gate; `Verify:` field is required by convention but not enforced by the gate (it is a documentation aid, not a rejection trigger)
- `[ref]` (promoted) — `Source:` is mandatory; gate rejects without it
- Hedge gate applies to `[ref]`: "maybe", "I think", "probably" without Source → rejected

## Canon

The verify→promote cycle is the core of the v3.9 provenance layer:

```
[hypothesis]  →  Verify step carried out  →  [ref] (Source: <evidence>)
                                          →  [ref] (refuted, Source: <counter-evidence>)
```

Hypotheses that go unverified for > 30 days should be flagged during `/mem-lint` as stale claims.
