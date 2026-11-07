"""Review safety-sync#4: the resolve flow finishes through `_conflict.py
--continue`, never a bare `git rebase --continue`.

v4.9 (spec §3.4) passed the gowth-mem identity to every git call that can
create a commit, but the resolve flow still told the model to run a bare
`git rebase --continue`. On a machine with no git identity (user.useConfigOnly,
a fresh box) it failed with "Committer identity unknown"; the skill dropped
git's message, re-packaged a conflict that no longer existed and asked the
same questions again, forever. `_conflict.py --continue` runs under the sync
lock, sanitizes, stages and continues as gowth-mem@<host>: exit 0 done, 2 a
NEW conflict (only when git left unmerged paths), 1 anything else.

Every case runs with NO git identity anywhere (empty HOME, no system config,
`user.useConfigOnly=true`) and proves first that git itself refuses.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "hooks" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from _sync import _DEFAULT_GITIGNORE  # noqa: E402

SCRUB = ("AI_AGENT", "GOWTH_WORKSPACE", "CLAUDE_SUBAGENT", "GOWTH_MEM_OFF", "CLAUDE_CODE_ENTRYPOINT",
         "CLAUDE_PLUGIN_ROOT", "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
         "GIT_COMMITTER_EMAIL", "EMAIL", "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_EDITOR",
         "GOWTH_MEM_GIT_TOKEN")
SETUP_ID = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@x", "GIT_CONFIG_NOSYSTEM": "1"}
SECRET = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
MARK = "[REDACTED:github-pat]"

REF = "workspaces/demo/docs/ref.md"
BASE = "# ref\n\n- [ref] heron cache size is 64 MB. Source: heron docs v1\n- [ref] eviction is LRU. Source: v1\n"
FROM_A = BASE.replace("64 MB", "128 MB").replace("is LRU", "is ARC")
FROM_B1 = BASE.replace("64 MB", "256 MB")
FROM_B2 = FROM_B1.replace("is LRU", "is FIFO")


def no_identity_env(tmp: Path, **extra: str) -> dict:
    home = tmp / "empty-home"
    home.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in SCRUB and not k.startswith("GIT_CONFIG_")}
    env.update({"HOME": str(home), "XDG_CONFIG_HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "user.useConfigOnly",
                "GIT_CONFIG_VALUE_0": "true", "GOWTH_MEM_NO_AUTOHEAL": "1",
                "CLAUDE_CONFIG_DIR": str(tmp / "claude-config")})
    env.update(extra)
    return env


def git(cwd: Path, *args: str, env: "dict | None" = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "init.defaultBranch=main", "-c", "core.hooksPath=/dev/null", *args],
                          cwd=str(cwd), capture_output=True, text=True,
                          env=env if env is not None else {**os.environ, **SETUP_ID}, check=check)


class _Conflict(unittest.TestCase):
    """A on the remote and B locally both edit ref.md; B has no git identity."""

    two_local_commits = False
    gitignore = _DEFAULT_GITIGNORE

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gowth_resolve_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bare = self.tmp / "bare.git"
        git(self.tmp, "init", "-q", "--bare", "-b", "main", str(self.bare))
        self.a, self.b = self.tmp / "A", self.tmp / "B"
        git(self.tmp, "clone", "-q", str(self.bare), str(self.a))
        ws = self.a / "workspaces" / "demo"
        (ws / "docs").mkdir(parents=True)
        (ws / "workspace.json").write_text('{"name": "demo"}')
        (self.a / REF).write_text(BASE)
        (self.a / "settings.json").write_text('{"layout_version": 3}')
        (self.a / ".gitignore").write_text(self.gitignore)
        git(self.a, "add", "-A")
        git(self.a, "commit", "-q", "-m", "base")
        git(self.a, "push", "-q", "origin", "main")
        git(self.tmp, "clone", "-q", str(self.bare), str(self.b))
        (self.a / REF).write_text(FROM_A)
        git(self.a, "commit", "-q", "-am", "A: ref edit")
        git(self.a, "push", "-q", "origin", "main")
        (self.b / REF).write_text(FROM_B1)
        git(self.b, "commit", "-q", "-am", "B: ref edit")
        if self.two_local_commits:
            (self.b / REF).write_text(FROM_B2)
            git(self.b, "commit", "-q", "-am", "B: second ref edit")
        (self.b / "config.json").write_text(json.dumps({"remote": str(self.bare), "branch": "main",
                                                        "host_id": "box-b"}))
        self.env = no_identity_env(self.tmp, GOWTH_MEM_HOME=str(self.b))
        self.cm = self.b / "SYNC-CONFLICT.md"

    def run_script(self, script: str, *args: str) -> subprocess.CompletedProcess:
        r = subprocess.run([sys.executable, str(SCRIPTS / script), *args], capture_output=True, text=True,
                           env=self.env, timeout=180)
        self.assertNotIn("Traceback", r.stderr)
        return r

    def mid_rebase(self) -> bool:
        return (self.b / ".git" / "rebase-merge").exists() or (self.b / ".git" / "rebase-apply").exists()

    def conflict(self) -> None:
        r = self.run_script("auto-sync.py", "--pull-rebase-push")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertTrue(self.cm.is_file())
        self.assertTrue(self.mid_rebase())
        self.assertEqual(git(self.b, "diff", "--name-only", "--diff-filter=U").stdout.strip(), "",
                         "package_conflict stages what it packages")

    def keep(self, text: str) -> None:
        """The skill's apply step (atomic_write of the chosen text)."""
        from _atomic import atomic_write  # type: ignore
        atomic_write(self.b / REF, text)

    def cont(self) -> subprocess.CompletedProcess:
        return self.run_script("_conflict.py", "--continue")

    def committer(self, subject: str) -> str:
        log = git(self.b, "log", "--format=%s|%an|%cn <%ce>", "-n", "10").stdout
        return next(ln for ln in log.splitlines() if ln.startswith(subject + "|"))


