---
description: Save a memory entry into the right file under ~/.gowth-mem/. Routes to a topic file in the active workspace, or to a cross-cutting registry (workspaces/<ws>/docs/* or shared/*) when the entry is genuinely cross-workspace.
---

Invoke the `mem-save` skill to capture the current entry. Writes are scoped to the active workspace unless the entry is genuinely cross-workspace (secrets, system tools).

The skill will:

1. Resolve the active workspace via `_workspace.py active`.
2. Classify the entry by type (episodic / verified fact / tool quirk / decision / lesson / pointer / session-state / reusable workflow).
3. Route to the destination per the v2.6 routing table:
   - Topic content → `workspaces/<ws>/<slug>/<slug>.md` under the matching section. New topics scaffold from a type-specific template (`type: <runbook|incident|reference|research|strategy|how-to|concept|decision|tool|misc>` in frontmatter — see `/mem-topic --ensure --type=<type>`). For existing topics, write under the matching heading from that template; for legacy `misc` skeletons, use `[exp] | [ref] | [decision] | [reflection]`.
   - Lesson (Symptom + Tried + Root + Fix [+ Source]) → `workspaces/<ws>/<topic>/lessons.md` (use `memL` for the dedicated 5-field path).
   - Cross-topic tool quirk → `workspaces/<ws>/docs/tools.md`.
   - Workspace overflow → `workspaces/<ws>/docs/{exp,ref}.md`.
   - Session state → `workspaces/<ws>/docs/handoff.md` (prefix `host:<machine>`).
   - Secret pointer (env-var name, NEVER value) → `shared/secrets.md`.
   - System tool (cross-workspace) → `shared/tools.md`.
   - Reusable workflow (≥2× repeated) → `shared/skills/<slug>.md` or `workspaces/<ws>/skills/<slug>.md` (use `memk`).
4. Apply mem0 ADD / UPDATE / DELETE / NOOP against the target.
5. Update frontmatter `last_touched` to today.
6. Atomic write, then refresh MOC + index (`_moc.py --ws <ws>` + `_index.py`).
7. Report: `path / op / workspace`.

Hard rules: `[ref]` requires `Source:` (reject otherwise); never write a real secret value; conflict with existing entry → DELETE old (or mark `(superseded)`); cross-workspace writes require explicit `/mem-workspace <other>` switch.

For dedicated lesson capture (5-field schema), prefer `/mem-lesson` or `memL` shortcut.
