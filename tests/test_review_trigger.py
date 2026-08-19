#!/usr/bin/env python3
"""Hook-level tests for v4.0 auto-journal.py review + capture integration.

Drives auto-journal.py as a subprocess (as Claude Code's Stop hook does), with
GOWTH_MEM_HOME tempfile isolation. Verifies the two independent cadences
(journal turn_count vs review review_count) never collide, that capture runs
per turn, and that everything degrades gracefully.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "hooks" / "scripts"
HOOK = SCRIPTS_DIR / "auto-journal.py"


def _make_transcript(path: Path) -> None:
    # Real-world shape: signature-only (empty) thinking + visible text + tool_use.
    lines = [
        json.dumps({"type": "user", "message": {"content": "implement the capture module"}}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": "", "signature": "sig-xyz"},
            {"type": "text", "text": "Done — wrote _capture.py"},
            {"type": "tool_use", "name": "Edit", "input": {"file_path": "/repo/hooks/scripts/_capture.py"}},
        ]}}),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class ReviewBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        # Minimal materialized workspace.
        (self.home / "workspaces" / "default" / "journal").mkdir(parents=True)
        (self.home / "workspaces" / "default" / "workspace.json").write_text(
            json.dumps({"name": "default"}))
        (self.home / "config.json").write_text(json.dumps({"active_workspace": "default"}))
        self._write_settings()
        self.tx = self.home / "transcript.jsonl"
        _make_transcript(self.tx)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_settings(self, reflection_enabled: bool = True, journal_every: int = 10,
                         turn_interval: int = 15, capture_enabled=None,
                         journal_enabled: bool = True, auto_forget: bool = False,
                         min_review_turns: int | None = None) -> None:
        reflection: dict = {
            "enabled": reflection_enabled,
            "turn_interval": turn_interval,
            "capture_thinking": True,
            "max_prompt_chars": 2000,
            "max_thinking_chars": 1500,
        }
        if capture_enabled is not None:
            reflection["capture_enabled"] = capture_enabled
        if min_review_turns is not None:
            reflection["min_review_turns"] = min_review_turns
        settings = {
            "auto_journal": {"journal_every": journal_every, "auto_journal_enabled": journal_enabled},
            "reflection": reflection,
            # Forget subprocess off by default during tests (speed + isolation).
            "journal": {"auto_forget_enabled": auto_forget},
        }
        (self.home / "settings.json").write_text(json.dumps(settings))

    def _run_stop(self, session_id: str, with_transcript: bool = True,
                  agent_type: str | None = None, transcript_path: str | None = None,
                  extra_env: dict | None = None) -> subprocess.CompletedProcess:
        payload: dict = {"session_id": session_id}
        if agent_type:
            payload["agent_type"] = agent_type
        if with_transcript:
            payload["transcript_path"] = transcript_path or str(self.tx)
        env = {**os.environ, "GOWTH_MEM_HOME": str(self.home)}
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps(payload), capture_output=True, text=True, env=env,
        )

    @staticmethod
    def _classify(result: subprocess.CompletedProcess) -> tuple[bool, bool, dict]:
        """Return (journal_fired, review_fired, parsed_output).

        v4.7.1: matches the exact bracketed trigger tokens — a paused-review
        NOTICE must not classify as a fired review (its prefix is
        [gowth-mem:review-paused …], distinct by contract)."""
        out = result.stdout.strip()
        if not out:
            return False, False, {}
        d = json.loads(out)
        reason = d.get("reason", "") if d.get("decision") == "block" else ""
        return ("[gowth-mem:auto-journal ws=" in reason), ("[gowth-mem:self-review ws=" in reason), d

    def _session_file(self, session_id: str) -> Path:
        today = datetime.now().strftime("%Y-%m-%d")
        return (self.home / "workspaces" / "default" / "journal" / "sessions"
                / f"{today}-{session_id[:8]}.md")

    def _state(self, session_id: str) -> dict:
        sp = self.home / "state.json"
        if not sp.is_file():
            return {}
        return json.loads(sp.read_text()).get("session", {}).get(session_id, {})


class TestCounterIndependence(ReviewBase):
    def test_journal_and_review_cadences_do_not_collide(self):
        sid = "abcdef123456"
        journal_stops, review_stops, outputs = [], [], []
        for i in range(1, 31):
            r = self._run_stop(sid)
            self.assertEqual(r.returncode, 0, f"stop {i} stderr: {r.stderr}")
            j, rev, d = self._classify(r)
            outputs.append(d)
            if j:
                journal_stops.append(i)
            if rev:
                review_stops.append(i)

        self.assertEqual(journal_stops, [10, 20, 30], "journal must fire at 10/20/30")
        self.assertEqual(review_stops, [15, 30], "review must fire at 15/30")

        # At stop 30 both fire → exactly ONE block whose reason carries both.
        d30 = outputs[29]
        self.assertEqual(d30.get("decision"), "block")
        self.assertIn("auto-journal", d30["reason"])
        self.assertIn("self-review", d30["reason"])

        # Counters reset after firing; total_turns monotonic through it all.
        st = self._state(sid)
        self.assertEqual(st.get("total_turns"), 30, "total_turns must be monotonic")
        self.assertEqual(st.get("turn_count"), 0, "turn_count resets after journal fire at 30")
        self.assertEqual(st.get("review_count"), 0, "review_count resets after review fire at 30")

    def test_review_only_stop_15_has_no_journal(self):
        sid = "only15xxxxxx"
        outputs = [self._classify(self._run_stop(sid)) for _ in range(15)]
        j15, rev15, d15 = outputs[14]
        self.assertTrue(rev15, "review must fire at stop 15")
        self.assertFalse(j15, "journal must NOT fire at stop 15")
        self.assertIn("self-review", d15["reason"])
        self.assertNotIn("auto-journal", d15["reason"])


class TestCaptureThroughHook(ReviewBase):
    def test_capture_writes_session_file(self):
        sid = "capwrite1234"
        r = self._run_stop(sid)
        self.assertEqual(r.returncode, 0)
        sf = self._session_file(sid)
        self.assertTrue(sf.is_file(), "capture must create the session log")
        content = sf.read_text()
        self.assertIn("## turn 1", content)
        self.assertIn("implement the capture module", content)
        self.assertIn("Done — wrote _capture.py", content)      # Claude summary
        self.assertIn("Edit(_capture.py)", content)             # actions trace
        self.assertNotIn("**Thinking:**", content)              # empty thinking → no line

    def test_capture_turn_number_is_monotonic_total(self):
        sid = "captotal1234"
        for _ in range(3):
            self._run_stop(sid)
        content = self._session_file(sid).read_text()
        for n in (1, 2, 3):
            self.assertIn(f"## turn {n}", content)


class TestReflectionDisabled(ReviewBase):
    def test_disabled_no_review_no_capture_journal_inline(self):
        """v4.7.1 privacy contract: `reflection.enabled: false` was the
        documented opt-out for raw-turn capture — it stays that way unless
        `reflection.capture_enabled: true` opts back in. Without a session log
        the journal reason must use the inline-fallback wording."""
        self._write_settings(reflection_enabled=False)
        sid = "disabled1234"
        review_fired = False
        journal_stops = []
        journal_reason = ""
        for i in range(1, 16):
            r = self._run_stop(sid)
            self.assertEqual(r.returncode, 0)
            j, rev, d = self._classify(r)
            review_fired = review_fired or rev
            if j:
                journal_stops.append(i)
                journal_reason = d["reason"]
        self.assertFalse(review_fired, "review must never fire when reflection disabled")
        self.assertEqual(journal_stops, [10], "journal still fires when reflection disabled")
        self.assertFalse(self._session_file(sid).exists(),
                         "reflection off (no explicit capture_enabled) must NOT capture")
        self.assertIn("for the full protocol", journal_reason,
                      "no session log → journal reason must be the inline-fallback variant")

    def test_capture_enabled_opts_journal_only_into_delegation(self):
        """v4.7.1: journal-only users set reflection.capture_enabled: true to
        give the delegation teammate a session-log turn source."""
        self._write_settings(reflection_enabled=False, capture_enabled=True, journal_every=2)
        sid = "optin1234567"
        self._run_stop(sid)
        r = self._run_stop(sid)
        j, _, d = self._classify(r)
        self.assertTrue(self._session_file(sid).exists(),
                        "capture_enabled=true must capture even with reflection off")
        self.assertTrue(j, "journal must fire at stop 2")
        self.assertIn("subagent", d["reason"].lower(),
                      "with a session log the journal reason must be the delegate variant")


class TestDelegationReasons(ReviewBase):
    """v4.7: cadence blocks delegate to a background teammate — the injected
    reason must carry the dispatch directive + the session-log path so the main
    session's only cost is one Agent/Task call."""

    def test_journal_reason_dispatches_teammate_with_session_log(self):
        self._write_settings(journal_every=2, turn_interval=99)
        sid = "delegate1234"
        self._run_stop(sid)
        r = self._run_stop(sid)
        j, _, d = self._classify(r)
        self.assertTrue(j, "journal must fire at stop 2 with journal_every=2")
        reason = d["reason"]
        self.assertIn("subagent", reason.lower(),
                      "journal reason must instruct dispatching a subagent teammate")
        self.assertIn(str(self._session_file(sid)), reason,
                      "journal reason must carry the session-log path (teammate's source)")
        self.assertIn("never dispatch further subagents", reason,
                      "the anti-recursion sentinel must ship inside the teammate prompt")

    def test_journal_reason_falls_back_inline_without_session_log(self):
        """No transcript → no session log → a fresh subagent would have no
        turn source. The reason must be the pre-v4.7 inline variant."""
        self._write_settings(journal_every=2, turn_interval=99)
        sid = "nolog1234567"
        self._run_stop(sid, with_transcript=False)
        r = self._run_stop(sid, with_transcript=False)
        j, _, d = self._classify(r)
        self.assertTrue(j, "journal must still fire without a transcript")
        self.assertNotIn(str(self._session_file(sid)), d["reason"],
                         "reason must not reference a session log that was never captured")
        self.assertIn("for the full protocol", d["reason"],
                      "no session log → inline-fallback wording, not the delegate variant")

    def test_review_reason_dispatches_teammate(self):
        self._write_settings(journal_every=99, turn_interval=2, min_review_turns=2)
        sid = "revdeleg1234"
        self._run_stop(sid)
        r = self._run_stop(sid)
        _, rev, d = self._classify(r)
        self.assertTrue(rev, "review must fire at stop 2 with turn_interval=2")
        self.assertIn("subagent", d["reason"].lower(),
                      "review reason must instruct dispatching a fresh-context subagent")

    def test_review_deferred_without_session_log(self):
        """v4.7.1: a judge with no session log can only fabricate or hit the
        signal floor — the review must be DEFERRED (counter kept) so it fires
        as soon as a log exists, and never dispatches at a missing file. The
        first deferral surfaces a one-line PAUSED notice (user-visible signal);
        later deferrals are silent."""
        self._write_settings(journal_every=99, turn_interval=2, min_review_turns=1)
        sid = "revnolog1234"
        self._run_stop(sid, with_transcript=False)
        # Stop 2 = the exact cadence crossing → one-time paused notice, no dispatch.
        r = self._run_stop(sid, with_transcript=False)
        _, rev, d = self._classify(r)
        self.assertEqual(d.get("decision"), "block", "first deferral must surface a notice")
        self.assertIn("paused", d["reason"], "notice must say the review is paused")
        self.assertIn("capture_enabled", d["reason"], "notice must name the fix knob")
        self.assertNotIn("DISPATCH", d["reason"], "notice must not dispatch a judge")
        self.assertNotIn(str(self._session_file(sid)), d["reason"])
        # The notice must NOT reuse the live trigger token — external consumers
        # (e.g. a session-insights skill) key on [gowth-mem:self-review] and
        # would launch a full review of a session with nothing to review.
        self.assertFalse(rev, "paused notice must not classify as a fired review")
        self.assertNotIn("[gowth-mem:self-review ws=", d["reason"])
        self.assertIn("[gowth-mem:review-paused ws=", d["reason"])
        self.assertGreaterEqual(self._state(sid).get("review_count", 0), 2,
                                "deferred review must keep its counter (fires once a log exists)")
        # Stop 3: still no log, past the crossing → silent (no block, no log spam).
        r = self._run_stop(sid, with_transcript=False)
        d = json.loads(r.stdout.strip())
        self.assertNotEqual(d.get("decision"), "block",
                            "deferral past the first crossing must be silent")
        # Log appears (transcript now available) → real review fires on the next stop.
        r = self._run_stop(sid)
        _, rev, d = self._classify(r)
        self.assertTrue(rev, "review must fire on the first stop after a log exists")
        self.assertIn(str(self._session_file(sid)), d["reason"])
        self.assertIn("DISPATCH", d["reason"])

    def test_deferral_notice_plus_journal_inline_has_no_two_agent_header(self):
        """v4.7.1: the TWO-SEPARATE-subagents header belongs to a real
        journal-dispatch + review-dispatch collision. A paused-review notice
        joining an inline journal reason is NOT that — the header must key off
        what actually fired, not off len(reasons)."""
        self._write_settings(journal_every=2, turn_interval=2)
        sid = "noticecol123"
        self._run_stop(sid, with_transcript=False)
        r = self._run_stop(sid, with_transcript=False)
        d = json.loads(r.stdout.strip())
        self.assertEqual(d.get("decision"), "block")
        self.assertIn("auto-journal", d["reason"])
        self.assertIn("paused", d["reason"])
        self.assertNotIn("TWO SEPARATE", d["reason"],
                         "no dispatch collision → no two-agent header")

    def test_collision_block_directs_two_separate_subagents(self):
        """v4.7.1: when both cadences fire on one Stop, the joined block must
        explicitly direct TWO separate subagents (teammate + fresh judge) so a
        model cannot collapse them into one fork that grades itself."""
        self._write_settings(journal_every=2, turn_interval=2, min_review_turns=2)
        sid = "collide12345"
        self._run_stop(sid)
        r = self._run_stop(sid)
        j, rev, d = self._classify(r)
        self.assertTrue(j and rev, "both cadences must fire at stop 2")
        self.assertIn("TWO SEPARATE", d["reason"],
                      "collision block must direct two separate subagents")


