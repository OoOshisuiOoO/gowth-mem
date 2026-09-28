"""v4.8 Task 3b: a sync conflict on <ws>/memory/MEMORY.md merges itself.

Two machines append to Claude's free zone below the managed block and push.
The second pull --rebase conflicts on the file. Instead of packaging it into
SYNC-CONFLICT.md (AI-mediated), the free zones are unioned (local lines first,
exact-line dedupe), the block is regenerated from the vault, the file is
staged, and the rebase continues. Other conflicted files still go through
SYNC-CONFLICT.md.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "hooks" / "scripts"
sys.path.insert(0, str(SCRIPTS))

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@x", "GIT_CONFIG_NOSYSTEM": "1", "HOME": "/nonexistent"}


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "init.defaultBranch=main", "-c", "core.hooksPath=/dev/null",
                           *args], cwd=str(cwd), capture_output=True, text=True,
                          env={**os.environ, **GIT_ENV}, check=check)


def _layout(vault: Path) -> None:
    ws = vault / "workspaces" / "demo"
    (ws / "docs").mkdir(parents=True, exist_ok=True)
    (ws / "memory").mkdir(parents=True, exist_ok=True)
    (ws / "workspace.json").write_text('{"name": "demo"}')
    (ws / "docs" / "handoff.md").write_text("## 2026-09-13 s\n- host:mac 2026-09-13 [next] keep going\n")
    (vault / "shared").mkdir(exist_ok=True)
    (vault / "shared" / "secrets.md").write_text("- `FAKE_KEY`\n")
    (vault / "settings.json").write_text('{"layout_version": 3}')


class MemfileSyncMergeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gowth_merge_"))
        self.bare = self.tmp / "bare.git"
        git(self.tmp, "init", "--bare", str(self.bare))
        self.a = self.tmp / "A"
        self.b = self.tmp / "B"
        git(self.tmp, "clone", "-q", str(self.bare), str(self.a))
        _layout(self.a)
        os.environ["GOWTH_MEM_HOME"] = str(self.a)
        import _memfile  # type: ignore
        block = _memfile.render("demo")
        (self.a / "workspaces" / "demo" / "memory" / "MEMORY.md").write_text(block + "- base note\n")
        git(self.a, "add", "-A")
        git(self.a, "commit", "-q", "-m", "base")
        git(self.a, "push", "-q", "origin", "main")
        git(self.tmp, "clone", "-q", str(self.bare), str(self.b))
        self.mem_a = self.a / "workspaces" / "demo" / "memory" / "MEMORY.md"
        self.mem_b = self.b / "workspaces" / "demo" / "memory" / "MEMORY.md"

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _diverge(self, extra_b_file: bool = False) -> None:
        self.mem_a.write_text(self.mem_a.read_text() + "- note from A\n")
        git(self.a, "commit", "-q", "-am", "A note")
        git(self.a, "push", "-q", "origin", "main")
        self.mem_b.write_text(self.mem_b.read_text() + "- note from B\n")
        if extra_b_file:
            (self.b / "workspaces" / "demo" / "docs" / "handoff.md").write_text("## B version\n")
            self.mem_a  # keep linter quiet
        git(self.b, "commit", "-q", "-am", "B note")
        r = git(self.b, "pull", "--rebase", "origin", "main", check=False)
        self.assertIn("CONFLICT", r.stdout + r.stderr)

    def test_memory_md_conflict_merges_and_continues(self):
        self._diverge()
        os.environ["GOWTH_MEM_HOME"] = str(self.b)
        import _conflict  # type: ignore
        result = _conflict.package_conflict()
        self.assertIsNone(result, "MEMORY.md-only conflict must resolve without SYNC-CONFLICT.md")
        self.assertFalse((self.b / "SYNC-CONFLICT.md").exists())
        text = self.mem_b.read_text()
        import _memfile  # type: ignore
        self.assertEqual(text.count(_memfile.END), 1)
        free = text.split(_memfile.END, 1)[1]
        self.assertIn("- base note", free)
        self.assertIn("- note from A", free)
        self.assertIn("- note from B", free)
        self.assertNotIn("<<<<<<<", text)
        st = git(self.b, "status", "--porcelain").stdout
        self.assertEqual(st.strip(), "", f"rebase did not finish cleanly: {st}")
        self.assertFalse((self.b / ".git" / "rebase-merge").exists())

    def test_other_conflicts_still_packaged(self):
        # A also edits handoff.md so B's handoff.md conflicts too
        (self.a / "workspaces" / "demo" / "docs" / "handoff.md").write_text("## A version\n")
        self._diverge(extra_b_file=True)
        os.environ["GOWTH_MEM_HOME"] = str(self.b)
        import _conflict  # type: ignore
        result = _conflict.package_conflict()
        self.assertIsNotNone(result)
        body = (self.b / "SYNC-CONFLICT.md").read_text()
        self.assertIn("docs/handoff.md", body)
        self.assertNotIn("MEMORY.md", body)
        text = self.mem_b.read_text()
        self.assertNotIn("<<<<<<<", text)
        self.assertIn("- note from B", text)


if __name__ == "__main__":
    unittest.main()
