"""Round-4 review P1 (pre-existing since v2.9.1): when the pull FAILED without
starting a rebase (offline, bad token, DNS, the v4.8 120 s timeout),
`_restore_stash(pull_ok=False)` kept the auto-stash forever — every
uncommitted vault edit vanished from the working tree into stash@{0}, the
next commit lacked it, and no later sync brought it back or pushed it.
The stash is popped whenever no rebase is in progress.
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

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@x", "GIT_CONFIG_NOSYSTEM": "1"}


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "init.defaultBranch=main", "-c", "core.hooksPath=/dev/null", *args],
                          cwd=str(cwd), capture_output=True, text=True,
                          env={**os.environ, **GIT_ENV}, check=check)


class OfflinePullTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gowth_offline_"))
        self.v = self.tmp / "vault"
        ws = self.v / "workspaces" / "demo"
        (ws / "docs").mkdir(parents=True)
        (ws / "t").mkdir()
        (ws / "workspace.json").write_text('{"name": "demo"}')
        (ws / "docs" / "handoff.md").write_text("## 2026-09-13 s\n- host:mac 2026-09-13 [next] start\n")
        (self.v / "settings.json").write_text('{"layout_version": 3}')
        (self.v / ".gitignore").write_text("state.json\nindex.db\n.locks/\nconfig.json\n")
        git(self.v, "init", "-q")
        git(self.v, "add", "-A")
        git(self.v, "commit", "-q", "-m", "base")
        # a remote nobody can reach
        (self.v / "config.json").write_text(json.dumps({"remote": str(self.tmp / "no-such-remote.git"), "branch": "main"}))
        git(self.v, "remote", "add", "origin", str(self.tmp / "no-such-remote.git"))
        # uncommitted memory: a handoff edit and a new aspect
        self.handoff = ws / "docs" / "handoff.md"
        self.handoff.write_text(self.handoff.read_text() + "- host:mac 2026-09-14 [blocker] token expired\n")
        self.aspect = ws / "t" / "2026-09-14-new.md"
        self.aspect.write_text("---\nslug: t-new\n---\n- [exp] offline edit must survive\n")
        self.env = {**os.environ, **GIT_ENV, "GOWTH_MEM_HOME": str(self.v)}
        self.env.pop("GOWTH_WORKSPACE", None)
        self.env.pop("AI_AGENT", None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _sync(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPTS / "auto-sync.py"), *args],
                              capture_output=True, text=True, env=self.env, timeout=180)

    def test_failed_pull_without_a_rebase_restores_the_working_tree(self):
        r = self._sync("--pull-only")
        self.assertNotEqual(r.returncode, 0, "the pull must report the unreachable remote")
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("[blocker] token expired", self.handoff.read_text(), "the handoff edit vanished")
        self.assertTrue(self.aspect.is_file(), "the new aspect vanished")
        self.assertEqual(git(self.v, "stash", "list").stdout.strip(), "", "no stash entry may be left behind")
        r2 = self._sync("--commit-only")
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertIn("token expired", git(self.v, "show", "HEAD:workspaces/demo/docs/handoff.md").stdout)


if __name__ == "__main__":
    unittest.main()
