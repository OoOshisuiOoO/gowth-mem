---
description: Reorganize topics within the active workspace — move slugs to new parents, rebuild MOCs, preserve wikilinks
argument-hint: "<slug> <new-parent-path>   |   --plan-from-stdin"
---

# /mem-restructure

Move one or more topics within the active workspace by updating their `frontmatter.parents` field. Slugs stay stable so wikilinks `[[slug]]` keep resolving.

## Single-slug usage

```
/mem-restructure ema-cross strategies/trend
```

→ Moves `workspaces/<active>/topics/ema-cross.md` to `workspaces/<active>/topics/strategies/trend/ema-cross.md`, sets `parents: [strategies, trend]`, and rebuilds MOCs + index.

## Bulk via stdin

```
/mem-restructure --plan-from-stdin
```

Then paste a YAML-style plan:

```yaml
- slug: ema-cross
  parents: [strategies, trend]
- slug: rsi-confluence
  parents: [strategies, oscillator]
```

## Steps

```bash
WS=$(python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_workspace.py" active)

if [ "$1" = "--plan-from-stdin" ]; then
  PLAN=$(cat)
else
  SLUG="$1"; PARENTS="$2"
  PLAN="- slug: $SLUG\n  parents: [$(echo "$PARENTS" | tr '/' ',' | sed 's/,/, /g')]"
fi

python3 - "$WS" <<PYEOF
import sys, re
from pathlib import Path
sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}/hooks/scripts")
from _frontmatter import parse_file, render
from _atomic import atomic_write
from _home import topics_dir

ws = sys.argv[1]
plan_text = """$PLAN"""

# Tiny YAML-ish parser for the plan
moves = []
current = {}
for line in plan_text.splitlines():
    if line.startswith("- slug:"):
        if current: moves.append(current)
        current = {"slug": line.split(":",1)[1].strip()}
    elif line.strip().startswith("parents:"):
        v = line.split(":",1)[1].strip()
        if v.startswith("[") and v.endswith("]"):
            inner = v[1:-1].strip()
            current["parents"] = [s.strip() for s in inner.split(",") if s.strip()]
        else:
            current["parents"] = []
if current: moves.append(current)

td = topics_dir(ws)
moved = []
for m in moves:
    slug = m["slug"]
    new_parents = m.get("parents", [])
    # Find current path of slug
    src = None
    for p in td.rglob(f"{slug}.md"):
        src = p
        break
    if src is None:
        print(f"skip: {slug} not found")
        continue
    fm, body = parse_file(src)
    fm["parents"] = new_parents
    dst_dir = td
    for part in new_parents:
        dst_dir = dst_dir / part
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / f"{slug}.md"
    atomic_write(dst, render(fm, body))
    if dst != src:
        src.unlink()
    moved.append((slug, dst))
    print(f"moved: {slug} → {dst.relative_to(Path.home() / '.gowth-mem')}")

print(f"total: {len(moved)} moves")
PYEOF

python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_moc.py" --ws "$WS"
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_index.py"

# Single commit per restructure batch
cd ~/.gowth-mem
git add -A
COUNT=$(git diff --cached --name-only | wc -l | tr -d ' ')
git commit -m "knowledge(restructure): $COUNT changes in workspace $WS"
```

## Hard rules

- Slugs are NEVER renamed by this command (would break wikilinks). To rename a slug, do it manually then run `/mem-restructure` to update parents.
- Only operates within the **active workspace**. Cross-workspace moves require explicit `/mem-workspace <other>` first.
- Max depth enforced by `settings.topic_layout.max_depth` (default 3).
