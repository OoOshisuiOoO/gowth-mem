---
name: mem-save
description: Use when the user says "save this", "remember this", "note this", "ghi lại", "lưu", or after a debug session ends. Routes the entry to the right destination under ~/.gowth-mem/ — either a topic file (topics/<slug>.md) or a cross-topic registry (docs/handoff|secrets|tools.md). Topic routing uses keyword overlap.
---

# mem-save

Save a memory entry into the right file with the right structure under `~/.gowth-mem/`.

## Routing

| Type of info | Destination |
|---|---|
| Episodic experience (debug, fix, lesson, surprise, anti-pattern) | `topics/<slug>.md` as `- [exp] ...` |
| Verified semantic fact (with **Source** link) | `topics/<slug>.md` as `- [ref] ...` |
| Topic-specific tool quirk | `topics/<slug>.md` as `- [tool] ...` |
| Architectural decision + rationale | `topics/<slug>.md` as `- [decision] ...` |
| Lesson / takeaway / pattern | `topics/<slug>.md` as `- [reflection] ...` |
| Cross-topic tool quirk (applies broadly) | `docs/tools.md` |
| Resource pointer (env-var name; **never the value**) | `docs/secrets.md` |
| Session state (current task / next / blocker) | `docs/handoff.md` (prefix `host:<machine>`) |
| Reusable workflow (≥2× repeated) | `skills/<slug>.md` (use `memk`) |

## Topic routing

Use the helper to pick the right slug:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_topic.py" --route "<your text>"
```

The router:

1. Extracts ≥4-char keywords from your text (drop stopwords).
2. Scans `~/.gowth-mem/topics/*.md`; counts keyword overlap with each.
3. If max overlap ≥ `settings.topic_routing.min_keyword_overlap` (default 3) → that slug.
4. Else → new slug from top-2 distinctive keywords (kebab-case, ≤40 chars).
5. Else → `settings.topic_routing.default_topic` (default `misc`).

To create a new topic file with frontmatter:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_topic.py" --ensure "<slug>"
```

After writing entries, regenerate the topic index (cheap):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_topic.py" --regen-index
```

## Entry format

Each entry is 1–2 lines. No noise. Type prefix is mandatory.

```markdown
- [exp] <fact / decision / lesson> — <why or context, ≤1 line>
  Source: <URL | file:line | session date>
```

For `[ref]`, **Source is required**. Reject the entry if missing.

## Atomic writes

All writes go through `_atomic.atomic_write` so concurrent sessions don't corrupt files:

```python
import sys
sys.path.insert(0, "${CLAUDE_PLUGIN_ROOT}/hooks/scripts")
from _atomic import atomic_write
from pathlib import Path
target = Path.home() / ".gowth-mem" / "topics" / f"{slug}.md"
existing = target.read_text() if target.is_file() else ""
atomic_write(target, existing + new_entry + "\n")
```

## Steps

1. Determine the type using the routing table.
2. For topic content: route to a slug via `_topic.py --route`. Ensure the topic file exists.
3. Read the target. Apply mem0 ADD / UPDATE / DELETE / NOOP:
   - **ADD** if the entry is new and not redundant.
   - **UPDATE** if a similar entry exists with stale info → rewrite that line.
   - **DELETE** if a previous entry directly contradicts the new one (audit trail not needed for working memory).
   - **NOOP** if duplicate-equivalent.
4. Write atomically.
5. Report path + which mem0 op was applied.

## Hard rules

- **Never write a real secret value** to `docs/secrets.md` or anywhere. Only env-var names + how to obtain.
- **Never `mem-save` to `docs/handoff.md` mid-task** — wait until end of session or before `/compact`. Prefix the line with `host:<machine>`.
- **`[ref]` without a Source link is rejected.**
- **Conflict with existing entry** → DELETE the old (or mark `(superseded)` if audit matters), keep the new.
- **Cross-machine knowledge**: anything you save here syncs via `/mem-sync` (or auto on PostCompact). Don't put machine-specific paths in synced files; use `host:<machine>` prefix in `docs/handoff.md` if you must.
