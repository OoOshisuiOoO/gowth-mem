---
description: Self-heal gowth-mem plugin's installed_plugins.json registration. Pulls the marketplace clone to latest, materializes the cache dir, atomically rewrites installPath. Idempotent — silent when healthy. Use after `claude /plugin marketplace update`, when hooks stop firing, or to verify portability before sharing config across machines.
---

Run the gowth-mem self-heal doctor.

Bypasses Claude Code issue #52218 — `autoUpdate` bumps version metadata in `~/.claude/plugins/installed_plugins.json` but leaves `installPath` pointing at a missing cache dir, so every gowth-mem hook silently skips after the next restart.

Run with the Bash tool:

```bash
DOCTOR="${CLAUDE_PLUGIN_ROOT:-$HOME/.claude/plugins/marketplaces/gowth-mem}/bin/doctor.sh"
[ -f "$DOCTOR" ] || DOCTOR="$HOME/.claude/plugins/marketplaces/gowth-mem/bin/doctor.sh"
if [ ! -f "$DOCTOR" ]; then
  echo "doctor.sh not found — run: claude /plugin marketplace update gowth-mem"
  exit 1
fi
# Default: --pull (fetch latest marketplace before heal). Any user-supplied args override.
if [ $# -eq 0 ]; then
  bash "$DOCTOR" --pull
else
  bash "$DOCTOR" "$@"
fi
```

## Args (forwarded to `bin/doctor.sh`)

- `--dry-run` — show what would change, write nothing
- `--quiet` — silent unless heal is applied
- `--pull` — git fetch + ff-only pull marketplace clone first (default when no args)
- `--market <m> --plugin <p>` — heal a different plugin

## When to run

- After `claude /plugin marketplace update gowth-mem` to confirm the cache dir was materialized.
- When `[gowth-mem:bootstrap]` stops appearing in SessionStart context.
- Before pushing a fresh `~/.claude/settings.json` to a new machine.

## What it heals

| Symptom | Detection | Action |
|---|---|---|
| `installPath` outside `~/.claude/plugins/cache/` | path prefix check | rewrite to canonical cache path |
| `installPath` folder missing or incomplete | `[ -f .claude-plugin/plugin.json ]` | materialize from marketplace clone (tar pipe, exclude `.git`/`__pycache__`) |
| Registry version stale vs. marketplace | `version` field comparison | bump version + `lastUpdated` + `gitCommitSha` |

## Output

Heal events go to stderr. Stdout stays empty. Exit code is always 0 — safe to chain into hooks.

After a heal, restart Claude Code (or `/reload-plugins`) so the new `installPath` takes effect.
