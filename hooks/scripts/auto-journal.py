#!/usr/bin/env python3
"""Stop hook (v3.4): auto-distill + auto-prune every N user turns, scoped to the
active workspace.

State lives in global ~/.gowth-mem/state.json (per-machine, gitignored).
Updates are protected by file_lock("state") for multi-session safety.

v3.4 changes:
  - REASON externalized to templates/auto-journal-instructions.md (pointer injected)
  - journal_every read from settings.json (default 10)
  - auto_journal_enabled toggle in settings.json (default true)
  - Skip in subagent context: CLAUDE_SUBAGENT env or stdin agent_type == "subagent"
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import atomic_write  # type: ignore
from _debug import log_debug  # type: ignore
from _home import active_workspace, gowth_home, list_workspaces, read_settings, state_path  # type: ignore
from _lock import file_lock  # type: ignore

AUTO_DISTILL_EVERY = 10  # fallback default; overridden by settings.json journal_every


def _load_state() -> dict:
    p = state_path()
    if not p.is_file():
        return {"version": 2, "files": {}, "session": {}}
    try:
        d = json.loads(p.read_text())
        d.setdefault("files", {})
        d.setdefault("session", {})
        return d
    except Exception as e:
        log_debug("auto-journal", f"load_state failed: {e}")
        return {"version": 2, "files": {}, "session": {}}


def _save_state(state: dict) -> None:
    try:
        gowth_home().mkdir(parents=True, exist_ok=True)
        atomic_write(state_path(), json.dumps(state, indent=2))
    except Exception as e:
        log_debug("auto-journal", f"save_state failed: {e}")


def _read_journal_settings() -> tuple[int, bool]:
    """Return (journal_every, auto_journal_enabled) from settings.json."""
    try:
        settings = read_settings()
        aj = settings.get("auto_journal", {}) if isinstance(settings, dict) else {}
        every = int(aj.get("journal_every", settings.get("journal_every", AUTO_DISTILL_EVERY)))
        enabled = bool(aj.get("auto_journal_enabled", settings.get("auto_journal_enabled", True)))
        return every, enabled
    except Exception:
        return AUTO_DISTILL_EVERY, True


def _is_subagent(data: dict) -> bool:
    """Return True if this Stop event is from a subagent context — skip journaling.

    Detection layers (any one sufficient):
      - CLAUDE_SUBAGENT env (opt-in for users running custom subagent shells)
      - stdin.agent_type == "subagent"  (Claude Code legacy signal)
      - stdin.hook_event_name == "SubagentStop"  (current Claude Code signal)
      - stdin.in_loop is truthy  (ralph/ultrawork loops)
    """
    if os.environ.get("CLAUDE_SUBAGENT"):
        return True
    if not isinstance(data, dict):
        return False
    if data.get("agent_type") == "subagent":
        return True
    if data.get("hook_event_name") == "SubagentStop":
        return True
    if data.get("in_loop"):
        return True
    return False


def _build_reason(ws: str, journal_every: int) -> str:
    """Build the journal reason string: short pointer + TL;DR (≤400 chars total)."""
    instructions_path = Path(__file__).parent.parent.parent / "templates" / "auto-journal-instructions.md"
    return (
        f"[gowth-mem:auto-journal ws={ws}] {journal_every} turns elapsed. "
        f"Read {instructions_path} for the full protocol, then update journal. "
        f"TL;DR: classify items as [decision]/[exp]/[ref]/[tool]/[secret-ref], "
        f"route to topic folders, apply quality gates, update handoff.md."
    )


def main() -> int:
    try:
        raw_stdin = sys.stdin.read()
    except Exception:
        raw_stdin = ""

    try:
        data = json.loads(raw_stdin) if raw_stdin.strip() else {}
    except Exception:
        data = {}

    # v3.4: skip in subagent context (no double-journaling under ralph/ultrawork)
    if _is_subagent(data):
        return 0

    # v3.4: respect auto_journal_enabled toggle
    journal_every, enabled = _read_journal_settings()
    if not enabled:
        return 0

    session_id = data.get("session_id") or "default"

    try:
        with file_lock("state", timeout=5.0):
            state = _load_state()
            sess = state["session"].setdefault(session_id, {"turn_count": 0})
            sess["turn_count"] = sess.get("turn_count", 0) + 1
            turn = sess["turn_count"]
            _save_state(state)
    except TimeoutError as e:
        log_debug("auto-journal", f"state lock timeout (increment): {e}")
        print(json.dumps({"continue": True, "suppressOutput": True}))
        return 0

    if turn < journal_every:
        print(json.dumps({"continue": True, "suppressOutput": True}))
        return 0

    try:
        with file_lock("state", timeout=5.0):
            state = _load_state()
            state["session"].setdefault(session_id, {"turn_count": 0})["turn_count"] = 0
            _save_state(state)
    except TimeoutError as e:
        log_debug("auto-journal", f"state lock timeout (reset): {e}")

    # Side-effect: best-effort prune + consolidate. Output is intentionally
    # not embedded in the reason — agent reads the externalized template
    # (Pattern 3: externalize long context, inject pointer only).
    prune_script = Path(__file__).parent / "_prune.py"
    if prune_script.is_file():
        try:
            subprocess.run(
                ["python3", str(prune_script), "--all-workspaces"],
                capture_output=True, text=True, timeout=8,
            )
        except subprocess.TimeoutExpired as e:
            log_debug("auto-journal", f"prune subprocess timeout after 8s: {e}")
        except Exception as e:
            log_debug("auto-journal", f"prune subprocess failed: {e}")

    consolidate_script = Path(__file__).parent / "_consolidate.py"
    if consolidate_script.is_file():
        try:
            subprocess.run(
                ["python3", str(consolidate_script)],
                capture_output=True, text=True, timeout=8,
            )
        except subprocess.TimeoutExpired as e:
            log_debug("auto-journal", f"consolidate subprocess timeout after 8s: {e}")
        except Exception as e:
            log_debug("auto-journal", f"consolidate subprocess failed: {e}")

    ws = active_workspace()
    reason = _build_reason(ws, journal_every)

    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


if __name__ == "__main__":
    # stdin is consumed inside main() via sys.stdin.read()
    sys.exit(main())
