"""v4.8: every hook emission stays under Claude Code's persist rule.

Measured on the 2.1.283 binary (E2E, 2026-09-28): any hook additionalContext or
SessionStart stdout of >= 10,000 chars is written to a tool-results file and the
model receives a 2,000-char preview. 9,500 chars is delivered in full. The
gowth-mem bootstrap (15,589 chars) hit this in 193 sessions since 2026-09-02, so
the model never saw handoff.md. `clamp_context` is the one guard every emitter
passes through.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "hooks" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _home  # type: ignore  # noqa: E402

MARKER = "[gowth-mem: truncated to fit the host limit]"


class ClampContextTest(unittest.TestCase):
    def test_limit_is_under_the_host_persist_threshold(self):
        self.assertEqual(_home.HOOK_CONTEXT_MAX, 9000)

    def test_short_text_unchanged(self):
        text = "x" * 8999
        self.assertEqual(_home.clamp_context(text), text)

    def test_cuts_at_line_boundary_with_marker(self):
        text = "\n".join(["L" * 80] * 200)          # 16,199 chars
        out = _home.clamp_context(text)
        self.assertLess(len(out), 9000)
        self.assertTrue(out.endswith(MARKER))
        lines = out.split("\n")
        self.assertEqual(lines[-2], "L" * 80, "no half line before the marker")

    def test_single_long_line_is_hard_cut(self):
        out = _home.clamp_context("y" * 20000)
        self.assertLess(len(out), 9000)
        self.assertTrue(out.endswith(MARKER))

    def test_custom_limit(self):
        out = _home.clamp_context("a\n" * 1000, limit=500)
        self.assertLess(len(out), 500)
        self.assertTrue(out.endswith(MARKER))


if __name__ == "__main__":
    unittest.main()
