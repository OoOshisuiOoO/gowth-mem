#!/usr/bin/env python3
"""Stop hook: auto-distill + auto-prune every N user turns. Blocks Claude until done.

v0.9: in addition to instructing distill, the auto-journal cycle now also runs
the active prune helper (`_prune.py`) automatically before yielding. This keeps
docs/* lean per user direction (outdated knowledge → DELETE, not just mark).

Algorithm:
  1. Read session_id from stdin.
  2. Increment turn counter in .gowth-mem/state.json under session[id].turn_count.
  3. If turn_count % AUTO_DISTILL_EVERY == 0:
       - Run `_prune.py` synchronously (deletes superseded/expired/dup entries).
       - Emit decision=block with detailed instructions for Claude to:
           * Scan recent decisions / lessons / surprises / pains.
           * Apply 5-type strict schema with [type] prefix.
           * Promote signal entries via mem0 ADD/UPDATE/DELETE/NOOP.
  4. Otherwise → silent (no block).
  5. Reset counter to 0 right before block.

This replaces manual `/mem-distill` AND `/mem-prune` invocations.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

AUTO_DISTILL_EVERY = 10


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

    try:
        state_path.parent.mkdir(exist_ok=True)
        state_path.write_text(json.dumps(state, indent=2))
    except Exception:
        pass

    if turn < AUTO_DISTILL_EVERY:
        print(json.dumps({"continue": True, "suppressOutput": True}))
        return 0

    sess["turn_count"] = 0
    try:
        state_path.write_text(json.dumps(state, indent=2))
    except Exception:
        pass

    # Run active prune synchronously (best-effort).
    prune_summary = ""
    prune_script = Path(__file__).parent / "_prune.py"
    if prune_script.is_file():
        try:
            r = subprocess.run(
                ["python3", str(prune_script), "--workspace", str(workspace)],
                capture_output=True, text=True, timeout=10,
            )
            prune_summary = (r.stdout or "").strip().splitlines()[0] if r.stdout else ""
        except Exception:
            pass

    today = date.today().isoformat()
    reason = f"""[openclaw-bridge:auto-journal] {AUTO_DISTILL_EVERY} turns elapsed.

Pre-block prune ran: {prune_summary or '(no prune output)'}

Now do this WITHOUT user prompting before yielding control:

1. Scan the last {AUTO_DISTILL_EVERY} user turns and your replies.
2. For each high-signal item, classify into ONE of these 7 types and prepend the prefix:
   [decision]    choice + rationale          → docs/exp.md
   [preference]  always X / never Y          → docs/exp.md
   [milestone]   working solution            → docs/exp.md
   [problem]     bug / failure / fix         → docs/exp.md
   [fact]        verified external fact      → docs/ref.md (Source REQUIRED)
   [tool]        syntax / gotcha / version   → docs/tools.md
   [secret-ref]  env-var / path POINTER      → docs/secrets.md (NEVER value)
3. Apply quality gates — DROP if:
   - Entry < 20 chars
   - Code-only (no prose)
   - [fact] without Source
   - Vague / hedged ("maybe", "I think") without backing
4. Apply mem0 ADD / UPDATE / DELETE / NOOP against existing target file content.
5. Conflict with existing entry → DELETE old, ADD new (or mark `(superseded)` for next prune).
6. Confirm in 1 line: "auto-journal: kept N, dropped M, promoted K, conflicts resolved J".

Don't write the user a long message about this — just do the work silently and continue.
This is automation, not a conversation step."""

    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
