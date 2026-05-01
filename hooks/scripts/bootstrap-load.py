#!/usr/bin/env python3
"""SessionStart hook: assemble AGENTS.md + docs/ working memory + recent journal.

Loads in this order (matches AI-trade bootstrap rule + adds journal layer):
  1. AGENTS.md            — operating rules
  2. docs/handoff.md      — session state
  3. docs/exp.md          — episodic curated
  4. docs/ref.md          — verified facts
  5. docs/tools.md        — tool registry
  6. docs/secrets.md      — resource pointers
  7. docs/files.md        — project structure
  8. docs/journal/<today>.md      — raw daily journal (layer 1)
  9. docs/journal/<yesterday>.md  — raw journal one day back

Caps: 12k char/file, 60k total. Skips blanks, marks truncations.

Long-term knowledge (wiki/topics/, wiki/concepts/) is loaded by claude-obsidian's
own SessionStart hook (which reads wiki/hot.md). This hook does not touch wiki/.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

MAX_PER_FILE = 12_000
MAX_TOTAL = 60_000


def main() -> int:
    workspace = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    today = date.today()
    yesterday = today - timedelta(days=1)

    candidates = [
        workspace / "AGENTS.md",
        workspace / "docs" / "handoff.md",
        workspace / "docs" / "exp.md",
        workspace / "docs" / "ref.md",
        workspace / "docs" / "tools.md",
        workspace / "docs" / "secrets.md",
        workspace / "docs" / "files.md",
        workspace / "docs" / "journal" / f"{today.isoformat()}.md",
        workspace / "docs" / "journal" / f"{yesterday.isoformat()}.md",
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
