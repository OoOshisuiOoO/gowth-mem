---
description: "Show what the bootstrap and MEMORY.md block cost per session, plus recall stats"
---

Report the real memory footprint for the active workspace — what the model receives at
session start, after a compaction, and per prompt.

Run with the Bash tool:

```bash
python3 "$CLAUDE_PLUGIN_ROOT/hooks/scripts/bootstrap-load.py" --report
```

To check another workspace, prefix with `GOWTH_WORKSPACE=<name>`.

This calls the same code the SessionStart hook runs, so the numbers ARE what the model gets.

How to read it (v4.8):

- **mode=native** — this project loads `<ws>/memory/MEMORY.md` through Claude Code's auto
  memory (200 lines / 25 KB, re-attached after every compaction). The hook only prints a
  header. **mode=fallback** — not wired (`/mem-setup native`), or `native.enabled` is false:
  the hook prints the same sections as a ≤ 8,500-char bootstrap.
- **memfile: block N lines / M chars** — the managed block (rules digest, handoff newest-first,
  topic index, recent decisions, secret pointers, how to recall) and the budget it fits
  (`memfile.max_lines` / `memfile.max_chars`). **free zone** = Claude's own auto-memory lines
  below the end marker; the host cuts the file at 200 lines, so a large free zone shrinks the
  block (`/mem-doctor` warns past 190).
- **emission: startup / compact** — the hook's own additionalContext sizes. Anything a hook
  emits at ≥ 10,000 chars is persisted by Claude Code to a file with a 2,000-char preview
  (measured on 2.1.283); every gowth-mem emitter is clamped at 9,000.
- **recall:** — per-prompt recall totals from `state.json` (prompts that received memory,
  entries, chars). Zero means the gate never passed: check `/mem-recall <query>` returns hits
  and that `recall.on_prompt_enabled` is not false.

What to do when the block is thin or truncated:

- `docs/handoff.md` growing past a few KB → `/mem-handoff` to rotate stale bullets; the digest
  keeps the newest section first regardless.
- `shared/secrets.md` must hold env-var **pointers only** (`` `NAME` ``), never values — only
  backticked NAMES enter the block.
- Topic bodies, `docs/{exp,ref,tools,files}.md`, journals and skills are **not** in the block:
  they reach the model per prompt (gated BM25 recall) or via `/mem-recall` and Read.

Token estimate is chars / 4 (±20% vs the real tokenizer).
