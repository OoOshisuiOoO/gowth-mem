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

v4.7 changes (delegation-first — keep the main context clean):
  - Both cadence reasons now instruct the MAIN session to dispatch a
    background subagent teammate instead of doing the work inline (the inline
    path cost 10-20 unrelated tool calls in the main context every N turns).
    The teammate's turn source is the captured session log. Journal falls back
    to the inline instruction when no session log exists; review is DEFERRED
    (counter kept) until a log exists — a zero-context judge pointed at a
    missing file can only fabricate or hit the signal floor.
  - Capture gating: `reflection.capture_enabled` (defaults to
    `reflection.enabled`, preserving the documented pre-v4.7 privacy opt-out —
    session logs sync to the git remote). Journal-only users opt into
    delegation with `reflection.capture_enabled: true`.
  - When both cadences fire on one Stop, the joined block explicitly directs
    TWO separate subagents (teammate may be a fork; judge must not be).

v4.7.1 changes (hardening — closes the verified findings of the v4.7 audit):
  - The hook can no longer traceback: `session_id` is coerced to str (a JSON
    number crashed the `[:8]` slice on every Stop) and __main__ wraps main()
    so the entrypoint ALWAYS exits 0.
  - Boolean settings go through `_coerce_bool` — the JSON string "false" no
    longer reads as True on the privacy-critical `reflection.capture_enabled`,
    and an explicit null falls back to the documented default chain.
  - Capture runs whenever `reflection.capture_enabled` is on, even with BOTH
    cadences disabled (the early return used to silently kill the knob for
    /mem-review-only users).
  - TTL archival is cadence-independent: `_run_forget_daily()` runs at most
    once per calendar day per machine (state.json `forget_last_run`),
    replacing the review-cadence modulo arithmetic that both spawned the
    subprocess on every Stop at turn_interval=1 and never archived with both
    cadences off.
  - Pre-dispatch signal floor: `reflection.min_review_turns` (default 10 —
    matches the rubric §0b) — a judge is never dispatched at a log it will
    immediately floor-skip.
  - The review-paused notice is flagged per session in state.json
    (`review_paused_notified`) — literally once per session, immune to
    counter resets and mid-session turn_interval changes — and carries the
    /mem-review-backlog nudge (permanently-deferred cohorts can only ever be
    reviewed via the backlog).
  - `_reset_counters` falls back to an unlocked best-effort write on lock
    timeout — a swallowed TimeoutError left review_count >= interval and
    dispatched a second judge for the same window on the very next Stop.
  - Midnight date-split: the previous day's session log (same sid) is passed
    to the teammate/judge as a secondary turn source when it exists, and the
    signal floor counts across both files.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _capture  # type: ignore
from _atomic import atomic_write  # type: ignore
from _debug import log_debug  # type: ignore
from _home import active_workspace, gowth_home, journal_dir, list_workspaces, read_settings, state_path  # type: ignore
from _lock import file_lock  # type: ignore

AUTO_DISTILL_EVERY = 10  # fallback default; overridden by settings.json journal_every
DEFAULT_MIN_REVIEW_TURNS = 10  # matches rubric §0b: <10 turns → judge skips

_TURN_RE = re.compile(r"^##\s+turn\s+\d+\b", re.MULTILINE)


def _coerce_bool(v, default: bool) -> bool:
    """Strict-ish bool for hand-edited settings.json values (v4.7.1).

    `bool(...)` mis-parsed privacy-critical knobs: the JSON string "false" is
    truthy, and an explicit null bypassed the default chain to False. Here:
    None → default (explicit null = "use the default chain"); strings match
    case-insensitively; unrecognized values → default.
    """
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "1", "yes", "on"):
            return True
        if s in ("false", "0", "no", "off", ""):
            return False
        return default
    if isinstance(v, (int, float)):
        return bool(v)
    return default


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
        enabled = _coerce_bool(aj.get("auto_journal_enabled",
                                      settings.get("auto_journal_enabled")), True)
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
        return _coerce_bool(j.get("auto_forget_enabled"), True)
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


