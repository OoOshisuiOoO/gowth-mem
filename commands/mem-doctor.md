---
description: "Diagnose and self-heal plugin registration, version drift, native wiring"
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

## Runs automatically since v4.7.2

The SessionStart hook invokes `bin/doctor.sh --quiet` detached on `source=startup` (no
`--pull`, so no network on the startup path). A manual-only doctor is why one machine sat on
v3.9.0 for months. Opt out with `GOWTH_MEM_NO_AUTOHEAL=1` or `"doctor": {"auto_heal": false}`
in `~/.gowth-mem/settings.json`.

The official upgrade path is still first: `claude plugin update gowth-mem -y`, then restart
Claude Code. The doctor is the fallback for a registry that stays pinned anyway. Check what a
machine is actually running:

```bash
claude plugin list --json | python3 -c "import json,sys;[print(p['id'],p['version'],p['scope'],p['enabled']) for p in json.load(sys.stdin) if 'gowth-mem' in p['id']]"
```

Or read it straight out of the running plugin:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_version.py"
```

## When to run

- After `claude /plugin marketplace update gowth-mem` to confirm the cache dir was materialized.
- When `[gowth-mem:bootstrap]` stops appearing in SessionStart context.
- Before pushing a fresh `~/.claude/settings.json` to a new machine.
- After running `/mem-ops migrate-v3` to confirm the v3 layout is healthy across all workspaces.

## v3 layout sanity checks (advisory)

`bin/doctor.sh` is plugin-install scoped (registry repair) and does NOT validate
`~/.gowth-mem/` layout. To verify the v3 migration succeeded, inspect:

- `settings.json` → `layout_version` should be `3`.
- `shared/backup-v3/v2-pre-v3-*/` should hold ≤2 snapshots (rolling window).
- Every `workspaces/<ws>/<slug>/` topic folder should have `00-README.md` (not just `<slug>.md`).
- Reserved subdirs (`docs`, `journal`, `skills`, `research`) should never be reshaped into topic folders.

## What it heals

| Symptom | Detection | Action |
|---|---|---|
| `installPath` outside `~/.claude/plugins/cache/` | path prefix check | rewrite to canonical cache path |
| `installPath` folder missing or incomplete | `[ -f .claude-plugin/plugin.json ]` | materialize from marketplace clone (tar pipe, exclude `.git`/`__pycache__`) |
| Registry version stale vs. marketplace | `version` field comparison | bump version + `lastUpdated` + `gitCommitSha` |

## Output

Heal events go to stderr. Stdout stays empty. Exit code is always 0 — safe to chain into hooks.

After a heal, restart Claude Code (or `/reload-plugins`) so the new `installPath` takes effect.
