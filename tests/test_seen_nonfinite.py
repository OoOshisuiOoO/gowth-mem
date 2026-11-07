"""v4.9 review (hooks#2): a non-finite `seen` stamp in state.json must not take
turn counting down.

Python's json accepts `1e999`, `Infinity`, `-Infinity` and `NaN`. `_seen_of`
let every float through and called `int(v)`, which raises OverflowError /
ValueError. The prune runs inside the locked turn increment, so one bad stamp
on ANY other session crashed every real-turn Stop for good (no counting, no
capture, no cadences, no MEMORY.md refresh, no autosync) — silent apart from
hook-errors.log. The entry is now re-stamped like any other unusable value.
"""
from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_pipeline_hygiene import _StopCase, _load  # noqa: E402


class NonFiniteSeenUnitTest(unittest.TestCase):
    def setUp(self):
        self.aj = _load("aj_nonfinite", "auto-journal.py")

    def test_non_finite_and_huge_values_are_restamped(self):
        now = 1_800_000_000
        bad = {"inf": json.loads("1e999"), "ninf": float("-inf"), "nan": float("nan"),
               "jinf": json.loads('{"v": Infinity}')["v"], "jnan": json.loads('{"v": NaN}')["v"]}
        state = {"session": {**{k: {"seen": v} for k, v in bad.items()},
                             "huge-int": {"seen": 10 ** 400}, "ok": {"seen": now - 100},
                             "cur": {"seen": now}}}
        self.aj._stamp_and_prune_sessions(state, "cur", now)
        s = state["session"]
        for k in bad:
            self.assertEqual(s[k]["seen"], now, f"{k} must be re-stamped, not crash")
        self.assertEqual(s["huge-int"]["seen"], now, "an int far in the future is unusable → re-stamped")
        self.assertEqual(s["ok"]["seen"], now - 100, "a valid stamp is kept")


class NonFiniteSeenThroughTheHookTest(_StopCase):
    def test_a_bad_stamp_on_another_session_does_not_stop_counting(self):
        for i, raw in enumerate(("1e999", "Infinity", "-Infinity", "NaN"), start=1):
            with self.subTest(raw=raw):
                # a hand-edited state.json: the bad stamp sits on a DIFFERENT session
                # (rewriting the file also drops this session's entry; the Stop then
                # re-creates it and continues the session log's `## turn N` numbering)
                text = ('{"version": 2, "files": {}, "session": {"old-sess": {"turn_count": 3, "seen": '
                        + raw + '}}}')
                (self.home / "state.json").write_text(text)
                self.human(f"rotate the staging certificate ({raw})")
                self.stop()
                sess = self.sessions()
                self.assertIn(self.sid, sess, "the real turn was not counted — the Stop crashed")
                self.assertEqual(sess[self.sid].get("total_turns"), i)
                self.assertIn(f"## turn {i} ", self.log())
                self.assertIsInstance(sess["old-sess"]["seen"], int, "the bad stamp is repaired")
                self.assertLessEqual(abs(sess["old-sess"]["seen"] - int(time.time())), 120)
                self.assertIn(f"rotate the staging certificate ({raw})", self.log(),
                              "the turn was captured")
                errs = self.home / "logs" / "hook-errors.log"
                self.assertNotIn("unhandled crash", errs.read_text() if errs.is_file() else "")


if __name__ == "__main__":
    unittest.main()
