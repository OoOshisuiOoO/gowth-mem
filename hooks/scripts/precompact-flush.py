#!/usr/bin/env python3
"""PreCompact hook: remind agent to save critical info before compaction.

OpenClaw-inspired pre-compaction memory flush. Prints a reminder via
PreCompact hookSpecificOutput so the agent has one final chance to persist
decisions, lessons, and verified facts before older turns get summarized.

Wording matches the AI-trade taxonomy convention: route info to the right
docs/* file by type (exp / ref / tools / secrets / handoff).
"""
from __future__ import annotations

import json
import sys


REMINDER = """[openclaw-bridge:precompact-flush]
Sắp compact context. Trước khi tóm tắt, lưu critical info xuống đúng file:

- Episodic experiences (debug / fix / lesson / surprise) -> docs/exp.md
- Verified facts (with Source link) -> docs/ref.md
- Tool notes (cú pháp đã work, gotcha, version) -> docs/tools.md
- Secret pointers (env-var name only, KHÔNG value) -> docs/secrets.md
- Session state (đang làm / next / blocker / open threads) -> docs/handoff.md
- Long-term knowledge (concepts, methods cross-session) -> /save vào wiki/ qua claude-obsidian

Quy tắc: cốt lõi 1-2 dòng / entry, không noise, có Source để verify lại.
Conflict cũ -> xóa cũ, không giữ song song.
""".strip()


def main() -> int:
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreCompact",
            "additionalContext": REMINDER,
        }
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
