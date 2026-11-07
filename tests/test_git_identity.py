"""v4.9 §3.4: CI was red because of a PRODUCTION defect, not the test env.

The commit paths pass `-c user.name=gowth-mem -c user.email=gowth-mem@<host>`,
but `pull --rebase` (which REPLAYS local commits), `rebase --continue` and
`stash push` ran without it. On a machine with no global identity — the CI
runner, a fresh box, `user.useConfigOnly=true` — every diverged pull failed
and every MEMORY.md self-merge ended in SYNC-CONFLICT.md.

Every case runs with NO identity anywhere: empty HOME and XDG_CONFIG_HOME, no
system config, no GIT_AUTHOR_* / GIT_COMMITTER_* / EMAIL, and
`user.useConfigOnly=true` via GIT_CONFIG_COUNT — and proves first that git
itself refuses to commit in that env (so a pass is never vacuous).
"""
from __future__ import annotations

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

from _sync import _DEFAULT_GITIGNORE  # noqa: E402  (a second machine's --init writes the same file)

SCRUB = ("AI_AGENT", "GOWTH_WORKSPACE", "CLAUDE_SUBAGENT", "GOWTH_MEM_OFF", "CLAUDE_CODE_ENTRYPOINT",
         "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "EMAIL",
         "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_PARAMETERS", "GIT_DIR", "GIT_WORK_TREE",
         "GOWTH_MEM_GIT_TOKEN")
SETUP_ID = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@x", "GIT_CONFIG_NOSYSTEM": "1"}


def no_identity_env(tmp: Path, **extra: str) -> dict:
    home = tmp / "empty-home"
    home.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in SCRUB and not k.startswith("GIT_CONFIG_")}
    env.update({"HOME": str(home), "XDG_CONFIG_HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "user.useConfigOnly",
                "GIT_CONFIG_VALUE_0": "true"})
    env.update(extra)
    return env


def git(cwd: Path, *args: str, env: "dict | None" = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "init.defaultBranch=main", "-c", "core.hooksPath=/dev/null", *args],
                          cwd=str(cwd), capture_output=True, text=True,
                          env=env if env is not None else {**os.environ, **SETUP_ID}, check=check)


class NoIdentitySyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gowth_ident_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bare = self.tmp / "bare.git"
        git(self.tmp, "init", "-q", "--bare", "-b", "main", str(self.bare))
        self.a, self.b = self.tmp / "A", self.tmp / "B"
        git(self.tmp, "clone", "-q", str(self.bare), str(self.a))
        ws = self.a / "workspaces" / "demo"
        (ws / "docs").mkdir(parents=True)
        (ws / "workspace.json").write_text('{"name": "demo"}')
        (ws / "docs" / "handoff.md").write_text("## 2026-11-07 s\n- host:a 2026-11-07 [next] start\n")
        (self.a / "settings.json").write_text('{"layout_version": 3}')
        (self.a / ".gitignore").write_text(_DEFAULT_GITIGNORE)
        git(self.a, "add", "-A")
        git(self.a, "commit", "-q", "-m", "base")
        git(self.a, "push", "-q", "origin", "main")
        git(self.tmp, "clone", "-q", str(self.bare), str(self.b))
        # diverge: A pushes, B commits locally (both touch different files)
        (ws / "a-notes.md").write_text("from A\n")
        git(self.a, "add", "-A")
        git(self.a, "commit", "-q", "-m", "A: work")
        git(self.a, "push", "-q", "origin", "main")
        (self.b / "workspaces" / "demo" / "b-notes.md").write_text("from B\n")
        git(self.b, "add", "-A")
        git(self.b, "commit", "-q", "-m", "B: work")
        (self.b / "config.json").write_text(json.dumps({"remote": str(self.bare), "branch": "main",
                                                        "host_id": "ci-box"}))
        # an uncommitted edit so the stash path runs too
        self.untracked = self.b / "workspaces" / "demo" / "docs" / "scratch.md"
        self.untracked.write_text("uncommitted on B\n")
        self.env = no_identity_env(self.tmp, GOWTH_MEM_HOME=str(self.b))

    def test_the_env_really_has_no_identity(self):
        r = subprocess.run(["git", "var", "GIT_COMMITTER_IDENT"], capture_output=True, text=True,
                           env=self.env, cwd=str(self.b))
        self.assertNotEqual(r.returncode, 0, f"identity leaked into the test env: {r.stdout}")
        # and a plain `pull --rebase` (the pre-v4.9 call) cannot replay B's commit
        c = self.tmp / "C"
        shutil.copytree(self.b, c, symlinks=True)
        r = git(c, "pull", "--rebase", "origin", "main", env=self.env, check=False)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)

    def _assert_synced(self) -> None:
        self.assertFalse((self.b / ".git" / "rebase-merge").exists())
        self.assertFalse((self.b / ".git" / "rebase-apply").exists())
        self.assertEqual(git(self.b, "stash", "list").stdout.strip(), "", "no stash entry may be left")
        self.assertTrue((self.b / "workspaces" / "demo" / "a-notes.md").is_file(), "A's commit pulled")
        self.assertEqual(git(self.b, "rev-list", "--count", "origin/main..HEAD").stdout.strip(), "0", "pushed")
        bare_log = git(self.bare, "log", "--format=%s", "main").stdout
        self.assertIn("B: work", bare_log)
        self.assertFalse((self.b / "SYNC-CONFLICT.md").exists())

    def test_auto_sync_pull_rebase_push(self):
        r = subprocess.run([sys.executable, str(SCRIPTS / "auto-sync.py"), "--pull-rebase-push"],
                           capture_output=True, text=True, env=self.env, timeout=180)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self._assert_synced()
        # the replayed commit carries OUR identity (positive signal it was used)
        replayed = git(self.b, "log", "--format=%s|%an|%cn <%ce>", "-n", "5").stdout
        line = next(ln for ln in replayed.splitlines() if ln.startswith("B: work|"))
        self.assertEqual(line, "B: work|t|gowth-mem <gowth-mem@ci-box>")
        # commit_local swept the uncommitted file into its own commit, which was pushed too
        self.assertEqual(self.untracked.read_text(), "uncommitted on B\n")
        self.assertIn("workspaces/demo/docs/scratch.md",
                      git(self.bare, "ls-tree", "-r", "--name-only", "main").stdout)

    def test_auto_sync_pull_only(self):
        r = subprocess.run([sys.executable, str(SCRIPTS / "auto-sync.py"), "--pull-only"],
                           capture_output=True, text=True, env=self.env, timeout=180)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((self.b / ".git" / "rebase-merge").exists())
        self.assertEqual(git(self.b, "stash", "list").stdout.strip(), "")
        self.assertEqual(self.untracked.read_text(), "uncommitted on B\n", "the stashed edit came back")
        self.assertTrue((self.b / "workspaces" / "demo" / "a-notes.md").is_file())

    def test_manual_sync(self):
        r = subprocess.run([sys.executable, str(SCRIPTS / "_sync.py")],
                           capture_output=True, text=True, env=self.env, timeout=180)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self._assert_synced()

    def test_manual_sync_init_on_a_second_machine(self):
        """`_sync.py --init` commits the local vault, then pulls the remote with
        --allow-unrelated-histories --rebase: the local commit is replayed."""
        d = self.tmp / "D"
        (d / "workspaces" / "other" / "docs").mkdir(parents=True)
        (d / "workspaces" / "other" / "workspace.json").write_text('{"name": "other"}')
        (d / "workspaces" / "other" / "docs" / "handoff.md").write_text("## 2026-11-07 d\n- host:d [next] go\n")
        (d / "config.json").write_text(json.dumps({"remote": str(self.bare), "branch": "main", "host_id": "box-d"}))
        env = no_identity_env(self.tmp, GOWTH_MEM_HOME=str(d))
        r = subprocess.run([sys.executable, str(SCRIPTS / "_sync.py"), "--init"],
                           capture_output=True, text=True, env=env, timeout=180)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("init: pulled origin/main", r.stdout)
        self.assertTrue((d / "workspaces" / "demo" / "a-notes.md").is_file())
        self.assertFalse((d / ".git" / "rebase-merge").exists())
        self.assertIn("workspaces/other/docs/handoff.md",
                      git(self.bare, "ls-tree", "-r", "--name-only", "main").stdout)


