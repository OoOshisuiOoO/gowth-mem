# docs/handoff.md

Session state — read first thing, write before `/compact` or end of session.

In v2.0 this file is **synced** across machines. To keep multi-machine handoffs separate, each entry is prefixed with `host:<machine-name>`. The bootstrap hook can then filter by host on each machine.

## Format

```
- host:<machine> [doing|next|blocker|thread] <one-line content>  (YYYY-MM-DD)
```

## Examples

```markdown
- host:macbook-duy [doing] rewriting gowth-mem hooks for v2.0 global layout (2026-05-01)
- host:macbook-duy [next] finish auto-sync.py PostCompact wiring (2026-05-01)
- host:macbook-duy [blocker] none (2026-05-01)
- host:linux-srv [doing] backtest EMA strategy on M5 timeframe (2026-04-30)
- host:linux-srv [thread] need to validate slippage model — open from 2026-04-29 (2026-04-30)
```

## Hard rules

- One line per entry; if it doesn't fit, it doesn't belong here (use a topic file).
- Always include `host:<name>` prefix so multi-machine sync stays disambiguated.
- Always include `(YYYY-MM-DD)` suffix so stale entries are visible at a glance.
- Stale `[doing]`/`[next]` (>7 days) → DELETE on next prune. The `[blocker]` and `[thread]` lines stay until explicitly resolved.
- Conflict between local and remote handoff lines is resolved per-host: same-host newer line wins; different-host lines coexist.
