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
from datetime import datetime
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
                         turn_interval: int = 15, capture_enabled: bool | None = None,
                         journal_enabled: bool = True, auto_forget: bool = False) -> None:
        reflection: dict = {
            "enabled": reflection_enabled,
            "turn_interval": turn_interval,
            "capture_thinking": True,
            "max_prompt_chars": 2000,
            "max_thinking_chars": 1500,
        }
        if capture_enabled is not None:
            reflection["capture_enabled"] = capture_enabled
        settings = {
            "auto_journal": {"journal_every": journal_every, "auto_journal_enabled": journal_enabled},
            "reflection": reflection,
            # Forget subprocess off by default during tests (speed + isolation).
            "journal": {"auto_forget_enabled": auto_forget},
        }
        (self.home / "settings.json").write_text(json.dumps(settings))

    def _run_stop(self, session_id: str, with_transcript: bool = True,
                  agent_type: str | None = None, transcript_path: str | None = None
                  ) -> subprocess.CompletedProcess:
        payload: dict = {"session_id": session_id}
        if agent_type:
            payload["agent_type"] = agent_type
        if with_transcript:
            payload["transcript_path"] = transcript_path or str(self.tx)
        env = {**os.environ, "GOWTH_MEM_HOME": str(self.home)}
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
        self._write_settings(journal_every=99, turn_interval=2)
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
        self._write_settings(journal_every=99, turn_interval=2)
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
        self._write_settings(journal_every=2, turn_interval=2)
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
        self._write_settings(journal_enabled=False, turn_interval=1, auto_forget=True)
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