class ContinueWithoutIdentityTest(_Conflict):
    def test_the_env_really_has_no_identity(self):
        r = subprocess.run(["git", "var", "GIT_COMMITTER_IDENT"], capture_output=True, text=True,
                           env=self.env, cwd=str(self.b))
        self.assertNotEqual(r.returncode, 0, f"identity leaked into the test env: {r.stdout}")

    def test_keep_local_continue_and_push(self):
        self.conflict()
        report = self.cm.read_text()
        self.assertIn("_conflict.py", report)
        self.assertIn(" --continue", report)
        self.assertNotRegex(report, r"rebase\s+--continue", "SYNC-CONFLICT.md prescribes a bare continue")
        # positive control: the command the docs used to prescribe fails here
        bare = git(self.b, "rebase", "--continue", env={**self.env, "GIT_EDITOR": "true"}, check=False)
        self.assertNotEqual(bare.returncode, 0, bare.stdout + bare.stderr)
        self.assertIn("who you are", bare.stdout + bare.stderr)
        self.assertTrue(self.mid_rebase())
        self.keep(FROM_B1)                                    # keep-local
        r = self.cont()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("rebase finished", r.stdout)
        self.assertFalse(self.mid_rebase())
        self.assertEqual(self.committer("B: ref edit"), "B: ref edit|t|gowth-mem <gowth-mem@box-b>")
        self.assertEqual((self.b / REF).read_text(), FROM_B1)
        # the documented step 4: delete the report, push with _sync.py
        self.cm.unlink()
        r = self.run_script("_sync.py")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("sync: pushed to origin/main", r.stdout)
        self.assertEqual(git(self.bare, "show", f"main:{REF}").stdout, FROM_B1)
        pushed = git(self.bare, "log", "--format=%s|%cn <%ce>", "main").stdout
        self.assertIn("B: ref edit|gowth-mem <gowth-mem@box-b>", pushed)
        self.assertIn("A: ref edit|", pushed)
        self.assertEqual(git(self.b, "stash", "list").stdout.strip(), "")

    def test_keep_remote_drops_the_emptied_replay(self):
        self.conflict()
        self.keep(FROM_A)                                     # keep-remote
        r = self.cont()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.mid_rebase())
        self.assertNotIn("B: ref edit", git(self.b, "log", "--format=%s").stdout)
        self.assertEqual(git(self.b, "rev-parse", "HEAD").stdout, git(self.b, "rev-parse", "origin/main").stdout)
        self.assertEqual((self.b / REF).read_text(), FROM_A)

    def test_no_rebase_in_progress_is_a_clean_no_op(self):
        head = git(self.b, "rev-parse", "HEAD").stdout
        r = self.cont()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("no rebase in progress", r.stdout)
        self.assertEqual(git(self.b, "rev-parse", "HEAD").stdout, head)

    def test_unmerged_paths_are_refused_not_committed(self):
        """A stop nobody packaged (a pull run by hand) still holds raw markers:
        `add -A` would have committed them."""
        git(self.b, "pull", "--rebase", "origin", "main", check=False)        # leaves REF unmerged
        self.assertTrue(self.mid_rebase())
        head = git(self.b, "rev-parse", "HEAD").stdout
        r = self.cont()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("still unmerged", r.stderr)
        self.assertEqual(git(self.b, "rev-parse", "HEAD").stdout, head)
        self.assertIn(REF, git(self.b, "diff", "--name-only", "--diff-filter=U").stdout)
        self.assertIn("<<<<<<<", (self.b / REF).read_text())

    def test_a_secret_in_the_resolution_is_redacted_and_a_held_lock_refuses(self):
        from _lock import file_lock  # type: ignore
        self.conflict()
        merged = FROM_B1.replace("Source: v1", f"Source: v1, token {SECRET}")
        self.keep(merged)                                     # a merge proposal that carries a token
        head = git(self.b, "rev-parse", "HEAD").stdout
        with mock.patch.dict(os.environ, {"GOWTH_MEM_HOME": str(self.b)}):
            with file_lock("entries-demo", timeout=1.0):
                r = self.cont()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("refusing", r.stderr)
        self.assertTrue(self.mid_rebase())
        self.assertEqual(git(self.b, "rev-parse", "HEAD").stdout, head)
        r = self.cont()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("redacted 1 secret(s)", r.stdout)
        shown = git(self.b, "show", f"HEAD:{REF}").stdout
        self.assertNotIn(SECRET, shown)
        self.assertIn(MARK, shown)
        self.assertIn("256 MB", shown)

    def test_a_failure_that_is_not_a_conflict_prints_gits_message(self):
        """The old skill re-packaged ANY failed continue as a new conflict: the
        report was rewritten from nothing unmerged and the user was asked the
        same questions again, forever."""
        self.conflict()
        self.keep(FROM_B1)
        report = self.cm.read_bytes()
        with mock.patch.dict(os.environ, {"GOWTH_MEM_HOME": str(self.b)}):
            import _conflict  # type: ignore
            real = _conflict._git

            def failing(cwd, *args, **kw):
                if args[-2:] == ("rebase", "--continue"):
                    return 1, "", "fatal: simulated failure that is not a conflict"
                return real(cwd, *args, **kw)

            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(_conflict, "_git", failing), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = _conflict.continue_rebase()
        self.assertEqual(rc, 1, out.getvalue() + err.getvalue())
        self.assertIn("simulated failure that is not a conflict", err.getvalue())
        self.assertNotIn("NEW conflict", err.getvalue())
        self.assertEqual(self.cm.read_bytes(), report, "nothing new conflicted: the report is not rewritten")
        self.assertTrue(self.mid_rebase())

    def test_a_held_sync_lock_refuses(self):
        self.conflict()
        from _lock import file_lock  # type: ignore
        with mock.patch.dict(os.environ, {"GOWTH_MEM_HOME": str(self.b)}):
            import _conflict  # type: ignore
            err = io.StringIO()
            with file_lock("sync", timeout=1.0), contextlib.redirect_stderr(err):
                rc = _conflict.continue_rebase(lock_timeout=0.2)
        self.assertEqual(rc, 1)
        self.assertIn("sync lock", err.getvalue())
        self.assertTrue(self.mid_rebase())


