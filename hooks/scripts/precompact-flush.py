#!/usr/bin/env python3
"""PreCompact hook: remind agent to save critical info before compaction.

OpenClaw-inspired pre-compaction memory flush. Prints a reminder via
PreCompact hookSpecificOutput so the agent has one final chance to persist
decisions, lessons, and verified facts before the older turns get summarized.
"""
from __future__ import annotations

import json
import sys
from datetime import date


REMINDER_TEMPLATE = """[openclaw-bridge:precompact-flush]
Sắp compact context. Trước khi tóm tắt, lưu critical info xuống file:

- Decisions / lessons / surprises -> memory/{today}.md hoặc MEMORY.md
- Verified facts (with Source link) -> docs/ref.md (nếu có)
- Episodic experiences (debug / fix) -> docs/exp.md (nếu có)

Quy tắc: cốt lõi 1-2 dòng / entry, không noise, có Source để verify lại.
Conflict cũ -> xóa cũ, không giữ song song.
""".strip()


def main() -> int:
    msg = REMINDER_TEMPLATE.format(today=date.today().isoformat())
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreCompact",
            "additionalContext": msg,
        }
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