class MemfileMergeWithoutIdentityTest(unittest.TestCase):
    def test_the_memfile_self_merge_cases_pass_without_an_identity(self):
        """The 5 `test_memfile_sync_merge` cases that failed in CI, run as CI
        runs them; the module itself exports no identity for `package_conflict`."""
        tmp = Path(tempfile.mkdtemp(prefix="gowth_ident_mm_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        vault = tmp / "vault"
        vault.mkdir()
        env = no_identity_env(tmp, GOWTH_MEM_HOME=str(vault), CLAUDE_CONFIG_DIR=str(tmp))
        cases = ["test_memory_md_conflict_merges_and_continues",
                 "test_two_local_commits_both_merge_and_rebase_finishes",
                 "test_concurrent_memfile_write_mid_rebase_is_restaged",
                 "test_committed_deletion_survives_the_rebase_merge",
                 "test_fold_of_a_concurrent_write_is_sanitized_first"]
        names = [f"tests.test_memfile_sync_merge.MemfileSyncMergeTest.{c}" for c in cases]
        r = subprocess.run([sys.executable, "-m", "unittest", *names], capture_output=True, text=True,
                           env=env, cwd=str(ROOT), timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        self.assertIn("Ran 5 tests", r.stderr)
        src = (ROOT / "tests" / "test_memfile_sync_merge.py").read_text()
        self.assertNotIn("os.environ[\"GIT_COMMITTER", src, "the fix belongs in the code, not the test env")
        self.assertNotIn("os.environ.update(GIT_ENV", src)


if __name__ == "__main__":
    unittest.main()


class MigrateV3CommitWithoutIdentityTest(unittest.TestCase):
    """`_migrate_v3` commits the migrated layout with `check=False`: without the
    gowth-mem identity, a machine with no global git identity skipped that
    commit silently (the layout stayed uncommitted and unsynced)."""

    def test_the_migration_commit_lands_without_an_identity(self):
        tmp = Path(tempfile.mkdtemp(prefix="gowth_ident_mig_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        home = tmp / "vault"
        fixture = ROOT / "tests" / "fixtures" / "v2-snapshot"
        shutil.copytree(fixture, home)
        git(tmp, "init", "-q", "-b", "main", str(home))
        git(home, "add", "-A")
        git(home, "commit", "-q", "-m", "seed v2 fixture")
        env = no_identity_env(tmp, GOWTH_MEM_HOME=str(home))
        # positive control: git itself refuses to commit in this env
        probe = git(home, "commit", "--allow-empty", "-q", "-m", "probe", env=env, check=False)
        self.assertNotEqual(probe.returncode, 0, "the env still has an identity — the test would be vacuous")
        out = subprocess.run([sys.executable, str(SCRIPTS / "_migrate_v3.py")], env=env, cwd=str(ROOT),
                             capture_output=True, text=True, timeout=120)
        report = json.loads(out.stdout)
        self.assertEqual(report.get("status"), "ok", report)
        self.assertTrue(report.get("git_commit"), f"the migration commit did not land: {report}")
        subject = git(home, "log", "-1", "--format=%s %ae").stdout.strip()
        self.assertTrue(subject.startswith("v3 migration "), subject)
        self.assertIn(" gowth-mem@", subject)
