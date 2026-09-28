---
description: "Deep-research campaigns — /mem-research start|distill|status"
---

# /mem-research <start|distill|status> [args]

Read `${CLAUDE_PLUGIN_ROOT}/templates/ops/research-<sub>.md` and follow it with the remaining
arguments verbatim. No sub → show this list and stop.

| Sub | What |
|---|---|
| start | scaffold a deep-research campaign on an external repo/system under `workspaces/<ws>/research/<topic>/` |
| distill | scaffold `distilled.md` (TL;DR / architecture / key facts / code anchors / delta vs current) for a topic |
| status | list research topics in the active workspace and their state |