class OlderGitignoreTest(_Conflict):
    """A vault whose .gitignore predates the SYNC-CONFLICT.md line (it is in
    the default only, not in the backfilled required ignores)."""

    gitignore = _DEFAULT_GITIGNORE.replace("SYNC-CONFLICT.md\n", "")

    def test_the_report_is_never_committed(self):
        self.assertNotIn("SYNC-CONFLICT.md", (self.b / ".gitignore").read_text())
        self.conflict()
        self.keep(FROM_B1)
        r = self.cont()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.mid_rebase())
        self.assertNotIn("SYNC-CONFLICT.md", git(self.b, "ls-tree", "-r", "--name-only", "HEAD").stdout)
        self.assertIn("?? SYNC-CONFLICT.md", git(self.b, "status", "--porcelain").stdout)
        self.assertEqual(self.committer("B: ref edit"), "B: ref edit|t|gowth-mem <gowth-mem@box-b>")


class LaterStopTest(_Conflict):
    two_local_commits = True

    def test_a_later_replayed_commit_stopping_is_a_new_conflict(self):
        self.conflict()
        first = self.cm.read_text()
        self.assertIn("256 MB", first)
        self.keep(FROM_A.replace("128 MB", "256 MB"))         # merge: B's size, A's eviction
        r = self.cont()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("NEW conflict", r.stderr)
        self.assertTrue(self.mid_rebase())
        second = self.cm.read_text()
        self.assertNotEqual(second, first, "SYNC-CONFLICT.md must describe the new stop")
        self.assertIn("FIFO", second)
        self.assertNotRegex(second, r"rebase\s+--continue")
        self.assertEqual(self.committer("B: ref edit"), "B: ref edit|t|gowth-mem <gowth-mem@box-b>")
        self.keep(FROM_B2)                                    # keep-local
        r = self.cont()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.mid_rebase())
        self.assertEqual(self.committer("B: second ref edit"),
                         "B: second ref edit|t|gowth-mem <gowth-mem@box-b>")
        self.assertEqual((self.b / REF).read_text(), FROM_B2)