def _session_log_path(ws: str, session_id: str, day: str | None = None) -> Path:
    """Per-session capture log for `day` (default today) — the path
    _capture.capture_turn writes."""
    sid8 = (str(session_id) if session_id else "default")[:8] or "default"
    day = day or datetime.now().strftime("%Y-%m-%d")
    return journal_dir(ws) / "sessions" / f"{day}-{sid8}.md"


def _count_turn_blocks(p: Path | None) -> int:
    """Number of captured `## turn N` blocks in a session log (0 if missing).

    v4.7.1 pre-dispatch signal floor: never dispatch a judge at a log it will
    immediately floor-skip under the rubric's §0b (<10 turns → skip)."""
    if p is None:
        return 0
    try:
        if not p.is_file():
            return 0
        return len(_TURN_RE.findall(p.read_text(encoding="utf-8", errors="replace")))
    except Exception:
        return 0


def _build_reason(ws: str, journal_every: int, session_log: Path,
                  prev_log: Path | None = None) -> str:
    """Build the journal reason string: short pointer (≤650 chars with real paths).

    v4.7 delegation-first: the cadence work (classify/route/gate/handoff/MOC)
    polluted the MAIN session's context with 10-20 unrelated tool calls every
    N turns. When a session log exists, the reason now instructs dispatching
    ONE background subagent teammate whose turn source is that log — the main
    session's only cost is the dispatch call. Inline fallback stays for
    sessions with no captured log (capture disabled, older Claude Code without
    transcript_path, or any capture miss: a fresh subagent would have no turn
    source). v4.7.1: a session straddling midnight has its window split across
    two files — both are named as turn sources.
    """
    instructions_path = Path(__file__).parent.parent.parent / "templates" / "auto-journal-instructions.md"
    sources = [p for p in (session_log, prev_log) if p is not None and p.is_file()]
    if sources:
        if len(sources) == 1:
            src = str(sources[0])
        else:
            src = (f"{sources[0]} plus {sources[1]} "
                   f"(same session split across midnight — read BOTH)")
        return (
            f"[gowth-mem:auto-journal ws={ws}] {journal_every} turns elapsed. "
            f"DELEGATE — do NOT do this in the main context: dispatch ONE background subagent "
            f"(memory teammate) whose prompt is: \"You are the dispatched gowth-mem memory "
            f"teammate — never dispatch further subagents. Read {instructions_path} and execute "
            f"its teammate protocol for ws={ws}. Turn source = session log {src}.\" "
            f"Then continue your work immediately. Inline only if no subagent tool exists."
        )
    return (
        f"[gowth-mem:auto-journal ws={ws}] {journal_every} turns elapsed. "
        f"Read {instructions_path} for the full protocol, then update journal. "
        f"TL;DR: classify items as [goal]/[decision]/[exp]/[ref]/[tool]/[hypothesis]/[secret-ref], "
        f"route to topic folders, apply quality gates, update handoff.md."
    )


def _capture_enabled(refl_enabled: bool) -> bool:
    """v4.7.1: whether per-turn capture (session logs) runs.

    `reflection.capture_enabled` — defaults to `reflection.enabled`, so the
    documented pre-v4.7 privacy opt-out (`reflection.enabled: false` = no
    raw-turn capture; session logs sync to the git remote) keeps working.
    Journal-only users set `reflection.capture_enabled: true` to give the
    delegation teammate a session-log turn source. Parsed via _coerce_bool:
    the string "false" disables, an explicit null means "use the default"."""
    try:
        s = read_settings()
        r = s.get("reflection", {}) if isinstance(s, dict) else {}
        if not isinstance(r, dict):
            r = {}
        return _coerce_bool(r.get("capture_enabled"), refl_enabled)
    except Exception:
        return refl_enabled


