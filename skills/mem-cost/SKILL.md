---
name: mem-cost
description: Use when user asks how big bootstrap context is, before /compact, or when wanting to verify token efficiency. Estimates char/token cost of AGENTS.md + 6 docs/* + recent journal files.
---

# mem-cost

Quick token-cost estimator for the bootstrap layer.

## Inputs

- Workspace root (`$CLAUDE_PROJECT_DIR` or `$PWD`).

## Steps

1. Sum char count of each bootstrap file:
   - `AGENTS.md`
   - `docs/handoff.md`, `docs/exp.md`, `docs/ref.md`, `docs/tools.md`, `docs/secrets.md`, `docs/files.md`
   - `docs/journal/<today>.md`, `docs/journal/<yesterday>.md`
2. Estimate tokens: chars ÷ 4 (rough English/Vietnamese OpenAI / Anthropic tokenizer ratio).
3. Compare against bootstrap cap (60,000 chars ≈ 15,000 tokens).
4. Print per-file breakdown + total + warning if over cap.

## Output format

```
file                                 | chars | ~tokens
-------------------------------------|-------|--------
AGENTS.md                            |  1234 |   308
docs/handoff.md                      |   567 |   141
...
TOTAL                                |  8910 |  2227

Cap: 60,000 chars (~15,000 tokens).
Status: OK / Approaching cap / OVER cap.
```

## When to act on result

- **OVER cap**: bootstrap is being truncated. Run `/mem-distill` (clear distilled journal entries), `/mem-promote` (move topic-heavy content to wiki/), or manually shorten `docs/exp.md`.
- **Approaching cap**: schedule `/mem-distill` soon; check which file is largest.
- **Plenty of room**: nothing to do.
