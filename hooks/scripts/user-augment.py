#!/usr/bin/env python3
"""UserPromptSubmit hook: shortcut expansion + intent hint (user-prompt augmentation).

Detects shortcuts in the prompt (@today, @yesterday, @ws, @user) and tells
Claude how to expand them. Detects intent prefixes (review:, fix:, save:,
research:, plan:) and injects a role-specific reminder.

Claude Code hooks cannot rewrite the user's text directly. This hook operates
through `hookSpecificOutput.additionalContext`, which appears as a system
reminder alongside the prompt.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

INTENT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^\s*(review|critique)\b", re.I),
     "intent=review: examine and point out flaws, do not implement unless asked."),
    (re.compile(r"^\s*(fix|debug|repair|sửa)\b", re.I),
     "intent=fix: root-cause first, minimal diff, verify before claiming done."),
    (re.compile(r"^\s*(save|remember|note|nhớ|lưu|ghi)\b", re.I),
     "intent=save: invoke the mem-save skill to persist the entry."),
    (re.compile(r"^\s*(research|find|investigate|explain|tìm|nghiên cứu)\b", re.I),
     "intent=research: read first, no edits, cite sources, save findings."),
    (re.compile(r"^\s*(plan|design|architect|kế hoạch)\b", re.I),
     "intent=plan: produce structure, list steps, do not implement yet."),
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

    intent_msg: str | None = None
    for pattern, msg in INTENT_PATTERNS:
        if pattern.search(prompt):
            intent_msg = msg
            break

    if not expansions and not intent_msg:
        return 0

    parts = ["[openclaw-bridge:user-augment]"]
    if expansions:
        parts.append("Expand these shortcuts when interpreting the user prompt:")
        for k, v in expansions.items():
            parts.append(f"- {k} -> {v}")
    if intent_msg:
        parts.append("")
        parts.append(intent_msg)

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
