#!/usr/bin/env python3
"""UserPromptSubmit hook: shortcut expansion + intent-driven auto-skill injection.

v0.7 upgrade: instead of just hinting at intent, the hook now injects FULL
inline instructions for the matching skill. Claude executes the skill behavior
without the user typing `/mem-save` / `/mem-skillify` / `/mem-reflect` etc.

Detected intents (English + Vietnamese):
- save / lưu / nhớ / ghi          → inline mem-save instructions (route by type)
- skillify / skill này / làm thành skill / lặp lại workflow → inline mem-skillify
- reflect / tổng kết / tóm tắt / recap / suy ngẫm           → inline mem-reflect
- bootstrap / where am I / đang làm gì / status              → inline 3-line summary
- review / critique  → still nudges only (review is interactive)
- fix / debug / sửa  → still nudges only
- research / find / nghiên cứu → still nudges only
- plan / thiết kế / kế hoạch → still nudges only

Shortcut expansions (unchanged from v0.6):
- @today, @yesterday, @ws, @user, @hot
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path


# Inline skill-content for auto-trigger paths. Kept short — Claude has the full
# skill body via Skill tool if it needs more. These are the action-oriented
# crib-notes the hook injects so the user doesn't need to type a slash command.

INLINE_MEM_SAVE = """[auto-skill: mem-save] User intent = save/lưu/nhớ/ghi. Execute now without /mem-save:

Routing table (1-2 lines / entry, Source required for facts):
| Episodic (debug, fix, lesson, surprise, anti-pattern) | docs/exp.md |
| Verified semantic fact (with Source URL) | docs/ref.md |
| Tool syntax / gotcha / version | docs/tools.md |
| Resource pointer (env-var name; NEVER value) | docs/secrets.md |
| Session state (current task / next / blocker) | docs/handoff.md |
| Workflow done 2+ times | docs/skills/<name>.md (use mem-skillify) |

Apply mem0 ADD / UPDATE / DELETE / NOOP. Conflict cũ → xóa cũ. Confirm path written."""

INLINE_MEM_SKILLIFY = """[auto-skill: mem-skillify] User intent = skill / repeat workflow. Execute now without /mem-skillify:

1. Identify the recurring workflow's core steps (parameterize variables).
2. Pick a kebab-case <name> ≤30 chars.
3. mkdir -p docs/skills if missing.
4. Write docs/skills/<name>.md with frontmatter (name, description, created, inputs)
   and sections: Description / Steps (parameterized) / Variations / Token cost / Source.
5. Confirm path. Suggest invocation: `do <name> for <input>`."""

INLINE_MEM_REFLECT = """[auto-skill: mem-reflect] User intent = reflect/tổng kết. Execute now without /mem-reflect:

1. Read docs/journal/*.md from last 7 days + docs/exp.md.
2. Score entries by importance × recency × novelty.
3. Pick top 3 patterns (clusters of related entries).
4. Append to docs/exp.md § Reflections with format:
   ### YYYY-MM-DD: <title>
   **Claim**: <evergreen 1-line>
   **Evidence**: <file:line refs (≥2)>
   **Implication**: <1-line action>
5. Suggest /save (claude-obsidian) for portable reflections.
NEVER invent — every reflection cites ≥2 source entries."""

INLINE_MEM_BOOTSTRAP = """[auto-skill: mem-bootstrap] User intent = where am I / status. Execute now without /mem-bootstrap:

Read docs/handoff.md. Emit EXACTLY 3 lines for the user:
1. **đang làm gì**: <Current task — 1 line>
2. **step kế**: <Next step — 1 line>
3. **blocker**: <Blocker — 1 line, or `không`>

If docs/handoff.md is missing or empty, say so and suggest /mem-init."""


# (regex, message_or_inline_block, is_inline_skill)
INTENT_PATTERNS: list[tuple[re.Pattern[str], str, bool]] = [
    # Auto-skill triggers
    (re.compile(r"\b(save\s+this|save\s+it|remember\s+this|note\s+this|lưu\b|nhớ\b|ghi\b)", re.I),
     INLINE_MEM_SAVE, True),
    (re.compile(r"\b(skill|skillify|làm\s+thành\s+skill|lặp\s+lại\s+workflow|reusable)\b", re.I),
     INLINE_MEM_SKILLIFY, True),
    (re.compile(r"\b(reflect|tổng\s+kết|tóm\s+tắt|recap|suy\s+ngẫm)\b", re.I),
     INLINE_MEM_REFLECT, True),
    (re.compile(r"^\s*(bootstrap|where\s+am\s+i|đang\s+làm\s+gì|status)\b", re.I),
     INLINE_MEM_BOOTSTRAP, True),
    # Plain nudges (no inline skill)
    (re.compile(r"^\s*(review|critique)\b", re.I),
     "intent=review: examine and point out flaws, do not implement unless asked.", False),
    (re.compile(r"^\s*(fix|debug|repair|sửa)\b", re.I),
     "intent=fix: root-cause first, minimal diff, verify before claiming done.", False),
    (re.compile(r"^\s*(research|find|investigate|explain|tìm|nghiên\s+cứu)\b", re.I),
     "intent=research: read first, no edits, cite sources, save findings to docs/ref.md.", False),
    (re.compile(r"^\s*(plan|design|architect|kế\s+hoạch|thiết\s+kế)\b", re.I),
     "intent=plan: produce structure, list steps, do not implement yet.", False),
]


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return 0

    workspace = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    today = date.today()
    yesterday = today - timedelta(days=1)
    user = os.environ.get("USER") or os.environ.get("USERNAME") or "user"

    expansions: dict[str, str] = {}
    if re.search(r"@today\b", prompt):
        expansions["@today"] = today.isoformat()
    if re.search(r"@yesterday\b", prompt):
        expansions["@yesterday"] = yesterday.isoformat()
    if re.search(r"@ws\b|@workspace\b", prompt):
        expansions["@ws / @workspace"] = str(workspace)
    if re.search(r"@user\b", prompt):
        expansions["@user"] = user
    if re.search(r"@hot\b", prompt):
        hot = workspace / "wiki" / "hot.md"
        if hot.is_file():
            expansions["@hot"] = f"read {hot.relative_to(workspace)} (claude-obsidian hot cache)"
        else:
            expansions["@hot"] = "wiki/hot.md not found"

    triggered_block: str | None = None
    nudge: str | None = None
    for pattern, payload, is_inline in INTENT_PATTERNS:
        if pattern.search(prompt):
            if is_inline:
                triggered_block = payload
            else:
                nudge = payload
            break

    if not expansions and triggered_block is None and nudge is None:
        return 0

    parts = ["[openclaw-bridge:user-augment]"]
    if expansions:
        parts.append("Shortcuts:")
        for k, v in expansions.items():
            parts.append(f"- {k} -> {v}")
    if triggered_block:
        parts.append("")
        parts.append(triggered_block)
    elif nudge:
        parts.append("")
        parts.append(nudge)

    out = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "\n".join(parts),
        }
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
