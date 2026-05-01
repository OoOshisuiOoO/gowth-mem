#!/usr/bin/env python3
"""SessionStart hook: inject dynamic runtime context (system-prompt augmentation).

Distinct from bootstrap-load.py (which loads static role files). This hook
adds runtime info that changes per session: workspace path, git branch + dirty
state, host, OS, current date / time / timezone, and the contents of
`.claude/directives.md` if it exists.

Claude Code hooks cannot rewrite the system prompt outright; this is the
closest official mechanism — `hookSpecificOutput.additionalContext` is delivered
as a system reminder at the start of the conversation.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DIRECTIVES_MAX_CHARS = 4000


def git_info(workspace: Path) -> str:
    try:
        branch = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip()
        if not branch:
            return ""
        status = subprocess.run(
            ["git", "-C", str(workspace), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout
        dirty = " (dirty)" if status.strip() else ""
        return f"{branch}{dirty}"
    except Exception:
        return ""


def main() -> int:
    workspace = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    now = datetime.now(timezone.utc).astimezone()
    info = [
        f"- workspace: {workspace}",
        f"- host: {platform.node()}",
        f"- os: {platform.system()} {platform.release()}",
        f"- now: {now.strftime('%Y-%m-%d %H:%M %Z')}",
    ]
    branch = git_info(workspace)
    if branch:
        info.append(f"- git: {branch}")

    parts = ["[gowth-mem:system-augment] runtime:"] + info

    directives = workspace / ".claude" / "directives.md"
    if directives.is_file():
        try:
            content = directives.read_text(errors="ignore")[:DIRECTIVES_MAX_CHARS]
            if content.strip():
                parts.append("\n## .claude/directives.md (project always-on rules)")
                parts.append(content)
        except Exception:
            pass

    out = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "\n".join(parts),
        }
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
