#!/usr/bin/env python3
"""Tests for v3.6 _commitmsg.py — deterministic commit-message generation."""
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
    import _commitmsg
    return _commitmsg


CM = _load()


def _git(repo: Path, *args: str):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True)


def _new_repo() -> Path:
    d = Path(tempfile.mkdtemp())
    _git(d, "init", "-q")
    _git(d, "config", "user.email", "t@t")
    _git(d, "config", "user.name", "t")
    return d


def _write(repo: Path, rel: str, content: str):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


class TestBuildMessage(unittest.TestCase):
    def test_add_with_typed_entries(self):
        r = _new_repo()
        _write(r, "workspaces/trade/exness-ea/2026-06-18-mm.md",
               "# MM\n\n- [decision] use 1% risk because drawdown stays under 10%\n"
               "- [ref] tick coverage 2009-2026. Source: exness export\n")
        _git(r, "add", "-A")
        msg = CM.build_message(r, host="mac", context="stop-sync")
        self.assertTrue(msg.startswith("add(trade):"), msg.splitlines()[0])
        self.assertIn("Workspace: trade", msg)
        self.assertIn("Topics: exness-ea", msg)
        self.assertIn("decision", msg)
        self.assertIn("Context: stop-sync", msg)
        self.assertIn("Machine: mac", msg)

    def test_archive_when_journals_deleted(self):
        r = _new_repo()
        for d in ("2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04"):
            _write(r, f"workspaces/default/journal/{d}.md", "raw transcript\n")
        _git(r, "add", "-A"); _git(r, "commit", "-qm", "seed")
        for d in ("2026-06-01", "2026-06-02", "2026-06-03"):
            _git(r, "rm", "-q", f"workspaces/default/journal/{d}.md")
        msg = CM.build_message(r, host="mac", context="stop-sync")
        self.assertTrue(msg.startswith("archive(default):"), msg.splitlines()[0])
        self.assertIn("forget 3 raw journal", msg)

    def test_archive_when_handoff_rotated(self):
        r = _new_repo()
        _write(r, "workspaces/trade/docs/handoff.md", "# Handoff\n")
        _git(r, "add", "-A"); _git(r, "commit", "-qm", "seed")
        _write(r, "workspaces/trade/docs/handoff.md", "# Handoff\n\n## recent\n")
        _write(r, "workspaces/trade/docs/handoff-archive.md", "# Handoff archive\n\n## old\n")
        _git(r, "add", "-A")
        msg = CM.build_message(r, host="mac")
        self.assertTrue(msg.startswith("archive(trade):"), msg.splitlines()[0])
        self.assertIn("rotate handoff", msg)

    def test_prune_when_entries_removed(self):
        r = _new_repo()
        _write(r, "workspaces/devops/docs/ref.md", "# ref\n\n- [ref] old fact. Source: x\n- [ref] another. Source: y\n- [ref] third. Source: z\n")
        _git(r, "add", "-A"); _git(r, "commit", "-qm", "seed")
        _write(r, "workspaces/devops/docs/ref.md", "# ref\n\n- [ref] third. Source: z\n")
        _git(r, "add", "-A")
        msg = CM.build_message(r, host="mac")
        self.assertTrue(msg.startswith("prune(devops):"), msg.splitlines()[0])
        self.assertIn("-2 [ref]", msg)

    def test_multi_workspace_scope(self):
        r = _new_repo()
        _write(r, "workspaces/trade/a/2026-06-18-x.md", "- [exp] something specific happened because of Y here\n")
        _write(r, "workspaces/devops/b/2026-06-18-y.md", "- [exp] another specific thing because of Z here\n")
        _git(r, "add", "-A")
        msg = CM.build_message(r, host="mac")
        self.assertTrue(msg.startswith(("add(multi):", "update(multi):")), msg.splitlines()[0])
        self.assertIn("Workspace: devops, trade", msg)

    def test_deterministic(self):
        r = _new_repo()
        _write(r, "workspaces/trade/x/2026-06-18-x.md", "- [decision] do X because Y\n")
        _git(r, "add", "-A")
        m1 = CM.build_message(r, host="mac", context="c")
        m2 = CM.build_message(r, host="mac", context="c")
        self.assertEqual(m1, m2)

    def test_empty_diff_fallback(self):
        r = _new_repo()
        msg = CM.build_message(r, host="mac", fallback="sync")
        self.assertTrue(msg.startswith("sync:"))

    def test_subject_within_72_chars(self):
        r = _new_repo()
        for i in range(8):
            _write(r, f"workspaces/trade/topic{i}/2026-06-18-a.md", f"- [decision] choice {i} because reason {i}\n")
        _git(r, "add", "-A")
        subject = CM.build_message(r, host="mac").splitlines()[0]
        self.assertLessEqual(len(subject), 72, subject)


if __name__ == "__main__":
    unittest.main()
