---
description: Show exactly what the SessionStart bootstrap injects — per-file on-disk size, chars actually loaded, truncation, and any file dropped for lack of budget. Reads the real hook plan, so it cannot drift from what the model receives.
---

Report the real bootstrap token footprint for the active workspace.

Run with the Bash tool:

```bash
python3 "$CLAUDE_PLUGIN_ROOT/hooks/scripts/bootstrap-load.py" --report
```

To check another workspace, prefix with `GOWTH_WORKSPACE=<name>`.

This calls the same `_plan()` the SessionStart hook uses, so the numbers ARE what the
model gets. Before v4.3 this command measured a pre-v2.7 layout (`$PWD/docs/*.md`) that
no longer exists — it reported 0 of 9 files and quoted a 60,000-char cap the code never
used, which is exactly why a regression that loaded only 2 of 5 bootstrap files and
silently dropped `docs/handoff.md` went unnoticed.

How to read it:

- **on-disk** vs **loaded** — the gap is what the model never sees.
- **truncated** — the file is loaded head-first up to the per-file cap. `handoff.md` is
  newest-first, so its most recent entries survive; a file that puts current state at
  the BOTTOM would lose it, which is why nothing is tail-truncated.
- **DROPPED — no budget left** — a file got zero budget. Statics are allocated in
  priority order (shared AGENTS → secrets → tools) after the per-session deltas are
  reserved, so a bloated static file starves the ones behind it.

What to do when something is dropped or heavily truncated:

- `shared/secrets.md` is meant to hold env-var **pointers only** (`<env:NAME>`), never
  prose or real values. It is the usual culprit.
- `docs/handoff.md` growing past a few KB → `/mem-handoff` to rotate stale bullets.
- Topic files, `docs/{exp,ref,tools,files}.md` and skills are **not** in the bootstrap
  at all — they are recalled on demand via `/mem-recall`, so their size does not affect
  this budget.

Token estimate is chars / 4 (±20% vs the real tokenizer).
