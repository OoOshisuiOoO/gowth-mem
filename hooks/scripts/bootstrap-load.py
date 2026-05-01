#!/usr/bin/env python3
"""SessionStart hook: assemble AGENTS.md + docs/ working memory.

Loads AGENTS.md (operating rules) + 6 docs/ files in AI-trade bootstrap order:
handoff.md, exp.md, ref.md, tools.md, secrets.md, files.md. Capped per file
(12k char) and total (60k char). Skips blanks, marks truncations.

For long-term knowledge (wiki/), claude-obsidian's own SessionStart hook
loads wiki/hot.md — this hook does NOT touch wiki/ to avoid duplication.

Output: JSON to stdout in the SessionStart hookSpecificOutput shape.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

MAX_PER_FILE = 12_000
MAX_TOTAL = 60_000


def main() -> int:
    workspace = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())

    candidates = [
        workspace / "AGENTS.md",
        workspace / "docs" / "handoff.md",
        workspace / "docs" / "exp.md",
        workspace / "docs" / "ref.md",
        workspace / "docs" / "tools.md",
        workspace / "docs" / "secrets.md",
        workspace / "docs" / "files.md",
    ]

    parts: list[str] = []
    total = 0
    stop = False
    for f in candidates:
        if stop:
            break
        if not f.is_file():
            continue
        try:
            raw = f.read_text(errors="ignore")
        except Exception:
            continue
        if not raw.strip():
            continue
        truncated_file = len(raw) > MAX_PER_FILE
        chunk = raw[:MAX_PER_FILE]
        room = MAX_TOTAL - total
        if room <= 200:
            break
        truncated_total = False
        if len(chunk) > room:
            chunk = chunk[:room]
            truncated_total = True
            stop = True
        rel = f.relative_to(workspace)
        marker = f"\n[truncated, see {rel}]" if (truncated_file or truncated_total) else ""
        parts.append(f"\n=== {rel} ===\n{chunk}{marker}")
        total += len(chunk)

    if not parts:
        return 0

    out = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "[openclaw-bridge:bootstrap]" + "".join(parts),
        }
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