class TestForgetOnReviewCadence(ReviewBase):
    def test_journal_off_reflection_on_still_forgets(self):
        """v4.7.1: journal-off + reflection-on captures every turn; TTL archival
        must still run (was: _forget only ran on the journal cadence → unbounded
        growth for that config)."""
        self._write_settings(journal_enabled=False, turn_interval=1, auto_forget=True,
                             min_review_turns=1)
        old = self.home / "workspaces" / "default" / "journal" / "2026-01-01.md"
        old.write_text("# old raw journal\nnoise line\n")
        stale = 30 * 86400
        os.utime(old, (os.path.getmtime(old) - stale, os.path.getmtime(old) - stale))
        sid = "forgetrev123"
        r = self._run_stop(sid)  # turn_interval=1 → review fires; forget must run too
        self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")
        _, rev, _ = self._classify(r)
        self.assertTrue(rev, "review must fire at stop 1 with turn_interval=1")
        self.assertFalse(old.exists(),
                         "past-TTL journal must be archived even with journal cadence disabled")

    def test_deferred_forget_not_every_stop_at_interval_1(self):
        """v4.7.1: with turn_interval=1 the deferred-modulo would be true on
        EVERY Stop — the forget subprocess must still only run at the crossing,
        never per turn. A stale file created AFTER the crossing must survive
        subsequent silent-deferral stops."""
        self._write_settings(journal_enabled=False, turn_interval=1, auto_forget=True,
                             capture_enabled=False)
        sid = "nospam123456"
        self._run_stop(sid)  # stop 1 = the crossing (forget may run here)
        old = self.home / "workspaces" / "default" / "journal" / "2026-01-01.md"
        old.write_text("# old raw journal\nnoise line\n")
        stale = 30 * 86400
        os.utime(old, (os.path.getmtime(old) - stale, os.path.getmtime(old) - stale))
        self._run_stop(sid)  # stop 2: silent deferral — must NOT spawn forget
        self._run_stop(sid)  # stop 3: same
        self.assertTrue(old.exists(),
                        "forget must not run on every Stop during deferral at turn_interval=1")

    def test_forget_runs_even_when_review_deferred(self):
        """v4.7.1 residual (reviewer N3): journal-off + reflection-on +
        capture-off must still archive — journal/<date>.md files keep arriving
        from /mem-journal and precompact-flush.py regardless of capture."""
        self._write_settings(journal_enabled=False, turn_interval=1, auto_forget=True,
                             capture_enabled=False)
        old = self.home / "workspaces" / "default" / "journal" / "2026-01-01.md"
        old.write_text("# old raw journal\nnoise line\n")
        stale = 30 * 86400
        os.utime(old, (os.path.getmtime(old) - stale, os.path.getmtime(old) - stale))
        sid = "forgetdef123"
        r = self._run_stop(sid)  # capture off → review defers, but forget must run
        self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")
        self.assertFalse(self._session_file(sid).exists(), "capture off → no session log")
        self.assertFalse(old.exists(),
                         "past-TTL journal must be archived even while the review is deferred")


