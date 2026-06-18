#!/usr/bin/env python3
"""Tests for v3.8 _changelog.py — themed memory changelog from descriptive commits."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "hooks" / "scripts"


def _load():
    sys.path.insert(0, str(SCRIPTS_DIR))
    import _changelog
    return _changelog


CL = _load()


def _git(repo: Path, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


class TestChangelog(unittest.TestCase):
    def test_rolls_up_descriptive_commits(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp)
            _git(r, "init", "-q"); _git(r, "config", "user.email", "t@t"); _git(r, "config", "user.name", "t")
            (r / "a.md").write_text("x")
            _git(r, "add", "-A")
            msg = ("add(trade): +2 [decision] in exness-ea\n\n"
                   "- 3 files changed\n\nWorkspace: trade\nTopics: exness-ea\n"
                   "Entries: +2 decision +1 ref\nFiles: 3\n")
            _git(r, "commit", "-q", "-m", msg)
            (r / "b.md").write_text("y")
            _git(r, "add", "-A")
            _git(r, "commit", "-q", "-m", "archive(devops): forget 3 raw journals\n\nWorkspace: devops\nFiles: 3\n")
            cl = CL.build_changelog(r, days=3650)
            self.assertEqual(cl["commits"], 2)
            self.assertIn("trade", cl["workspaces"])
            self.assertIn("devops", cl["workspaces"])
            self.assertEqual(cl["workspaces"]["trade"]["entries"].get("decision"), 2)
            self.assertIn("exness-ea", cl["workspaces"]["trade"]["topics"])
            self.assertEqual(cl["workspaces"]["devops"]["types"].get("archive"), 1)
            txt = CL.render(cl)
            self.assertIn("trade", txt)
            self.assertIn("Entries:", txt)

    def test_empty_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp)
            _git(r, "init", "-q")
            cl = CL.build_changelog(r, days=7)
            self.assertEqual(cl["commits"], 0)
            self.assertIn("No memory changes", CL.render(cl))


if __name__ == "__main__":
    unittest.main()
