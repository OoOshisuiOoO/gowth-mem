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

→ Moves the topic folder `workspaces/<active>/ema-cross/` (including `00-README.md`, all `YYYY-MM-DD-<aspect>.md` files, and `lessons.md`) to `workspaces/<active>/strategies/trend/ema-cross/`, sets `parents: [strategies, trend]` in the `00-README.md` frontmatter, and rebuilds the workspace MOC + topic README + search index.

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
import sys, shutil
from pathlib import Path
sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}/hooks/scripts")
from _frontmatter import parse_file, render
from _atomic import atomic_write
from _home import workspace_dir, is_topic_folder, TOPIC_README

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

wsd = workspace_dir(ws)
moved = []
for m in moves:
    slug = m["slug"]
    new_parents = m.get("parents", [])
    # Find current TOPIC FOLDER: rglob for a folder whose 00-README.md exists with this slug.
    src_folder = None
    for p in wsd.rglob(TOPIC_README):
        folder = p.parent
        if folder.name == slug or (is_topic_folder(folder) and folder.name == slug):
            src_folder = folder
            break
    if src_folder is None:
        # v2.4 fallback: <slug>/<slug>.md folder-note layout
        for p in wsd.rglob(f"{slug}/{slug}.md"):
            src_folder = p.parent
            break
    if src_folder is None:
        print(f"skip: {slug} folder not found (no v3 00-README.md or v2.4 <slug>/<slug>.md)")
        continue
    dst_parent = wsd
    for part in new_parents:
        dst_parent = dst_parent / part
    dst_parent.mkdir(parents=True, exist_ok=True)
    dst_folder = dst_parent / slug
    if dst_folder != src_folder:
        if dst_folder.exists():
            print(f"skip: {slug} destination {dst_folder} already exists")
            continue
        # Move the whole topic folder (all aspects + lessons + README move together).
        shutil.move(str(src_folder), str(dst_folder))
    # Patch parents: in the 00-README.md (or v2.4 landing) frontmatter.
    readme = dst_folder / TOPIC_README
    if not readme.is_file():
        readme = dst_folder / f"{slug}.md"  # v2.4 fallback
    if readme.is_file():
        fm, body = parse_file(readme)
        fm["parents"] = new_parents
        atomic_write(readme, render(fm, body))
    moved.append((slug, dst_folder))
    print(f"moved: {slug} → {dst_folder.relative_to(Path.home() / '.gowth-mem')}")

print(f"total: {len(moved)} folder moves")
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
