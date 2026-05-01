#!/usr/bin/env python3
"""UserPromptSubmit hook: if ~/.gowth-mem/SYNC-CONFLICT.md exists, inject a
reminder so Claude prompts the user to run /mem-sync-resolve before doing
other work.

Reads stdin (Claude Code's prompt JSON) and writes the standard hook output
JSON with `additionalContext`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _home import conflict_md  # type: ignore


def main() -> int:
    cm = conflict_md()
    if not cm.is_file():
        return 0

    try:
        head = cm.read_text(errors="ignore").splitlines()[:30]
    except Exception:
        head = []

    summary = "\n".join(head)
    msg = (
        "[gowth-mem SYNC-CONFLICT pending]\n\n"
        f"~/.gowth-mem/SYNC-CONFLICT.md exists. Resolve before continuing other work.\n"
        f"Run /mem-sync-resolve to walk each conflicted file and apply the user's choice.\n\n"
        "Preview (first 30 lines):\n"
        "```\n"
        f"{summary}\n"
        "```\n"
    )

    out = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": msg,
        }
    }
    sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    try:
        # Discard stdin (we don't need the user's prompt to decide).
        sys.stdin.read()
    except Exception:
        pass
    sys.exit(main())
