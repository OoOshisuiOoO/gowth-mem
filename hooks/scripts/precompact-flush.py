#!/usr/bin/env python3
"""PreCompact hook: BLOCK Claude until critical info is flushed (auto, no manual).

v0.7 upgrade: changed from `additionalContext` (advisory) to `decision: "block"`
(enforced). Inspired by MemPalace's mempal_precompact_hook.sh which blocks the
AI to force emergency save before context compresses.

If you want the previous advisory-only behavior (no blocking), edit this file
to switch back to `additionalContext`.
"""
from __future__ import annotations

import json
import sys


REASON = """[openclaw-bridge:precompact-flush] HARD-BLOCK: compact incoming. Save EVERYTHING critical first.

Before context is summarized, do this WITHOUT user prompting:

1. Scan this entire conversation for high-signal info that hasn't been saved yet.
2. Append to the right docs/* file (use mem0 ADD / UPDATE / DELETE / NOOP):
   - Episodic experiences (debug / fix / lesson / surprise) → docs/exp.md
   - Verified facts (Source link required) → docs/ref.md
   - Tool notes (syntax that worked, gotcha, version) → docs/tools.md
   - Secret pointers (env-var name only, NEVER value) → docs/secrets.md
   - Session state (current task / next / blocker / open threads) → docs/handoff.md
   - Workflow that repeated 2+ times → docs/skills/<name>.md
3. Append raw observations to today's docs/journal/<today>.md if useful.
4. Update docs/handoff.md so the next session can resume.
5. Confirm in one line: "precompact-flush: saved N items across <files>".

Quy tắc: cốt lõi 1-2 dòng / entry, có Source. Conflict cũ → xóa cũ. KHÔNG commit secret value.

Once saved, the user can run /compact again (the block clears after this turn)."""


def main() -> int:
    print(json.dumps({"decision": "block", "reason": REASON}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