def _read_reflection_settings() -> tuple[bool, int, int]:
    """Return (reflection_enabled, turn_interval, min_review_turns).

    Defaults: enabled True, turn_interval 15, min_review_turns 10 (the rubric
    §0b floor — the hook must not dispatch a judge that will floor-skip).
    Independent of auto_journal.
    """
    try:
        s = read_settings()
        r = s.get("reflection", {}) if isinstance(s, dict) else {}
        if not isinstance(r, dict):
            r = {}
        enabled = _coerce_bool(r.get("enabled"), True)
        interval = int(r.get("turn_interval", 15))
        min_turns = int(r.get("min_review_turns", DEFAULT_MIN_REVIEW_TURNS))
        return (enabled,
                interval if interval > 0 else 15,
                min_turns if min_turns > 0 else DEFAULT_MIN_REVIEW_TURNS)
    except Exception:
        return True, 15, DEFAULT_MIN_REVIEW_TURNS


def _backlog_stat() -> str:
    """v4.1 backlog nudge (stat()-only, cheap). v4.7.1: appended to BOTH the
    review reason and the paused notice — permanently-deferred cohorts (no
    transcript_path, or capture opted out) can only ever be reviewed via
    /mem-review-backlog and used to get the nudge at every crossing."""
    try:
        from _review_ledger import stats as _rl_stats  # type: ignore
        backlog = _rl_stats().get("unreviewed", 0)
        if backlog:
            return (f" Backlog: {backlog} past conversation(s) unreviewed — "
                    f"run /mem-review-backlog when idle.")
    except Exception:
        pass
    return ""


def _build_review_reason(ws: str, review_count: int, session_log: Path,
                         prev_log: Path | None = None) -> str:
    """Build the self-review reason: dispatch directive for a fresh-context
    judge + the session log path(s) + the score-ledger path. Callers must only
    call this when the signal floor is met (main() defers otherwise)."""
    instructions_path = Path(__file__).parent.parent.parent / "templates" / "self-review-instructions.md"
    scores_path = journal_dir(ws) / "_scores.md"
    if prev_log is not None and prev_log.is_file() and session_log.is_file():
        log_ref = (f"the session logs {prev_log} + {session_log} "
                   f"(same session split across midnight — read BOTH, older first)")
    elif prev_log is not None and prev_log.is_file():
        log_ref = f"the session log {prev_log}"
    else:
        log_ref = f"the session log {session_log}"
    reason = (
        f"[gowth-mem:self-review ws={ws}] {review_count} turns logged. "
        f"DISPATCH a fresh-context background subagent as the judge (do NOT review in the "
        f"main context): pass it {instructions_path} + {log_ref}; "
        f"scores go to {scores_path}. Relay its 3-line summary when it completes. "
        f"Be honest — chân thật, thẳng thắn."
    )
    return reason + _backlog_stat()


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

    _run_forget()


def _run_forget() -> None:
    """v3.6 active forgetting — archive journal raw older than journal.raw_ttl_days
    (canon §3). Near-noop when nothing is past TTL; gated by auto_forget_enabled.
    Archived files stay recoverable (gz under .archive/ + memory-repo git history).
    """
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


def _run_forget_daily() -> None:
    """v4.7.1: cadence-INDEPENDENT TTL archival, at most once per calendar day
    per machine (state.json `forget_last_run`).

    Replaces the review-cadence modulo arithmetic, which was wrong in both
    directions: it spawned the forget subprocess on EVERY Stop at
    journal-off + turn_interval=1 + capture-on, and never archived at all with
    both cadences off — while /mem-journal and precompact-flush.py keep
    writing journal/<date>.md regardless of any cadence. The journal cadence's
    _run_maintenance still calls _run_forget directly (unchanged behavior).
    """
    if not _auto_forget_enabled():
        return
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        if _load_state().get("forget_last_run") == today:
            return  # cheap unlocked pre-check (the common case)
        with file_lock("state", timeout=5.0):
            state = _load_state()  # re-check under the lock
            if state.get("forget_last_run") == today:
                return
            state["forget_last_run"] = today
            _save_state(state)
    except TimeoutError as e:
        log_debug("auto-journal", f"state lock timeout (forget_daily): {e}")
        return
    except Exception as e:
        log_debug("auto-journal", f"forget_daily gate failed: {e}")
        return
    _run_forget()


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
        # v4.7.1: best-effort unlocked fallback. Leaving the counter >= interval
        # dispatches a SECOND judge/teammate for the same window on the very
        # next Stop — worse than the small risk of clobbering one concurrent
        # increment (atomic_write keeps the file itself consistent).
        try:
            state = _load_state()
            sess = state["session"].setdefault(session_id, {})
            for n in names:
                sess[n] = 0
            _save_state(state)
        except Exception as e2:
            log_debug("auto-journal", f"unlocked reset fallback failed: {e2}")


