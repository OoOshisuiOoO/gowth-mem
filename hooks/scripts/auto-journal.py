#!/usr/bin/env python3
"""Stop hook: auto-distill every N user turns. Blocks Claude until journal is digested.

Inspired by MemPalace's mempal_save_hook.sh which fires every 15 user messages
and blocks the AI to save topics/decisions/quotes.

Algorithm:
  1. Read session_id from stdin.
  2. Increment turn counter in .gowth-mem/state.json under session[id].turn_count.
  3. If turn_count % AUTO_DISTILL_EVERY == 0 → emit decision=block with detailed
     instructions for Claude to:
       - Scan recent decisions / lessons / surprises / pains from this session.
       - Append signal entries to docs/journal/<today>.md.
       - Apply mem0 ADD/UPDATE/DELETE/NOOP to docs/exp.md / ref.md / tools.md.
  4. Otherwise → silent (no block).
  5. Reset counter to 0 right before block so Claude only triggers once.

This replaces manual `/mem-distill`. User never has to type it.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

AUTO_DISTILL_EVERY = 10  # block every N user turns


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    session_id = data.get("session_id") or "default"
    workspace = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    state_path = workspace / ".gowth-mem" / "state.json"

    state = {"version": 1, "files": {}, "session": {}}
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text())
            state.setdefault("files", {})
            state.setdefault("session", {})
        except Exception:
            pass

    sess = state["session"].setdefault(session_id, {"turn_count": 0})
    sess["turn_count"] = sess.get("turn_count", 0) + 1
    turn = sess["turn_count"]

    # Persist incremented counter (best-effort; failures are non-critical)
    try:
        state_path.parent.mkdir(exist_ok=True)
        state_path.write_text(json.dumps(state, indent=2))
    except Exception:
        pass

    # Only block when threshold reached.
    if turn < AUTO_DISTILL_EVERY:
        print(json.dumps({"continue": True, "suppressOutput": True}))
        return 0

    # Reset counter so the next 10 turns can accumulate before next auto-distill.
    sess["turn_count"] = 0
    try:
        state_path.write_text(json.dumps(state, indent=2))
    except Exception:
        pass

    today = date.today().isoformat()
    reason = f"""[openclaw-bridge:auto-journal] {AUTO_DISTILL_EVERY} turns elapsed — auto-distill before stopping.

Before yielding control, do this WITHOUT user prompting:

1. Scan the last {AUTO_DISTILL_EVERY} user turns and your replies.
2. Extract any:
   - Decisions made (what was chosen, why)
   - Lessons learned (what failed, what worked, surprise)
   - Verified facts with Source links
   - Tool notes (syntax that worked, gotcha)
   - Resource pointers (env-var names, file paths — NEVER values)
3. Append signal entries to `docs/journal/{today}.md` under correct sections (Logs / Wins / Pains / Questions).
4. Apply mem0-style **ADD / UPDATE / DELETE / NOOP** to promote any high-signal entries already in journal into:
   - `docs/exp.md` for episodic lessons
   - `docs/ref.md` for verified facts (require Source link)
   - `docs/tools.md` for tool notes
5. Drop pure chatter / noise.
6. Confirm with one line: "auto-journal: kept N, dropped M, promoted K".

Don't write the user a long message about this — just do the saves silently and continue.
This is automation, not a conversation step."""

    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
