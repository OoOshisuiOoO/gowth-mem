---
description: Scaffold a new gowth-mem workspace from the workspace-skeleton template
argument-hint: "<name> [--title \"Title\"] [--description \"…\"]"
---

# /mem-workspace-create

Create a new workspace under `~/.gowth-mem/workspaces/<name>/` with full v3 skeleton:
- `workspace.json`, `_MAP.md` (workspace MOC, `layout_version: 3`)
- `AGENTS.md` (workspace-specific delta rules)
- `docs/{handoff,exp,ref,tools,files}.md` (reserved subdir)
- `journal/` (reserved subdir for daily logs)
- `skills/` (reserved subdir for workspace-specific skills)
- `research/` (reserved subdir for long-form research output, new in v3)
- `misc/00-README.md` (fallback topic folder — v3 layout, no inline entries)

## Steps

```bash
NAME="$1"; shift
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --title) ARGS+=(--title "$2"); shift 2 ;;
    --description) ARGS+=(--description "$2"); shift 2 ;;
    *) shift ;;
  esac
done

if [ -z "$NAME" ]; then
  echo "Usage: /mem-workspace-create <name> [--title …] [--description …]" >&2
  exit 1
fi

python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_workspace.py" create "$NAME" "${ARGS[@]}"
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_moc.py" --ws "$NAME"
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_index.py"
echo "Done. Switch with: /mem-workspace $NAME"
```

## After create

The new workspace contains a `misc/00-README.md` fallback topic folder (v3 layout — no inline entries; first routed line creates `misc/YYYY-MM-DD-note.md`) and an empty `_MAP.md`. Add a `workspace_map` glob if you want auto-detection from `cwd`:

```
/mem-workspace-map "/Volumes/Data/Git/<your-repo>/**" <name>
```
