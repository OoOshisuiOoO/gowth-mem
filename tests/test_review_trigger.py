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
                         turn_interval: int = 15) -> None:
        settings = {
            "auto_journal": {"journal_every": journal_every, "auto_journal_enabled": True},
            "reflection": {
                "enabled": reflection_enabled,
                "turn_interval": turn_interval,
                "capture_thinking": True,
                "max_prompt_chars": 2000,
                "max_thinking_chars": 1500,
            },
            # Skip the forget subprocess during tests (speed + isolation).
            "journal": {"auto_forget_enabled": False},
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
        """Return (journal_fired, review_fired, parsed_output)."""
        out = result.stdout.strip()
        if not out:
            return False, False, {}
        d = json.loads(out)
        reason = d.get("reason", "") if d.get("decision") == "block" else ""
        return ("auto-journal" in reason), ("self-review" in reason), d

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
    def test_disabled_no_capture_no_review(self):
        self._write_settings(reflection_enabled=False)
        sid = "disabled1234"
        review_fired = False
        journal_stops = []
        for i in range(1, 16):
            r = self._run_stop(sid)
            self.assertEqual(r.returncode, 0)
            j, rev, _ = self._classify(r)
            review_fired = review_fired or rev
            if j:
                journal_stops.append(i)
        self.assertFalse(review_fired, "review must never fire when reflection disabled")
        self.assertEqual(journal_stops, [10], "journal still fires when reflection disabled")
        self.assertFalse(self._session_file(sid).exists(),
                         "no capture when reflection disabled")


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
