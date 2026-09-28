---
description: "Vault ops umbrella (/mem-ops <sub>) — reindex, prune, forget, validate, more"
---

# /mem-ops <sub> [args]

Rarely typed operations live here so the command listing stays small (v4.8: 39 → 16
commands; the listing alone cost 9,153 chars of context per session). Each sub's full
instructions are a file Claude Code never lists.

**Do this:** read `${CLAUDE_PLUGIN_ROOT}/templates/ops/<sub>.md` and follow it, passing the
remaining arguments through verbatim (flags such as `--fix`, `--all`, `--dry-run` keep their
meaning from the file). No `<sub>`, or one not in the table: print the table and stop.

| Sub | What |
|---|---|
| budget | 4-tier weighted context plan for a query (working/episodic/semantic/procedural) |
| changelog | themed changelog rolled up from the memory repo's commit history |
| compress | rtk-style pre-storage compression of repeated lines |
| config | configure git remote, branch and token for the vault sync |
| dream | 3-phase dreaming consolidation (Light → REM → Deep) on one or all workspaces |
| forget | archive raw journal past its TTL (and aged aspects with --aspects) |
| gate | scan existing memory for entries that violate the write rules |
| journal | open or create today's journal entry in the vault |
| lint | heuristic contradiction lint across [ref]/[decision]/[tool] entries |
| migrate-global | migrate v1.0 per-workspace .gowth-mem folders into the global vault |
| migrate-v3 | migrate a v2.4 vault to the v3 topic-folder layout |
| promote | promote a topic's knowledge into a wiki page |
| prune | delete outdated, superseded or duplicate entries from the curated layer |
| reflect | Generative-Agents-style reflection over recent journal + docs/exp.md |
| reindex | build or refresh index.db (FTS5) over the vault |
| restructure | move topics under new parents, rebuild MOCs |
| retag | backfill deterministic #tags into aspect frontmatter |
| review-backlog | self-review past conversations never reviewed live |
| skillify | extract a recurring workflow from this session into a reusable skill |
| sync-resolve | AI-mediated conflict resolver for SYNC-CONFLICT.md (also /mem-sync resolve) |
| validate | file-level schema validator (--fix repairs frontmatter, --prune-junk removes junk) |
| verify | promote a verified [hypothesis] to a [ref] with Source, or mark it refuted |
