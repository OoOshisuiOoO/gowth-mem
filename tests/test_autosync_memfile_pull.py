"""Review N3 (v4.8): the SessionStart `auto-sync.py --pull-only` stashed a dirty
MEMORY.md, pulled the peer's, and popped — when only the derived block
conflicted (two machines, two same-day decisions in DIFFERENT files, so the
sources merge cleanly) the pop left MEMORY.md `UU` with raw markers, every
later commit refused, every later pull failed at the stash, and no
SYNC-CONFLICT.md was written: cross-machine sync stopped silently.

Now a dirty MEMORY.md is set aside before the stash (its free zone kept, the
file restored to HEAD), and after the pull the free zones are unioned and the
block re-rendered from the merged sources.
"""
from __future__ import annotations

import datetime
import json
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
           "GIT_COMMITTER_EMAIL": "t@x", "GIT_CONFIG_NOSYSTEM": "1"}


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "init.defaultBranch=main", "-c", "core.hooksPath=/dev/null", *args],
                          cwd=str(cwd), capture_output=True, text=True,
                          env={**os.environ, **GIT_ENV}, check=check)


class MemfilePullTest(unittest.TestCase):
    def setUp(self):
        self.today = datetime.date.today().isoformat()
        self.tmp = Path(tempfile.mkdtemp(prefix="gowth_pull_"))
        self.bare = self.tmp / "bare.git"
        git(self.tmp, "init", "-q", "--bare", "-b", "main", str(self.bare))
        self.a = self.tmp / "A"
        self.b = self.tmp / "B"
        git(self.tmp, "clone", "-q", str(self.bare), str(self.a))
        ws = self.a / "workspaces" / "demo"
        (ws / "docs").mkdir(parents=True)
        (ws / "memory").mkdir()
        (ws / "ingress").mkdir()
        (ws / "workspace.json").write_text('{"name": "demo"}')
        (self.a / "shared").mkdir()
        (self.a / "shared" / "secrets.md").write_text("- `FAKE_KEY`\n")
        (self.a / "settings.json").write_text('{"layout_version": 3}')
        (self.a / ".gitignore").write_text("state.json\nindex.db\n.locks/\nconfig.json\n")
        (ws / "docs" / "handoff.md").write_text(f"## {self.today} s\n- host:base {self.today} [next] start\n")
        (ws / "ingress" / "00-README.md").write_text(
            f"---\nslug: ingress\ntitle: Ingress\ntype: topic\nlast_touched: {self.today}\n---\n# Ingress\n\nGateway choices.\n")
        os.environ["GOWTH_MEM_HOME"] = str(self.a)
        import _memfile  # type: ignore
        self._memfile = _memfile
        _memfile.write("demo")
        (ws / "memory" / "MEMORY.md").write_text((ws / "memory" / "MEMORY.md").read_text() + "- base note\n")
        git(self.a, "add", "-A")
        git(self.a, "commit", "-q", "-m", "base")
        git(self.a, "push", "-q", "origin", "main")
        git(self.tmp, "clone", "-q", str(self.bare), str(self.b))
        # A: a decision today, pushed
        (ws / "ingress" / f"{self.today}-a-choice.md").write_text(
            "---\nslug: ingress-a\n---\n## [decision] Use pelican-router at the edge\n\nRationale: fewer hops.\n")
        _memfile.write("demo")
        git(self.a, "add", "-A")
        git(self.a, "commit", "-q", "-m", "A decision")
        git(self.a, "push", "-q", "origin", "main")
        # B: a different decision today, re-rendered by its Stop hook, NOT committed (autosync debounced)
        os.environ["GOWTH_MEM_HOME"] = str(self.b)
        (self.b / "workspaces" / "demo" / "ingress" / f"{self.today}-b-choice.md").write_text(
            "---\nslug: ingress-b\n---\n## [decision] Keep nginx for internal traffic\n\nRationale: already tuned.\n")
        _memfile.write("demo")
        self.mem_b = self.b / "workspaces" / "demo" / "memory" / "MEMORY.md"
        self.mem_b.write_text(self.mem_b.read_text() + "- B's own note\n")
        (self.b / "config.json").write_text(json.dumps({"remote": str(self.bare), "branch": "main"}))
        self.env = {**os.environ, **GIT_ENV, "GOWTH_MEM_HOME": str(self.b)}
        self.env.pop("GOWTH_WORKSPACE", None)
        self.env.pop("AI_AGENT", None)

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _pull_only(self) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPTS / "auto-sync.py"), "--pull-only"],
                              capture_output=True, text=True, env=self.env, timeout=120)

    def test_dirty_memfile_survives_the_session_start_pull(self):
        self.assertIn("MEMORY.md", git(self.b, "status", "--porcelain").stdout)
        r = self._pull_only()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        text = self.mem_b.read_text()
        self.assertNotIn("<<<<<<<", text)
        self.assertNotIn(">>>>>>>", text)
        self.assertEqual(git(self.b, "diff", "--name-only", "--diff-filter=U").stdout.strip(), "")
        self.assertEqual(git(self.b, "stash", "list").stdout.strip(), "", "no stash entry may be left behind")
        block, free = self._memfile.split(text)
        self.assertIn("Use pelican-router at the edge", block)
        self.assertIn("Keep nginx for internal traffic", block)
        self.assertIn("- B's own note", free)
        self.assertIn("- base note", free)
        # the pulled commit is in
        self.assertTrue((self.b / "workspaces" / "demo" / "ingress" / f"{self.today}-a-choice.md").is_file())
        # and the next commit path is not blocked
        r2 = subprocess.run([sys.executable, str(SCRIPTS / "auto-sync.py"), "--commit-only"],
                            capture_output=True, text=True, env=self.env, timeout=120)
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertEqual(git(self.b, "status", "--porcelain").stdout.strip(), "")

    def _commit_b_free_zone(self, lines: list) -> None:
        """Commit B's MEMORY.md with these free-zone lines (B's own baseline)."""
        block = self._memfile.split(self.mem_b.read_text())[0]
        self.mem_b.write_text(block + "".join(l + "\n" for l in lines))
        git(self.b, "add", "-A")
        git(self.b, "commit", "-q", "-m", "B baseline")

    def test_local_deletions_and_edits_survive_the_pull(self):
        """Review R1: the 2-way union brought back every free-zone line Claude
        deleted or edited since the last commit (the peer's HEAD copy still
        held them). The merge is 3-way against HEAD: a line the LOCAL side
        removed stays removed; blank lines are kept."""
        self._commit_b_free_zone(["- [Testing](testing.md) — run the suite before committing",
                                  "", "- [Old deploy notes](deploy_old.md) — legacy", "- keep me"])
        # Claude edits one index line and deletes another (nothing committed yet)
        block = self._memfile.split(self.mem_b.read_text())[0]
        self.mem_b.write_text(block + "- [Testing](testing.md) — run the INTEGRATION suite, Postgres 16\n\n- keep me\n")
        r = self._pull_only()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        text = self.mem_b.read_text()
        block, free = self._memfile.split(text)
        self.assertEqual(free, "- [Testing](testing.md) — run the INTEGRATION suite, Postgres 16\n\n- keep me\n",
                         "free zone must be byte-identical when the peer changed nothing in it")
        self.assertNotIn("run the suite before committing", text)
        self.assertNotIn("deploy_old.md", text)
        self.assertIn("Use pelican-router at the edge", block)

    def test_peer_additions_and_deletions_are_honoured(self):
        # baseline shared by both: L1, L2; A deletes L2 and adds LA; B (dirty) adds LB
        git(self.b, "checkout", "--", ".")                        # drop setUp's dirty render
        (self.b / "workspaces" / "demo" / "ingress" / f"{self.today}-b-choice.md").unlink()
        git(self.b, "pull", "-q", "--rebase", "origin", "main")   # B is behind A's decision
        self._commit_b_free_zone(["- L1", "- L2"])
        git(self.b, "push", "-q", "origin", "main")
        git(self.a, "pull", "-q", "--rebase", "origin", "main")
        mem_a = self.a / "workspaces" / "demo" / "memory" / "MEMORY.md"
        block_a = self._memfile.split(mem_a.read_text())[0]
        mem_a.write_text(block_a + "- L1\n- LA\n")
        git(self.a, "commit", "-q", "-am", "A deletes L2, adds LA")
        git(self.a, "push", "-q", "origin", "main")
        block = self._memfile.split(self.mem_b.read_text())[0]
        self.mem_b.write_text(block + "- L1\n- L2\n- LB\n")
        r = self._pull_only()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        free = self._memfile.split(self.mem_b.read_text())[1]
        self.assertEqual(free, "- L1\n- LB\n- LA\n")

    def test_staged_memfile_is_set_aside_too(self):
        """Review R2: `git checkout -- <rel>` restores from the INDEX, so a
        staged MEMORY.md (commit_local refusing on a stray marker elsewhere
        leaves it staged) still went through the stash and N3 recurred."""
        git(self.b, "add", "--", "workspaces/demo/memory/MEMORY.md")
        self.assertTrue(git(self.b, "status", "--porcelain").stdout.startswith("M "))
        r = self._pull_only()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(git(self.b, "diff", "--name-only", "--diff-filter=U").stdout.strip(), "")
        self.assertEqual(git(self.b, "stash", "list").stdout.strip(), "")
        self.assertIn("- B's own note", self.mem_b.read_text())

    def test_stranded_sidecar_from_a_killed_pull_is_recovered(self):
        """Review R3: a pull killed between set-aside and restore left Claude's
        notes only in .locks/pullsave-<ws>.md; nothing read it back and the
        next set-aside overwrote it."""
        sidecar = self.b / ".locks" / "pullsave-demo.md"
        sidecar.parent.mkdir(exist_ok=True)
        sidecar.write_text("- NOTE-1 rescued from a killed pull\n")
        r = self._pull_only()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        text = self.mem_b.read_text()
        self.assertIn("- NOTE-1 rescued from a killed pull", text)
        self.assertIn("- B's own note", text)
        self.assertFalse(sidecar.exists(), "the sidecar is consumed once its notes are back")

    def test_set_aside_waits_for_the_memfile_lock(self):
        """Review R4: prepare() can re-render MEMORY.md between the set-aside and
        the stash (first start after an upgrade); the set-aside holds the
        memfile lock, so a concurrent _memfile.write waits instead."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("gowth_autosync_r4", SCRIPTS / "auto-sync.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        from _lock import file_lock  # type: ignore
        held: list = []
        with file_lock("memfile-demo", timeout=1.0):
            saved = mod._set_aside_memfiles(self.b, True, held, lock_timeout=0.2)
        self.assertEqual(saved, [], "with the lock held elsewhere the file is left alone (old path, loud)")
        self.assertIn("- B's own note", self.mem_b.read_text())
        saved = mod._set_aside_memfiles(self.b, True, held, lock_timeout=1.0)
        try:
            self.assertEqual(len(saved), 1)
            self.assertEqual(len(held), 1, "the memfile lock stays held until the restore")
            with self.assertRaises(TimeoutError):
                with file_lock("memfile-demo", timeout=0.2):
                    pass
        finally:
            mod._restore_memfiles(self.b, saved, True, held)
        with file_lock("memfile-demo", timeout=0.5):
            pass

    def test_untracked_memfile_added_upstream_still_merges(self):
        # B never committed a MEMORY.md; the remote adds one → the pull used to refuse
        git(self.b, "rm", "-q", "--cached", "workspaces/demo/memory/MEMORY.md")
        git(self.b, "commit", "-q", "-m", "B drops the memfile from the index")
        r = self._pull_only()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        text = self.mem_b.read_text()
        self.assertNotIn("<<<<<<<", text)
        self.assertIn("- B's own note", text)
        self.assertEqual(git(self.b, "diff", "--name-only", "--diff-filter=U").stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