class TestV471RobustStdin(ReviewBase):
    """v4.7.1: the hook entrypoint must NEVER exit non-zero (repo rule) —
    v4.7 moved the sid slice into the unguarded every-Stop path and a JSON
    number killed capture, autosync, and both cadences on every turn."""

    def _run_raw(self, payload) -> subprocess.CompletedProcess:
        env = {**os.environ, "GOWTH_MEM_HOME": str(self.home)}
        return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                              capture_output=True, text=True, env=env)

    def test_numeric_session_id_exits_0_and_still_captures(self):
        r = self._run_raw({"session_id": 12345, "transcript_path": str(self.tx)})
        self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")
        self.assertNotIn("Traceback", r.stderr)
        json.loads(r.stdout.strip())  # valid JSON out
        st = json.loads((self.home / "state.json").read_text())["session"]
        self.assertIn("12345", st, "counters must key on the coerced string sid")
        today = datetime.now().strftime("%Y-%m-%d")
        self.assertTrue((self.home / "workspaces" / "default" / "journal" / "sessions"
                         / f"{today}-12345.md").is_file(),
                        "capture must still run with a numeric session_id")

    def test_non_dict_stdin_exits_0(self):
        env = {**os.environ, "GOWTH_MEM_HOME": str(self.home)}
        r = subprocess.run([sys.executable, str(HOOK)], input="[1, 2, 3]",
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")
        self.assertNotIn("Traceback", r.stderr)

    def test_non_string_transcript_path_ignored(self):
        r = self._run_raw({"session_id": "tpnum1234567", "transcript_path": 42})
        self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")
        self.assertNotIn("Traceback", r.stderr)


class TestV471CaptureOnly(ReviewBase):
    def test_capture_only_config_still_captures(self):
        """Both cadences OFF + capture_enabled true (a /mem-review-only user):
        the early return silently killed the knob pre-v4.7.1 — journal/sessions/
        stayed empty forever with no notice."""
        self._write_settings(reflection_enabled=False, journal_enabled=False,
                             capture_enabled=True)
        sid = "captonly1234"
        r = self._run_stop(sid)
        self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")
        d = json.loads(r.stdout.strip())
        self.assertNotEqual(d.get("decision"), "block")
        self.assertTrue(self._session_file(sid).is_file(),
                        "capture must run even with both cadences disabled")

    def test_all_off_still_early_returns(self):
        self._write_settings(reflection_enabled=False, journal_enabled=False)
        sid = "alloff123456"
        r = self._run_stop(sid)
        self.assertEqual(r.returncode, 0)
        self.assertFalse(self._session_file(sid).exists())
        self.assertEqual(self._state(sid), {}, "all-off must not touch counters")


class TestV471BoolCoercion(ReviewBase):
    def test_capture_enabled_string_false_disables(self):
        """The JSON string "false" is truthy under bool() — on the
        privacy-critical knob it must parse as an opt-out (raw prompts were
        syncing to the git remote despite an explicit-looking false)."""
        self._write_settings(capture_enabled="false")
        sid = "strfalse1234"
        r = self._run_stop(sid)
        self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")
        self.assertFalse(self._session_file(sid).exists(),
                         '"capture_enabled": "false" must disable capture')

    def test_capture_enabled_null_uses_default_chain(self):
        """Explicit null = "use the default chain" (reflection.enabled), not
        False — bool(None) silently killed capture with reflection on."""
        settings = {
            "auto_journal": {"journal_every": 10, "auto_journal_enabled": True},
            "reflection": {"enabled": True, "turn_interval": 15, "capture_enabled": None},
            "journal": {"auto_forget_enabled": False},
        }
        (self.home / "settings.json").write_text(json.dumps(settings))
        sid = "nullcap12345"
        self._run_stop(sid)
        self.assertTrue(self._session_file(sid).is_file(),
                        "null capture_enabled must fall back to reflection.enabled")


class TestV471SignalFloor(ReviewBase):
    def test_review_defers_at_young_log_no_noop_dispatch(self):
        """The crossing must NOT be consumed dispatching a judge that will
        immediately floor-skip a 2-turn log (rubric §0b, default floor 10)."""
        self._write_settings(journal_every=99, turn_interval=2)  # floor: default 10
        sid = "youngfloor12"
        self._run_stop(sid)
        r = self._run_stop(sid)  # crossing: log has 2 turns < 10
        _, rev, d = self._classify(r)
        self.assertFalse(rev, "review must defer below the signal floor")
        self.assertNotIn("[gowth-mem:review-paused ws=", d.get("reason", ""),
                         "capture IS working — a young log must not raise the paused notice")
        self.assertGreaterEqual(self._state(sid).get("review_count", 0), 2,
                                "deferred review must keep its counter")
        # Pad the log past the floor → the next Stop fires.
        sf = self._session_file(sid)
        pad = "".join(f"\n## turn {90 + i} — 00:0{i}\n**User:** pad\n" for i in range(10))
        sf.write_text(sf.read_text() + pad)
        r = self._run_stop(sid)
        _, rev, d = self._classify(r)
        self.assertTrue(rev, "review must fire once the floor is met")
        self.assertEqual(self._state(sid).get("review_count"), 0)


class TestV471PausedNotice(ReviewBase):
    def test_notice_fires_when_interval_lowered_past_crossing(self):
        """Lowering turn_interval mid-session past the crossing used to defer
        forever with no notice (the == crossing had already passed)."""
        self._write_settings(journal_every=99, turn_interval=50)
        sid = "lowerint1234"
        for _ in range(4):
            self._run_stop(sid, with_transcript=False)
        self._write_settings(journal_every=99, turn_interval=3)  # crossing 3 < count 5
        r = self._run_stop(sid, with_transcript=False)
        d = json.loads(r.stdout.strip())
        self.assertEqual(d.get("decision"), "block",
                         "notice must fire even when the crossing was passed by a settings change")
        self.assertIn("[gowth-mem:review-paused ws=", d["reason"])
        r = self._run_stop(sid, with_transcript=False)
        d = json.loads(r.stdout.strip())
        self.assertNotEqual(d.get("decision"), "block", "and only once")

    def test_notice_not_repeated_after_a_real_fire(self):
        """The persisted flag survives a real fire: a capture outage later in
        the session (e.g. the midnight path change) must not produce a second
        'once-per-session' notice."""
        self._write_settings(journal_every=99, turn_interval=2, min_review_turns=1)
        sid = "onceflag1234"
        self._run_stop(sid, with_transcript=False)
        r = self._run_stop(sid, with_transcript=False)  # crossing → the one notice
        self.assertIn("review-paused", json.loads(r.stdout.strip()).get("reason", ""))
        r = self._run_stop(sid)  # log appears → real fire, counter resets
        _, rev, _ = self._classify(r)
        self.assertTrue(rev)
        self._session_file(sid).unlink()  # capture output gone again
        self._run_stop(sid, with_transcript=False)
        r = self._run_stop(sid, with_transcript=False)  # crossing again
        d = json.loads(r.stdout.strip())
        self.assertNotEqual(d.get("decision"), "block",
                            "paused notice must fire at most once per session")

    def test_paused_notice_carries_backlog_nudge(self):
        """v4.7.1: no-transcript / capture-off cohorts can only ever be
        reviewed via /mem-review-backlog — the paused notice is the ONLY
        signal they still get, so it must carry the nudge."""
        claude_dir = self.home / "claude-config"
        proj = claude_dir / "projects" / "-tmp-proj"
        proj.mkdir(parents=True)
        big = proj / ("s" * 12 + ".jsonl")
        line = json.dumps({"type": "assistant", "message": {"content": "x" * 200}}) + "\n"
        big.write_text(line * 200)  # > 20k min_bytes
        old = big.stat().st_mtime - 7200
        os.utime(big, (old, old))  # idle > 60 min
        self._write_settings(journal_every=99, turn_interval=2)
        sid = "backlognud12"
        env = {"CLAUDE_CONFIG_DIR": str(claude_dir)}
        self._run_stop(sid, with_transcript=False, extra_env=env)
        r = self._run_stop(sid, with_transcript=False, extra_env=env)
        d = json.loads(r.stdout.strip())
        self.assertIn("review-paused", d.get("reason", ""))
        self.assertIn("Backlog: 1 past conversation", d["reason"])
        self.assertIn("/mem-review-backlog", d["reason"])


class TestV471MidnightSplit(ReviewBase):
    def _prev_log(self, sid: str) -> Path:
        yday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        return (self.home / "workspaces" / "default" / "journal" / "sessions"
                / f"{yday}-{sid[:8]}.md")

    def test_journal_reason_names_both_logs(self):
        """A session straddling midnight splits its window across two files —
        the teammate used to be handed only today's 1-2-turn log."""
        self._write_settings(journal_every=2, turn_interval=99)
        sid = "midnight1234"
        prev = self._prev_log(sid)
        prev.parent.mkdir(parents=True, exist_ok=True)
        prev.write_text("# Session log\n\n## turn 1 — 23:59\n**User:** before midnight\n")
        self._run_stop(sid)
        r = self._run_stop(sid)
        j, _, d = self._classify(r)
        self.assertTrue(j)
        self.assertIn(str(prev), d["reason"], "previous-day log must be a named turn source")
        self.assertIn(str(self._session_file(sid)), d["reason"])
        self.assertIn("read BOTH", d["reason"])

    def test_review_floor_counts_across_both_logs(self):
        self._write_settings(journal_every=99, turn_interval=2)  # floor: default 10
        sid = "midfloor1234"
        prev = self._prev_log(sid)
        prev.parent.mkdir(parents=True, exist_ok=True)
        prev.write_text("# Session log\n" + "".join(
            f"\n## turn {i} — 23:0{i % 10}\n**User:** pre-midnight\n" for i in range(1, 10)))
        self._run_stop(sid)
        r = self._run_stop(sid)  # 9 (yesterday) + 2 (today) = 11 >= floor 10
        _, rev, d = self._classify(r)
        self.assertTrue(rev, "the signal floor must count across the midnight split")
        self.assertIn(str(prev), d["reason"])


class TestV471ForgetDaily(ReviewBase):
    def test_forget_runs_with_all_cadences_off(self):
        """journal-off + reflection-off never archived pre-v4.7.1, while
        /mem-journal and precompact-flush.py keep writing journal/<date>.md."""
        self._write_settings(reflection_enabled=False, journal_enabled=False,
                             auto_forget=True)
        old = self.home / "workspaces" / "default" / "journal" / "2026-01-01.md"
        old.write_text("# old raw journal\nnoise line\n")
        stale = 30 * 86400
        os.utime(old, (os.path.getmtime(old) - stale, os.path.getmtime(old) - stale))
        r = self._run_stop("alloffforget")
        self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")
        self.assertFalse(old.exists(),
                         "TTL archival must be cadence-independent (daily throttle)")

    def test_forget_throttled_to_once_per_day(self):
        self._write_settings(journal_enabled=False, turn_interval=99, auto_forget=True)
        sid = "throttle1234"
        self._run_stop(sid)  # first Stop of the day → forget runs, date recorded
        old = self.home / "workspaces" / "default" / "journal" / "2026-01-01.md"
        old.write_text("# old raw journal\nnoise line\n")
        stale = 30 * 86400
        os.utime(old, (os.path.getmtime(old) - stale, os.path.getmtime(old) - stale))
        for _ in range(3):
            self._run_stop(sid)
        self.assertTrue(old.exists(), "same-day Stops must not spawn forget again")
        st = json.loads((self.home / "state.json").read_text())
        self.assertEqual(st.get("forget_last_run"), datetime.now().strftime("%Y-%m-%d"))


class TestSubagentSkip(ReviewBase):
    def test_subagent_no_capture_no_counters(self):
        sid = "subagent1234"
        r = self._run_stop(sid, agent_type="subagent")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self._state(sid), {}, "subagent stop must not touch counters")
        self.assertFalse(self._session_file(sid).exists(), "subagent stop must not capture")
        # A following normal stop is turn 1 (subagent did not increment).
        self._run_stop(sid)
        content = self._session_file(sid).read_text()
        self.assertIn("## turn 1", content)
        self.assertNotIn("## turn 2", content)


class TestGracefulTranscript(ReviewBase):
    def test_missing_transcript_hook_exits_0_no_session_file(self):
        sid = "notx12345678"
        r = self._run_stop(sid, with_transcript=False)
        self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")
        self.assertFalse(self._session_file(sid).exists(),
                         "no transcript → capture skipped silently")
        # Counters still advance (review/journal logic unaffected).
        self.assertEqual(self._state(sid).get("total_turns"), 1)

    def test_corrupt_transcript_hook_exits_0(self):
        sid = "corrupt12345"
        bad = self.home / "corrupt.jsonl"
        bad.write_text("not json\n{broken\nrandom\n")
        r = self._run_stop(sid, transcript_path=str(bad))
        self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")
        self.assertFalse(self._session_file(sid).exists(),
                         "corrupt transcript → nothing captured, no crash")


if __name__ == "__main__":
    unittest.main()
