"""Round-4 N3: `run_git`'s timeout path returned `TimeoutExpired.stdout` as
bytes (it is bytes even with text=True), so a caller concatenating stdout and
stderr raised TypeError when git had printed before hanging."""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hooks" / "scripts"))

import _git  # type: ignore  # noqa: E402


class RunGitTimeoutTest(unittest.TestCase):
    def test_timeout_yields_text_stdout_and_rc_124(self):
        real = _git.subprocess.run

        def hanging(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout"), output=b"partial output", stderr=b"partial err")

        _git.subprocess.run = hanging
        try:
            r = _git.run_git(Path("."), "status", check=False, timeout=1)
        finally:
            _git.subprocess.run = real
        self.assertEqual(r.returncode, 124)
        self.assertIsInstance(r.stdout, str)
        self.assertIsInstance(r.stderr, str)
        self.assertIn("partial output", r.stdout)
        self.assertIn("timed out", r.stderr)
        (r.stderr or "") + (r.stdout or "")     # what pull_rebase does


if __name__ == "__main__":
    unittest.main()
