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

v4.0 changes (metacognition — .claude/research/v4.0-metacognition.md §3/§4):
  - Reads `transcript_path` from stdin; captures each turn (prompt + thinking
    digest) into <ws>/journal/sessions/ via _capture.py (best-effort).
  - Independent per-session `review_count` cadence (settings.reflection.turn_interval,
    default 15) triggers an honest self-review. `total_turns` is monotonic and
    used as the capture turn number. Journal and review cadences never collide:
    when both fire on one Stop, a single block joins both reasons.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _capture  # type: ignore
from _atomic import atomic_write  # type: ignore
from _debug import log_debug  # type: ignore
from _home import active_workspace, gowth_home, journal_dir, list_workspaces, read_settings, state_path  # type: ignore
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


def _auto_forget_enabled() -> bool:
    """v3.6: whether the Stop hook archives journal raw past its TTL.

    Settings `journal.auto_forget_enabled` (default True). The canon (§3) treats
    journals as the ephemeral hippocampal buffer — `_forget.py` is the active
    forgetting step that keeps the active recall surface lean.
    """
    try:
        s = read_settings()
        j = s.get("journal", {}) if isinstance(s, dict) else {}
        return bool(j.get("auto_forget_enabled", True))
    except Exception:
        return True


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
        f"TL;DR: classify items as [goal]/[decision]/[exp]/[ref]/[tool]/[hypothesis]/[secret-ref], "
        f"route to topic folders, apply quality gates, update handoff.md."
    )


def _read_reflection_settings() -> tuple[bool, int]:
    """Return (reflection_enabled, turn_interval) from settings.json.

    Defaults: enabled True, turn_interval 15. Independent of auto_journal.
    """
    try:
        s = read_settings()
        r = s.get("reflection", {}) if isinstance(s, dict) else {}
        if not isinstance(r, dict):
            r = {}
        enabled = bool(r.get("enabled", True))
        interval = int(r.get("turn_interval", 15))
        return enabled, (interval if interval > 0 else 15)
    except Exception:
        return True, 15


def _build_review_reason(ws: str, review_count: int, session_id: str) -> str:
    """Build the self-review reason: short pointer to the anti-sycophancy contract
    + the session log path + the score-ledger path."""
    instructions_path = Path(__file__).parent.parent.parent / "templates" / "self-review-instructions.md"
    sid8 = (session_id or "default")[:8] or "default"
    today = datetime.now().strftime("%Y-%m-%d")
    session_log = journal_dir(ws) / "sessions" / f"{today}-{sid8}.md"
    scores_path = journal_dir(ws) / "_scores.md"
    return (
        f"[gowth-mem:self-review ws={ws}] {review_count} turns logged. "
        f"Read {instructions_path} and review the session log at {session_log}. "
        f"Scores go to {scores_path}. Be honest — chân thật, thẳng thắn."
    )


def _run_maintenance() -> None:
    """Best-effort prune + consolidate + forget subprocesses (journal cadence).

    Output is intentionally not embedded in the reason — the agent reads the
    externalized instructions template (Pattern 3: externalize long context,
    inject pointer only).
    """
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

    # v3.6: active forgetting — archive journal raw older than journal.raw_ttl_days
    # (canon §3). Near-noop when nothing is past TTL; gated by auto_forget_enabled.
    # Archived files stay recoverable (gz under .archive/ + memory-repo git history).
    forget_script = Path(__file__).parent / "_forget.py"
    if forget_script.is_file() and _auto_forget_enabled():
        try:
            subprocess.run(
                ["python3", str(forget_script), "--all-workspaces", "--quiet"],
                capture_output=True, text=True, timeout=10,
            )
        except subprocess.TimeoutExpired as e:
            log_debug("auto-journal", f"forget subprocess timeout after 10s: {e}")
        except Exception as e:
            log_debug("auto-journal", f"forget subprocess failed: {e}")


def _reset_counters(session_id: str, names: list[str]) -> None:
    """Reset the named per-session counters to 0 under the state lock.

    Only the listed keys are zeroed — other counters (e.g. total_turns) survive.
    """
    try:
        with file_lock("state", timeout=5.0):
            state = _load_state()
            sess = state["session"].setdefault(session_id, {})
            for n in names:
                sess[n] = 0
            _save_state(state)
    except TimeoutError as e:
        log_debug("auto-journal", f"state lock timeout (reset {names}): {e}")


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

    # v3.4: respect auto_journal_enabled toggle. v4.0: reflection is independent.
    journal_every, journal_enabled = _read_journal_settings()
    refl_enabled, turn_interval = _read_reflection_settings()
    if not journal_enabled and not refl_enabled:
        return 0

    session_id = data.get("session_id") or "default"
    transcript_path = data.get("transcript_path") or ""

    # Single lock acquisition: bump all cadence counters together so the two
    # cadences (journal turn_count vs review review_count) can never collide.
    try:
        with file_lock("state", timeout=5.0):
            state = _load_state()
            sess = state["session"].setdefault(session_id, {"turn_count": 0})
            sess["turn_count"] = sess.get("turn_count", 0) + 1
            sess["total_turns"] = sess.get("total_turns", 0) + 1  # monotonic
            sess["review_count"] = sess.get("review_count", 0) + 1
            turn = sess["turn_count"]
            total_turns = sess["total_turns"]
            review_count = sess["review_count"]
            _save_state(state)
    except TimeoutError as e:
        log_debug("auto-journal", f"state lock timeout (increment): {e}")
        print(json.dumps({"continue": True, "suppressOutput": True}))
        return 0

    # Resolve workspace once (prefer stdin cwd, else config/default).
    cwd = data.get("cwd")
    try:
        ws = active_workspace(Path(cwd)) if cwd else active_workspace()
    except Exception:
        ws = active_workspace()

    # v4.0: best-effort per-turn capture (prompt + thinking). Missing
    # transcript_path (older Claude Code) → capture_turn returns False silently.
    if refl_enabled:
        try:
            _capture.capture_turn(transcript_path, ws, session_id, total_turns, read_settings())
        except Exception as e:
            log_debug("auto-journal", f"capture_turn wrapper failed: {e}")

    reasons: list[str] = []

    # Journal cadence.
    if journal_enabled and turn >= journal_every:
        _reset_counters(session_id, ["turn_count"])
        _run_maintenance()
        reasons.append(_build_reason(ws, journal_every))

    # Review cadence (independent counter).
    if refl_enabled and review_count >= turn_interval:
        _reset_counters(session_id, ["review_count"])
        reasons.append(_build_review_reason(ws, review_count, session_id))

    if reasons:
        print(json.dumps({"decision": "block", "reason": "\n\n".join(reasons)}))
        return 0

    print(json.dumps({"continue": True, "suppressOutput": True}))
    return 0


if __name__ == "__main__":
    # stdin is consumed inside main() via sys.stdin.read()
    sys.exit(main())
