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

    def test_two_local_commits_both_merge_and_rebase_finishes(self):
        """Review C1: after an offline stretch (or a PreCompact --commit-only) B
        holds SEVERAL unpushed commits touching MEMORY.md. Every replayed commit
        that conflicts must be merged — not just the first — and the rebase must
        end with no markers and no stale SYNC-CONFLICT.md."""
        self.mem_a.write_text(self.mem_a.read_text() + "- note from A\n")
        git(self.a, "commit", "-q", "-am", "A note")
        git(self.a, "push", "-q", "origin", "main")
        self.mem_b.write_text(self.mem_b.read_text() + "- note from B1\n")
        git(self.b, "commit", "-q", "-am", "B note 1")
        self.mem_b.write_text(self.mem_b.read_text() + "- note from B2\n")
        git(self.b, "commit", "-q", "-am", "B note 2")
        r = git(self.b, "pull", "--rebase", "origin", "main", check=False)
        self.assertIn("CONFLICT", r.stdout + r.stderr)
        os.environ["GOWTH_MEM_HOME"] = str(self.b)
        import _conflict  # type: ignore
        result = _conflict.package_conflict()
        self.assertIsNone(result, "MEMORY.md-only conflicts across two commits must resolve")
        self.assertFalse((self.b / "SYNC-CONFLICT.md").exists())
        self.assertFalse((self.b / ".git" / "rebase-merge").exists(), "rebase left in progress")
        self.assertFalse((self.b / ".git" / "rebase-apply").exists())
        self.assertEqual(git(self.b, "diff", "--name-only", "--diff-filter=U").stdout.strip(), "")
        text = self.mem_b.read_text()
        self.assertNotIn("<<<<<<<", text)
        self.assertNotIn(">>>>>>>", text)
        import _memfile  # type: ignore
        self.assertEqual(text.count(_memfile.END), 1)
        free = text.split(_memfile.END, 1)[1]
        for line in ("- base note", "- note from A", "- note from B1", "- note from B2"):
            self.assertIn(line, free)
        st = git(self.b, "status", "--porcelain").stdout
        self.assertEqual(st.strip(), "", f"tree not clean after the rebase: {st}")
        # both local commits were replayed on top of origin/main
        ahead = git(self.b, "rev-list", "--count", "origin/main..HEAD").stdout.strip()
        self.assertEqual(ahead, "2")

    def _two_local_commits(self, b1_line: str = "- note from B1\n", b2_line: str = "- note from B2\n") -> None:
        self.mem_a.write_text(self.mem_a.read_text() + "- note from A\n")
        git(self.a, "commit", "-q", "-am", "A note")
        git(self.a, "push", "-q", "origin", "main")
        self.mem_b.write_text(self.mem_b.read_text() + b1_line)
        git(self.b, "commit", "-q", "-am", "B note 1")
        self.mem_b.write_text(self.mem_b.read_text() + b2_line)
        git(self.b, "commit", "-q", "-am", "B note 2")
        r = git(self.b, "pull", "--rebase", "origin", "main", check=False)
        self.assertIn("CONFLICT", r.stdout + r.stderr)
        os.environ["GOWTH_MEM_HOME"] = str(self.b)

    def test_replay_bound_exhausted_leaves_no_markers_and_names_memfile(self):
        """Review m3(c): giving up after MAX_REPLAYS used to leave raw markers,
        an unmerged MEMORY.md and a 0-file SYNC-CONFLICT.md."""
        self._two_local_commits()
        import _conflict  # type: ignore
        saved = _conflict.MAX_REPLAYS
        _conflict.MAX_REPLAYS = 1
        try:
            result = _conflict.package_conflict()
        finally:
            _conflict.MAX_REPLAYS = saved
        self.assertIsNotNone(result)
        body = (self.b / "SYNC-CONFLICT.md").read_text()
        self.assertIn("MEMORY.md", body)
        self.assertNotIn("on 0 file(s)", body)
        self.assertNotIn("<<<<<<<", self.mem_b.read_text())
        self.assertEqual(git(self.b, "diff", "--name-only", "--diff-filter=U").stdout.strip(), "",
                         "the last stop must be merged/reset even when the bound is hit")

    def test_concurrent_memfile_write_mid_rebase_is_restaged(self):
        """Review m3(b): another session's Stop re-rendering MEMORY.md while the
        rebase is stopped must not strand the rebase behind a 0-file conflict."""
        self._two_local_commits()
        import _conflict  # type: ignore
        real = _conflict.merge_memfile

        def racing(gh, rel):
            ok = real(gh, rel)
            f = gh / rel
            f.write_text(f.read_text() + "- concurrent note\n")      # after the stage
            return ok

        _conflict.merge_memfile = racing
        try:
            result = _conflict.package_conflict()
        finally:
            _conflict.merge_memfile = real
        self.assertIsNone(result, (self.b / "SYNC-CONFLICT.md").read_text() if (self.b / "SYNC-CONFLICT.md").exists() else "")
        self.assertNotIn("<<<<<<<", self.mem_b.read_text())
        self.assertIn("- concurrent note", self.mem_b.read_text())
        self.assertFalse((self.b / ".git" / "rebase-merge").exists())

    def test_dirty_other_file_mid_rebase_is_named_in_the_conflict(self):
        self._two_local_commits()
        import _conflict  # type: ignore
        real = _conflict.merge_memfile
        handoff = self.b / "workspaces" / "demo" / "docs" / "handoff.md"

        def dirtying(gh, rel):
            ok = real(gh, rel)
            handoff.write_text(handoff.read_text() + "- host:mac 2026-09-14 [doing] concurrent edit\n")
            return ok

        _conflict.merge_memfile = dirtying
        try:
            result = _conflict.package_conflict()
        finally:
            _conflict.merge_memfile = real
        self.assertNotIn("<<<<<<<", self.mem_b.read_text())
        self.assertIn("concurrent edit", handoff.read_text(), "a concurrent edit is never discarded")
        if result is not None:
            body = (self.b / "SYNC-CONFLICT.md").read_text()
            self.assertNotIn("on 0 file(s)", body)
            self.assertIn("handoff.md", body)

    def test_committed_deletion_survives_the_rebase_merge(self):
        """Review R1 (rebase path, pre-existing since Task 3b): the 2-way union
        of :3 and :2 brought a line B had REMOVED (committed) back after a
        conflict merge. Three-way against stage :1."""
        # shared baseline: base note + a wrong rule
        self.mem_a.write_text(self.mem_a.read_text() + "- Wrong rule — deploy on fridays\n")
        git(self.a, "commit", "-q", "-am", "baseline with a wrong rule")
        git(self.a, "push", "-q", "origin", "main")
        git(self.b, "pull", "-q", "--rebase", "origin", "main")
        # A appends a note (pushed); B commits the removal of the wrong rule
        self.mem_a.write_text(self.mem_a.read_text() + "- note from A\n")
        git(self.a, "commit", "-q", "-am", "A note")
        git(self.a, "push", "-q", "origin", "main")
        self.mem_b.write_text(self.mem_b.read_text().replace("- Wrong rule — deploy on fridays\n", ""))
        git(self.b, "commit", "-q", "-am", "B removes the wrong rule")
        r = git(self.b, "pull", "--rebase", "origin", "main", check=False)
        self.assertIn("CONFLICT", r.stdout + r.stderr)
        os.environ["GOWTH_MEM_HOME"] = str(self.b)
        import _conflict  # type: ignore
        self.assertIsNone(_conflict.package_conflict())
        text = self.mem_b.read_text()
        self.assertNotIn("Wrong rule", text, "a committed deletion must not come back")
        self.assertIn("- note from A", text)
        self.assertIn("- base note", text)
        self.assertNotIn("<<<<<<<", text)

    def test_fold_of_a_concurrent_write_is_sanitized_first(self):
        """Review R5: the `git add -u` fold staged a concurrently written raw
        secret in a tracked memory file, and pull_rebase pushed it."""
        secret = "glpat-" + "A1b2C3d4E5f6G7h8I9j0"
        note = self.b / "workspaces" / "demo" / "memory" / "note.md"
        note.write_text("clean\n")
        git(self.b, "add", "-A")
        git(self.b, "commit", "-q", "-m", "B adds a note file")
        self._two_local_commits()
        import _conflict  # type: ignore
        real = _conflict.merge_memfile

        def racing(gh, rel):
            ok = real(gh, rel)
            note.write_text(f"token {secret} pasted mid-rebase\n")
            return ok

        _conflict.merge_memfile = racing
        try:
            result = _conflict.package_conflict()
        finally:
            _conflict.merge_memfile = real
        self.assertIsNone(result)
        for ref in ("HEAD", "HEAD~1"):
            shown = git(self.b, "show", f"{ref}:workspaces/demo/memory/note.md", check=False).stdout
            self.assertNotIn(secret, shown, f"{ref} carries the raw secret")
        self.assertNotIn(secret, note.read_text())
        self.assertIn("[REDACTED", note.read_text())

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