# ── the doc surface ──────────────────────────────────────────────────────────

DOC_DIRS = (ROOT / "commands", ROOT / "skills", ROOT / "templates")
RESOLVE_DOCS = (ROOT / "templates" / "ops" / "sync-resolve.md", ROOT / "skills" / "mem-sync-resolve" / "SKILL.md",
                ROOT / "skills" / "mem-sync" / "SKILL.md", ROOT / "commands" / "mem-sync.md")
BARE_CONTINUE = re.compile(r"rebase\s+--continue")


def _bare_continues(text: str) -> list:
    """Lines that run `rebase --continue` without the gowth-mem identity."""
    return [ln for ln in text.splitlines() if BARE_CONTINUE.search(ln) and "user.email=" not in ln]


class DocSurfaceTest(unittest.TestCase):
    def test_no_bare_rebase_continue_in_commands_skills_or_templates(self):
        offenders = []
        for d in DOC_DIRS:
            for p in sorted(d.rglob("*")):
                if not p.is_file() or "__pycache__" in p.parts:
                    continue
                text = p.read_text(encoding="utf-8", errors="ignore")
                offenders += [f"{p.relative_to(ROOT)}: {ln.strip()}" for ln in _bare_continues(text)]
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_the_resolve_docs_name_the_continue_command(self):
        for p in RESOLVE_DOCS:
            text = p.read_text(encoding="utf-8")
            self.assertIn('/hooks/scripts/_conflict.py" --continue', text, p.name)
            self.assertNotIn("git config --global", text, p.name)
            self.assertNotIn("rebase --skip", text, f"{p.name}: a text-based skip can drop a real commit")

    def test_the_resolve_skill_branches_on_the_exit_code_and_shows_stderr(self):
        text = (ROOT / "skills" / "mem-sync-resolve" / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("capture_output", text, "the skill must not swallow git's message")
        self.assertIn("stderr", text)
        for code in ("**0**", "**2**", "**1**"):
            self.assertIn(code, text)
        self.assertNotIn("package_conflict()", text, "re-packaging a stop that left nothing unmerged loops")

    def test_the_bare_detector_is_not_vacuous(self):
        self.assertEqual(len(_bare_continues("   git rebase --continue   # may need --skip\n")), 1)
        self.assertEqual(_bare_continues("git -c user.email=gowth-mem@h rebase --continue"), [])


class ConflictReportTextTest(_Conflict):
    def test_the_gave_up_branch_names_the_continue_command(self):
        """The bounded-replay branch writes its own finish instruction."""
        git(self.b, "pull", "--rebase", "origin", "main", check=False)
        with mock.patch.dict(os.environ, {"GOWTH_MEM_HOME": str(self.b)}):
            import _conflict  # type: ignore
            with mock.patch.object(_conflict, "MAX_REPLAYS", 0), \
                    mock.patch.object(_conflict, "_resolve_stop", lambda gh, files, sections: []):
                out = _conflict.package_conflict(host="box-b")
        text = Path(out).read_text()
        self.assertIn("gave up after 0 replayed commits", text)
        self.assertIn(_conflict.continue_command(), text)
        self.assertEqual(_bare_continues(text), [], text)


if __name__ == "__main__":
    unittest.main()
