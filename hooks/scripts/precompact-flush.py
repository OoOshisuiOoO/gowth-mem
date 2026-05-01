#!/usr/bin/env python3
"""PreCompact hook (v2.0): block until critical info is flushed to topics.

Routes destinations for topic-organized memory at ~/.gowth-mem/.
"""
from __future__ import annotations

import json
import sys


REASON = """[gowth-mem:precompact-flush] HARD-BLOCK: compact incoming. Save EVERYTHING critical first.

Before context is summarized, do this WITHOUT user prompting:

1. Scan this entire conversation for high-signal info that hasn't been saved yet.
2. Route each item to its destination under ~/.gowth-mem/ (apply mem0 ADD/UPDATE/DELETE/NOOP):
   - Cross-topic registries (flat):
     - Session state (current task / next / blocker) → docs/handoff.md (prefix host:<name>)
     - Resource pointers (env-var name only, NEVER value) → docs/secrets.md
     - Cross-topic tool quirks → docs/tools.md
   - Topic content — pick or create the right topic file:
     - Find existing topics/<slug>.md whose keywords overlap (≥3 common words). Append there.
     - Else create topics/<new-slug>.md (top-2 distinctive keywords as slug).
     - Episodic experience → `- [exp] ...`
     - Verified fact (Source URL required) → `- [ref] ...`
     - Tool quirk specific to this topic → `- [tool] ...`
     - Architectural decision → `- [decision] ...`
     - Lesson learned → `- [reflection] ...`
3. Append raw observations to today's journal/<today>.md if useful.
4. Update docs/handoff.md so the next session can resume.
5. Confirm in one line: "precompact-flush: saved N items into <topics+docs>".

Quy tắc: 1-2 dòng / entry, có Source cho [ref]. Conflict cũ → DELETE cũ. KHÔNG commit secret value.

After this turn, the PostCompact hook will pull-rebase-push automatically.
Once saved, the user can run /compact again (the block clears after this turn)."""


def main() -> int:
    print(json.dumps({"decision": "block", "reason": REASON}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