def _mark_paused_notified(session_id: str) -> None:
    """v4.7.1: persist the once-per-session review-paused flag. The old
    `review_count == turn_interval` inference repeated the notice after a
    counter reset and went silent when turn_interval was lowered mid-session
    past the crossing."""
    try:
        with file_lock("state", timeout=5.0):
            state = _load_state()
            state["session"].setdefault(session_id, {})["review_paused_notified"] = True
            _save_state(state)
    except Exception as e:
        log_debug("auto-journal", f"mark paused-notified failed: {e}")


def main() -> int:
    try:
        raw_stdin = sys.stdin.read()
    except Exception:
        raw_stdin = ""

    try:
        data = json.loads(raw_stdin) if raw_stdin.strip() else {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}

    # v3.4: skip in subagent context (no double-journaling under ralph/ultrawork)
    if _is_subagent(data):
        return 0

    # v3.4: respect auto_journal_enabled toggle. v4.0: reflection is independent.
    journal_every, journal_enabled = _read_journal_settings()
    refl_enabled, turn_interval, min_review_turns = _read_reflection_settings()
    capture_on = _capture_enabled(refl_enabled)

    # v4.7.1: TTL archival is cadence-independent — /mem-journal and
    # precompact-flush.py keep writing journal/<date>.md with both cadences
    # off, and capture-only configs grow journal/sessions/ forever. At most
    # once per calendar day; near-noop when nothing is past TTL.
    _run_forget_daily()

    # v4.7.1: `reflection.capture_enabled: true` must work even with BOTH
    # cadences disabled (/mem-review-only users) — the early return checks it.
    if not journal_enabled and not refl_enabled and not capture_on:
        return 0

    # v4.7.1: str() — a truthy non-string session_id (e.g. a JSON number from
    # a wrapper harness) crashed the [:8] slice and killed the hook every Stop.
    session_id = str(data.get("session_id") or "default")
    transcript_path = data.get("transcript_path") or ""
    if not isinstance(transcript_path, str):
        transcript_path = ""

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
            paused_notified = bool(sess.get("review_paused_notified"))
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

    # One session-log path per Stop for the reason builders and the review
    # gate. capture_turn derives its own date internally, so a Stop straddling
    # midnight can split the window across two files — the previous-day log
    # (same sid) is included as a secondary turn source whenever it exists.
    session_log = _session_log_path(ws, session_id)
    prev_day = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    _prev = _session_log_path(ws, session_id, prev_day)
    prev_log = _prev if _prev.is_file() else None

    # v4.0: best-effort per-turn capture (prompt + thinking). Missing
    # transcript_path (older Claude Code) → capture_turn returns False silently.
    # v4.7.1: gated by reflection.capture_enabled (defaults to reflection.enabled).
    if capture_on:
        try:
            _capture.capture_turn(transcript_path, ws, session_id, total_turns, read_settings())
        except Exception as e:
            log_debug("auto-journal", f"capture_turn wrapper failed: {e}")

    # v4.4: debounced vault push. hooks.json wires auto-sync to PostCompact ONLY, so
    # a session that never compacts never pushed — a live vault was found holding 116
    # uncommitted changes at ahead=0/behind=0, invisible to the user's other machine.
    # Debounced (default 30 min) + spawned detached, so a turn never waits on network.
    try:
        from _sync import maybe_autosync  # type: ignore
        maybe_autosync()
    except Exception as e:
        log_debug("auto-journal", f"maybe_autosync failed: {e}")

    reasons: list[str] = []
    journal_fired = False
    review_fired = False

    # Journal cadence.
    if journal_enabled and turn >= journal_every:
        _reset_counters(session_id, ["turn_count"])
        _run_maintenance()
        reasons.append(_build_reason(ws, journal_every, session_log, prev_log))
        journal_fired = True

    # Review cadence (independent counter). v4.7.1: the review is DEFERRED
    # (counter kept) until the session log holds min_review_turns `## turn`
    # blocks — a fresh-context judge pointed at a missing or 2-turn log can
    # only fabricate or floor-skip (the crossing would be consumed by a
    # guaranteed no-op subagent). It fires on the first Stop at/after the
    # crossing where the floor is met.
    if refl_enabled and review_count >= turn_interval:
        turns_logged = _count_turn_blocks(session_log) + _count_turn_blocks(prev_log)
        if turns_logged >= min_review_turns:
            _reset_counters(session_id, ["review_count"])
            reasons.append(_build_review_reason(ws, review_count, session_log, prev_log))
            review_fired = True
        else:
            if review_count == turn_interval:
                # One debug line at the crossing — NOT a line per Stop.
                log_debug("auto-journal",
                          f"self-review deferred (count={review_count}, "
                          f"turns_logged={turns_logged}, floor={min_review_turns}, "
                          f"log={session_log})")
            if turns_logged == 0 and not session_log.is_file() and prev_log is None \
                    and not paused_notified:
                # v4.7.1: literally once per session (persisted flag). Only
                # when capture produces NO log at all — a young-but-growing
                # log means capture works and the review fires once the floor
                # is met, so no notice is needed there.
                # Prefix is deliberately NOT [gowth-mem:self-review …]: external
                # consumers (session-insights-style skills, _classify in tests)
                # key on that token and would treat a nothing-to-review notice
                # as a live review directive.
                _mark_paused_notified(session_id)
                reasons.append(
                    f"[gowth-mem:review-paused ws={ws}] session review paused — no session log "
                    f"captured (transcript_path missing, or reflection.capture_enabled is false). "
                    f"Reviews resume automatically once capture produces a log; opt in via "
                    f"settings.reflection.capture_enabled: true. Tell the user this in ONE line, "
                    f"then continue." + _backlog_stat()
                )

    if reasons:
        reason = "\n\n".join(reasons)
        if journal_fired and review_fired:
            # Both cadences DISPATCHED: the two directives conflict (teammate
            # may be a context-inheriting fork; the judge must NOT be). Say it
            # once, explicitly, so a model cannot collapse them into one
            # agent. Keyed off what fired — a paused-review notice joining an
            # inline journal reason must not grow this header.
            reason = (
                "[gowth-mem:both-cadences] Two cadences fired this turn. Dispatch TWO SEPARATE "
                "background subagents — (1) the memory teammate (a context-inheriting fork is "
                "fine), (2) the fresh-context judge (must NOT be a fork). Never merge them into "
                "one agent.\n\n" + reason
            )
        print(json.dumps({"decision": "block", "reason": reason}))
        return 0

    print(json.dumps({"continue": True, "suppressOutput": True}))
    return 0


if __name__ == "__main__":
    # stdin is consumed inside main() via sys.stdin.read()
    try:
        rc = main()
    except Exception as e:
        # v4.7.1: a hook entrypoint must NEVER exit non-zero — an uncaught
        # traceback here killed capture, autosync, and both cadences on every
        # Stop for the rest of the session.
        try:
            log_debug("auto-journal", f"unhandled crash: {e}")
            print(json.dumps({"continue": True, "suppressOutput": True}))
        except Exception:
            pass
        rc = 0
    sys.exit(rc)
