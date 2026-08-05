"""Tests for the conflict-marker commit guard (v4.6, data-corruption fix).

CONFIRMED live defect, not hypothetical. `auto-sync._stash_if_dirty` stashes a dirty
tree before pulling; when `git stash pop` then conflicts, `_restore_stash` only LOGGED
(its message even claimed "changes safe in stash") while leaving raw `<<<<<<<` markers
in the working tree, and `pull_rebase` still returned 0. The next `commit_local` did a
bare `git add -A` with no unmerged-path check, so the corruption was committed and
PUSHED to the remote — where the other machine pulled it.

Live evidence at the time of the fix: 16 commits in vault history introduced the
`<<<<<<< Updated upstream` marker string, several remediation commits reference
"occ#3", and one tracked file was corrupt at HEAD.

The guard is deliberately at the COMMIT boundary, not only at the stash boundary: it
is the last gate before corruption becomes shared state, so it protects against every
cause (stash pop, aborted rebase, a hand-edited file), not just the one we found.
"""
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "hooks" / "scripts"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


class MarkerGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_marker_")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        self.home = Path(self.tmp)
        (self.home / "workspaces" / "demo").mkdir(parents=True, exist_ok=True)
        _git(self.home, "init", "-b", "main")
        _git(self.home, "config", "user.email", "t@t")
        _git(self.home, "config", "user.name", "t")
        (self.home / "workspaces" / "demo" / "a.md").write_text("clean line\n")
        _git(self.home, "add", "-A")
        _git(self.home, "commit", "-m", "seed")
        self.mod = load_module("gowth_autosync_mg", SCRIPTS / "auto-sync.py")

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _head_count(self):
        r = _git(self.home, "rev-list", "--count", "HEAD")
        return int(r.stdout.strip() or 0)

    def test_clean_changes_still_commit(self):
        (self.home / "workspaces" / "demo" / "a.md").write_text("clean line\nmore\n")
        before = self._head_count()
        self.assertTrue(self.mod.commit_local(self.home, "h", quiet=True))
        self.assertEqual(self._head_count(), before + 1)

    def test_refuses_to_commit_conflict_markers(self):
        (self.home / "workspaces" / "demo" / "a.md").write_text(
            "before\n<<<<<<< Updated upstream\nmine\n=======\ntheirs\n"
            ">>>>>>> Stashed changes\nafter\n")
        before = self._head_count()
        self.assertFalse(self.mod.commit_local(self.home, "h", quiet=True),
                         "must refuse to commit a file containing conflict markers")
        self.assertEqual(self._head_count(), before, "no commit may be created")

    def test_marker_file_is_not_left_staged(self):
        """A staged-but-uncommitted marker file would be committed by the NEXT run."""
        (self.home / "workspaces" / "demo" / "a.md").write_text(
            "x\n<<<<<<< Updated upstream\na\n=======\nb\n>>>>>>> Stashed changes\n")
        self.mod.commit_local(self.home, "h", quiet=True)
        staged = _git(self.home, "diff", "--cached", "--name-only").stdout.split()
        self.assertNotIn("workspaces/demo/a.md", staged)

    def test_unmerged_paths_block_the_commit(self):
        """`git ls-files -u` non-empty means a merge/rebase is mid-flight."""
        (self.home / "workspaces" / "demo" / "b.md").write_text("base\n")
        _git(self.home, "add", "-A"); _git(self.home, "commit", "-m", "b")
        _git(self.home, "checkout", "-b", "other")
        (self.home / "workspaces" / "demo" / "b.md").write_text("other side\n")
        _git(self.home, "commit", "-am", "other")
        _git(self.home, "checkout", "main")
        (self.home / "workspaces" / "demo" / "b.md").write_text("main side\n")
        _git(self.home, "commit", "-am", "main")
        merge = _git(self.home, "merge", "other")
        self.assertNotEqual(merge.returncode, 0, "fixture must actually conflict")
        before = self._head_count()
        self.assertFalse(self.mod.commit_local(self.home, "h", quiet=True))
        self.assertEqual(self._head_count(), before)

    def test_markers_only_inside_a_code_fence_are_still_refused(self):
        """Conservative on purpose: a false refusal is recoverable, a pushed
        corruption is not."""
        (self.home / "workspaces" / "demo" / "a.md").write_text(
            "text\n```\n<<<<<<< Updated upstream\n```\n")
        self.assertFalse(self.mod.commit_local(self.home, "h", quiet=True))

    def test_equals_line_alone_does_not_block(self):
        """`=======` is legal markdown (setext h2 underline) — only the real
        `<<<<<<< ` / `>>>>>>> ` sentinels may block a commit."""
        (self.home / "workspaces" / "demo" / "a.md").write_text(
            "Heading\n=======\n\nbody\n")
        self.assertTrue(self.mod.commit_local(self.home, "h", quiet=True))


class RestoreStashSignalTest(unittest.TestCase):
    """A failed `stash pop` must be reported as a FAILURE, not swallowed."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_stash_")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        self.home = Path(self.tmp)
        self.home.mkdir(parents=True, exist_ok=True)
        self.mod = load_module("gowth_autosync_st", SCRIPTS / "auto-sync.py")

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pop_conflict_returns_false(self):
        class R:
            returncode = 1
            stdout = "CONFLICT (content): Merge conflict in workspaces/demo/a.md"
            stderr = ""
        self.mod.run_git = lambda *a, **k: R()          # type: ignore
        self.assertFalse(self.mod._restore_stash(self.home, True, quiet=True))

    def test_successful_pop_returns_true(self):
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        self.mod.run_git = lambda *a, **k: R()          # type: ignore
        self.assertTrue(self.mod._restore_stash(self.home, True, quiet=True))



class RawCaptureSanitizedTest(unittest.TestCase):
    """Raw conversation text entering the synced vault must pass the privacy filter.

    `_atomic.py`'s own docstring makes `safe_write` mandatory for any `.md` write under
    `workspaces/`/`shared/`, but `_capture.py` and `precompact-flush.py` — the two RAW
    transcript paths — used `atomic_write`, bypassing `_privacy.sanitize`. A live
    GitLab-PAT-shaped string reached the private remote through a session-capture file
    as a result.
    """

    def test_capture_uses_safe_write(self):
        src = (SCRIPTS / "_capture.py").read_text()
        self.assertIn("safe_write(target", src,
                      "_capture.py must sanitize raw turn text before it syncs")

    def test_precompact_flush_uses_safe_write(self):
        src = (SCRIPTS / "precompact-flush.py").read_text()
        self.assertIn("safe_write(target", src,
                      "precompact-flush.py must sanitize the raw dump before it syncs")

    def test_no_synced_md_writer_uses_bare_atomic_write(self):
        """Guard the whole class of defect, not just the two known files."""
        offenders = []
        for name in ("_capture.py", "precompact-flush.py", "_topic.py", "_lesson.py"):
            src = (SCRIPTS / name).read_text()
            for i, line in enumerate(src.splitlines(), 1):
                s = line.strip()
                if s.startswith("atomic_write(") and "target" in s:
                    offenders.append(f"{name}:{i}")
        self.assertEqual(offenders, [],
                         f"synced .md writes must use safe_write: {offenders}")

if __name__ == "__main__":
    unittest.main()
