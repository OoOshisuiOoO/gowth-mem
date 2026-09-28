---
description: "Deep-research campaigns — /mem-research start|distill|status"
---

# /mem-research <start|distill|status> [args]

Read `${CLAUDE_PLUGIN_ROOT}/templates/ops/research-<sub>.md` and follow it with the remaining
arguments verbatim. No sub → show this list and stop. The ops file's bash blocks are written against the plugin root as a dollar-brace shell expansion of CLAUDE_PLUGIN_ROOT. Claude Code substitutes that only in THIS command, never inside the ops file, and your shell does not export it — so run each of its commands with the prefix `CLAUDE_PLUGIN_ROOT='${CLAUDE_PLUGIN_ROOT}'` (or write that literal path in place of the expansion).

| Sub | What |
|---|---|
| start | scaffold a deep-research campaign on an external repo/system under `workspaces/<ws>/research/<topic>/` |
| distill | scaffold `distilled.md` (TL;DR / architecture / key facts / code anchors / delta vs current) for a topic |
| status | list research topics in the active workspace and their state |
