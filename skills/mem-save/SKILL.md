---
name: mem-save
description: Use when the user says "save this", "remember this", "note this", "ghi lại", "lưu", or after a debug session ends. Routes the entry to the right destination under ~/.gowth-mem/ — file-per-topic in the ACTIVE workspace, or a cross-cutting registry (workspaces/<ws>/docs/* or shared/*). Topic routing uses keyword overlap.
---

# mem-save (v2.2)

Save a memory entry into the right file under `~/.gowth-mem/`. **Writes are scoped to the active workspace** unless the entry is genuinely cross-workspace.

## 1. Resolve active workspace first

```bash
WS=$(python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_workspace.py" active)
echo "active workspace: $WS"
```

If wrong, switch before continuing: `/mem-workspace <other>`.

## 2. Routing table

| Type | Destination | Section |
|---|---|---|
| Episodic experience (debug/fix/lesson/anti-pattern) | `workspaces/$WS/topics/<slug>.md` | `## [exp]` |
| Verified fact (Source REQUIRED) | `workspaces/$WS/topics/<slug>.md` | `## [ref]` |
| Topic-specific tool quirk | `workspaces/$WS/topics/<slug>.md` | `## [ref]` |
| Architectural decision + rationale | `workspaces/$WS/topics/<slug>.md` | `## [decision]` |
| Lesson / takeaway / pattern | `workspaces/$WS/topics/<slug>.md` | `## [exp]` (reflection group) |
| Cross-topic tool quirk (workspace-scoped) | `workspaces/$WS/docs/tools.md` | (flat) |
| Workspace overflow when no topic fits | `workspaces/$WS/docs/{exp,ref}.md` | (flat) |
| Session state (current task / next / blocker) | `workspaces/$WS/docs/handoff.md` | prefix `host:<machine>` |
| Reusable workflow (≥2× repeated) | `shared/skills/<slug>.md` (cross-ws) OR `workspaces/$WS/skills/<slug>.md` | (use `memk`) |
| **Resource pointer** (env-var name; never the value) | `shared/secrets.md` | (flat, **never workspace-scoped**) |
| **System tool** (used across workspaces) | `shared/tools.md` | (flat) |

## 3. Topic routing helper

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_topic.py" --route "<your text>" --ws "$WS"
```

Outputs `<slug>\t<file_path>\t<section_hint>`. If `<file_path>` doesn't exist, it'll be created on write with the v2.2 frontmatter scaffold.

To force-create a topic file with frontmatter:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_topic.py" --ensure "<slug>" --ws "$WS"
```

## 4. v2.2 topic file format

```markdown
---
slug: <slug>
title: <Title>
status: draft|active|distilled|archived
created: 2026-05-02
last_touched: 2026-05-02
parents: []
links: []
aliases: []
---

# <Title>

> Cốt lõi 1 dòng.

## [exp]
- 2026-04-15: <1-2 dòng> (Source: <reproducible>)

## [ref]
- <fact> (Source: <url|file:line|doc>)

## [decision]
- Chọn X over Y because Z (Source: …)

## [reflection]
- Pattern observation. Cross-link: [[other-slug]] hoặc [[other-ws:other-slug]].
```

## 5. Entry format (per line)

Each entry is 1–2 lines. No noise. **`Source:` mandatory for `[ref]`.** Reject if missing.

## 6. Atomic writes

```python
import sys
sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}/hooks/scripts")
from _atomic import atomic_write
from _frontmatter import parse_file, render
from _home import topics_dir, active_workspace
from datetime import date

ws = active_workspace()
target = topics_dir(ws) / "<slug>.md"
fm, body = parse_file(target)
fm["last_touched"] = date.today().isoformat()
# … insert new entry under the right `## [exp]` / `## [ref]` heading in `body` …
atomic_write(target, render(fm, body))
```

## 7. After write — refresh MOC + index

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_moc.py" --ws "$WS"
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_index.py"
```

## 8. Steps (the actual save flow)

1. `WS=$(_workspace.py active)`. Confirm in output.
2. Determine type per routing table.
3. For topic content: route via `_topic.py --route ... --ws $WS`. Ensure topic file exists (frontmatter scaffold).
4. Read target. Apply mem0 ADD / UPDATE / DELETE / NOOP:
   - **ADD** if new and not redundant.
   - **UPDATE** if a similar entry exists with stale info → rewrite that line.
   - **DELETE** if directly contradicts new entry.
   - **NOOP** if duplicate.
5. Update frontmatter `last_touched` to today.
6. Write atomically.
7. Run `_moc.py --ws $WS` and `_index.py` (cheap).
8. Report: `path / op / workspace`.

## 9. Cross-workspace explicit references

Use `[[<workspace>:<slug>]]` syntax:
- `[[trade:ema-cross]]` — references workspace `trade`
- `[[shared:secrets]]` — references shared registry

Plain `[[slug]]` resolves in the **current** workspace only.

## 10. Hard rules

- **Never write a real secret value** to any file. `shared/secrets.md` is POINTER only (env-var name + how to obtain).
- **Never `mem-save` to handoff.md mid-task** — wait until end of session or before `/compact`. Prefix with `host:<machine>`.
- **`[ref]` without Source link → reject.**
- **Conflict with existing entry** → DELETE old (or mark `(superseded)` if audit matters), keep new.
- **Slug collisions across workspaces are OK** — slug uniqueness is per-workspace.
- **Never change a slug** that's been published — wikilinks will break. Use `/mem-restructure` to change `parents:` instead.
- **Cross-workspace writes** require explicit workspace switch (`/mem-workspace <other>`) — don't write to a non-active workspace silently.
