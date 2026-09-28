"""v4.8 Task 2: `_handoff.digest` — the newest-first handoff slice for MEMORY.md.

The audit found three real handoff shapes: newest-first (personal, devops),
mixed order (trade: newest section at the top, later auto-journal sections
appended at the tail), and a 52 KB headerless body (idol-ai). The digest must
put the newest dated section first for all three, keep live bullets
([blocker]/[doing]/[next]/[thread]) ahead of [done] inside it, drop blank
lines, and cut long lines, so the block stays useful inside a 60-line budget.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "hooks" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _handoff  # type: ignore  # noqa: E402

NEWEST_FIRST = """# handoff — demo

## 2026-09-13 snapshot
- host:mac 2026-09-13 [done] shipped v4
- host:mac 2026-09-13 [blocker] waiting on token

## 2026-09-11 snapshot
- host:mac 2026-09-11 [done] old work

## Notes
- structural section, undated
"""

MIXED = """# handoff — trade

## 2026-09-11 status
- host:mac 2026-09-11 [done] first

## Notes
- keep me after dated sections

## 2026-09-13 auto-journal
- host:mac 2026-09-13 [next] newest appended at the tail
"""


class HandoffDigestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_digest_")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        self.docs = Path(self.tmp) / "workspaces" / "demo" / "docs"
        self.docs.mkdir(parents=True)

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, text: str) -> None:
        (self.docs / "handoff.md").write_text(text)

    def test_missing_file_is_empty(self):
        self.assertEqual(_handoff.digest("demo"), [])

    def test_newest_section_first_and_live_bullets_first(self):
        self._write(NEWEST_FIRST)
        lines = _handoff.digest("demo")
        self.assertEqual(lines[0], "## 2026-09-13 snapshot")
        self.assertIn("[blocker]", lines[1])
        self.assertIn("[done] shipped", lines[2])
        self.assertNotIn("", lines)

    def test_mixed_order_puts_newest_first_then_older_then_undated(self):
        self._write(MIXED)
        lines = _handoff.digest("demo")
        headers = [l for l in lines if l.startswith("## ")]
        self.assertEqual(headers, ["## 2026-09-13 auto-journal", "## 2026-09-11 status", "## Notes"])

    def test_headerless_body_yields_first_nonblank_lines(self):
        body = "# handoff — idol\n\n" + "".join(f"line {i} some state text\n\n" for i in range(300))
        self._write(body)
        lines = _handoff.digest("demo")
        self.assertEqual(len(lines), 60)
        self.assertNotIn("", lines)
        self.assertEqual(lines[1], "line 0 some state text")

    def test_long_line_is_cut_with_ellipsis(self):
        self._write("## 2026-09-13 s\n- host:mac 2026-09-13 [doing] " + "x" * 400 + "\n")
        lines = _handoff.digest("demo")
        self.assertEqual(len(lines[1]), 160)
        self.assertTrue(lines[1].endswith("…"))

    def test_max_lines_respected(self):
        self._write("## 2026-09-13 s\n" + "".join(f"- host:mac 2026-09-13 [done] item {i}\n" for i in range(200)))
        self.assertEqual(len(_handoff.digest("demo", max_lines=60)), 60)
        self.assertEqual(len(_handoff.digest("demo", max_lines=15)), 15)


if __name__ == "__main__":
    unittest.main()
