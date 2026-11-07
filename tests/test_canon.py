"""v4.9 §3.5: `_canon.py` — the shipped rules + research canon reach every vault,
and never race a pull.

  * install → current (3 ensures byte-identical) → updated only when the vault
    copy matches a PREVIOUSLY shipped version; a user edit is never overwritten;
    nothing is deleted; a template not shipped in this build is skipped;
  * `.gitattributes` gains `workspaces/*/docs/questions.md merge=union` once;
  * auto-sync delivers it only AFTER a successful pull (under the sync lock): a
    pull that brings a peer's older canon lands first and is then updated in
    place — a tracked modification, no add/add — uncommitted edits survive and
    no stash is left behind; the no-remote branch installs it too; what was
    delivered is committed at once, never left untracked for the next pull's
    stash (review safety-sync#1 — tests/test_canon_commit_fix.py);
  * bootstrap-load delivers it only for a vault with no .git or no remote;
  * templates/canon-hashes.json holds the current template's sha1 (fails the
    build when a template changes without `--rebuild-hashes`).

The sync cases run the REAL auto-sync.py from a copied plugin tree whose
templates are fixtures (spec: WP-A3 tests use fixture files).
"""
from __future__ import annotations

import hashlib
import json
import os
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

import _canon  # noqa: E402

SCRUB = ("AI_AGENT", "GOWTH_WORKSPACE", "CLAUDE_SUBAGENT", "GOWTH_MEM_OFF", "CLAUDE_CODE_ENTRYPOINT")
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@x", "GIT_CONFIG_NOSYSTEM": "1"}
V1 = "# Memory rules\n\nRule one: look it up before you guess.\n"
V2 = V1 + "Rule two: never edit an old entry; append a correction that names it.\n"
DQ = "# Data quality (fixture)\n\nKeep what is durable, sourced and reusable.\n"


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "init.defaultBranch=main", "-c", "core.hooksPath=/dev/null", *args],
                          cwd=str(cwd), capture_output=True, text=True,
                          env={**os.environ, **GIT_ENV}, check=check)


def make_plugin(dst: Path, rules: "str | None" = V2, hashes: "dict | None" = None, dq: "str | None" = DQ) -> Path:
    """A plugin tree: the real hooks/scripts + fixture canon templates."""
    scripts = dst / "hooks" / "scripts"
    scripts.mkdir(parents=True)
    for f in SCRIPTS.iterdir():
        if f.suffix in (".py", ".sh"):
            shutil.copy2(f, scripts / f.name)
    tpl = dst / "templates" / "dot-gowth-mem" / "shared"
    (tpl / "research").mkdir(parents=True)
    if rules is not None:
        (tpl / "memory-rules.md").write_text(rules)
    if dq is not None:
        (tpl / "research" / "data-quality-2026.md").write_text(dq)
    if hashes is None:
        hashes = {"shared/memory-rules.md": [sha1(V1), sha1(V2)],
                  "shared/research/data-quality-2026.md": [sha1(DQ)]}
    (dst / "templates" / "canon-hashes.json").write_text(json.dumps(hashes))
    return dst


class _Case(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gowth_canon_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.vault = self.tmp / "vault"
        self.vault.mkdir()
        (self.vault / "settings.json").write_text(json.dumps({"layout_version": 3}))
        patcher = mock.patch.dict(os.environ, {"GOWTH_MEM_HOME": str(self.vault)})
        patcher.start()
        self.addCleanup(patcher.stop)
        for k in SCRUB:
            os.environ.pop(k, None)

    def rules(self) -> Path:
        return self.vault / "shared" / "memory-rules.md"


# ── shipped hashes ─────────────────────────────────────────────────────────

class HashesTest(unittest.TestCase):
    def test_every_shipped_template_is_in_the_hashes(self):
        hashes = _canon.load_hashes(ROOT)
        shipped = 0
        for src, dst in _canon.CANON:
            p = ROOT / src
            if not p.is_file():
                continue
            shipped += 1
            self.assertIn(hashlib.sha1(p.read_bytes()).hexdigest(), hashes.get(dst, set()),
                          f"{src} changed without `_canon.py --rebuild-hashes`")
        self.assertGreaterEqual(shipped, 3, "the three research canons ship")

    def test_hash_keys_are_canon_destinations(self):
        data = json.loads((ROOT / _canon.HASHES_REL).read_text())
        self.assertTrue(data)
        self.assertLessEqual(set(data), {dst for _src, dst in _canon.CANON})
        for dst, hs in data.items():
            self.assertTrue(hs and all(len(h) == 40 for h in hs), dst)

    def test_rebuild_covers_the_committed_hashes(self):
        try:
            shallow = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--is-shallow-repository"],
                                     capture_output=True, text=True, timeout=30)
        except OSError:
            self.skipTest("git not available")
        if shallow.returncode != 0 or shallow.stdout.strip() != "false":
            self.skipTest("needs the full git history (shallow or no checkout)")
        rebuilt = _canon.rebuild_hashes(ROOT, write=False)
        committed = json.loads((ROOT / _canon.HASHES_REL).read_text())
        for dst, hs in committed.items():
            self.assertLessEqual(set(hs), set(rebuilt.get(dst, [])), dst)
        for dst in rebuilt:
            self.assertIn(dst, committed, f"{dst} has history but no committed hashes")
        # older versions really are in there (git log --follow found the history)
        self.assertGreaterEqual(len(rebuilt["shared/research/data-quality-2026.md"]), 2)


# ── ensure() on fixtures ──────────────────────────────────────────────────

class EnsureTest(_Case):
    def setUp(self):
        super().setUp()
        self.plugin = make_plugin(self.tmp / "plugin")

    def ensure(self) -> dict:
        return dict(_canon.ensure(self.vault, root=self.plugin))

    def test_install_then_current_three_passes(self):
        got = self.ensure()
        self.assertEqual(got, {"shared/memory-rules.md": "installed",
                               "shared/research/data-quality-2026.md": "installed",
                               ".gitattributes": "installed"})
        self.assertEqual(self.rules().read_text(), V2)
        ga = (self.vault / ".gitattributes").read_text()
        self.assertIn(_canon.UNION_LINE + "\n", ga)
        files = [self.rules(), self.vault / "shared" / "research" / "data-quality-2026.md",
                 self.vault / ".gitattributes"]
        first = [(p.read_bytes(), os.stat(p).st_mtime_ns) for p in files]
        for _ in range(2):
            self.assertEqual(set(self.ensure().values()), {"current"})
            self.assertEqual([(p.read_bytes(), os.stat(p).st_mtime_ns) for p in files], first)

    def test_a_previously_shipped_version_is_updated(self):
        self.rules().parent.mkdir(parents=True)
        self.rules().write_text(V1)
        self.assertEqual(self.ensure()["shared/memory-rules.md"], "updated")
        self.assertEqual(self.rules().read_text(), V2)

    def test_a_user_edit_is_never_overwritten(self):
        self.rules().parent.mkdir(parents=True)
        edited = V1 + "My own rule: tea before deploys.\n"
        self.rules().write_text(edited)
        for _ in range(3):
            self.assertEqual(self.ensure()["shared/memory-rules.md"], "user-modified")
            self.assertEqual(self.rules().read_text(), edited)

    def test_a_newer_peer_version_is_left_alone_by_an_older_plugin(self):
        """No ping-pong: a plugin that never shipped V2 leaves V2 in place."""
        old = make_plugin(self.tmp / "old", rules=V1, hashes={"shared/memory-rules.md": [sha1(V1)]})
        self.rules().parent.mkdir(parents=True)
        self.rules().write_text(V2)
        self.assertEqual(dict(_canon.ensure(self.vault, root=old))["shared/memory-rules.md"], "user-modified")
        self.assertEqual(self.rules().read_text(), V2)

    def test_an_unshipped_template_is_skipped_and_nothing_is_deleted(self):
        bare = make_plugin(self.tmp / "bare", rules=None)
        (self.vault / "shared" / "research").mkdir(parents=True)
        extra = self.vault / "shared" / "research" / "my-notes.md"
        extra.write_text("mine\n")
        self.rules().write_text("a rules file the user keeps\n")
        got = dict(_canon.ensure(self.vault, root=bare))
        self.assertNotIn("shared/memory-rules.md", got)
        self.assertEqual(got["shared/research/data-quality-2026.md"], "installed")
        self.assertEqual(self.rules().read_text(), "a rules file the user keeps\n")
        self.assertEqual(extra.read_text(), "mine\n")

    def test_gitattributes_line_is_appended_once(self):
        ga = self.vault / ".gitattributes"
        ga.write_text("*.png binary")                     # no trailing newline
        self.assertEqual(self.ensure()[".gitattributes"], "updated")
        self.assertEqual(ga.read_text().count(_canon.UNION_LINE), 1)
        self.assertTrue(ga.read_text().startswith("*.png binary\n"))
        before = ga.read_bytes()
        self.assertEqual(self.ensure()[".gitattributes"], "current")
        self.assertEqual(ga.read_bytes(), before)
        ga.write_text("# mine\nworkspaces/*/docs/questions.md   merge=union  \n")
        self.assertEqual(self.ensure()[".gitattributes"], "current", "whitespace variants count")
        ga.write_text("workspaces/*/docs/questions.md -merge\n")     # a deliberate user override
        self.assertEqual(self.ensure()[".gitattributes"], "user-modified")
        self.assertEqual(ga.read_text(), "workspaces/*/docs/questions.md -merge\n")

    def test_symlinks_are_never_written_through(self):
        outside = self.tmp / "outside.md"
        outside.write_text(V1)                            # matches a shipped version
        self.rules().parent.mkdir(parents=True)
        self.rules().symlink_to(outside)
        elsewhere = self.tmp / "elsewhere"                # a shared/research/ pointing out of the vault
        elsewhere.mkdir()
        (self.vault / "shared" / "research").symlink_to(elsewhere)
        got = self.ensure()
        self.assertEqual(got["shared/memory-rules.md"], "user-modified")
        self.assertEqual(outside.read_text(), V1)
        self.assertEqual(got["shared/research/data-quality-2026.md"], "user-modified")
        self.assertEqual(list(elsewhere.iterdir()), [])
        self.assertEqual(got[".gitattributes"], "installed", "positive control: the rest is delivered")

    def test_an_uninstalled_vault_gets_nothing(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        self.assertEqual(_canon.ensure(empty, root=self.plugin), [])
        self.assertEqual(list(empty.iterdir()), [])
        self.assertEqual(_canon.ensure(self.tmp / "missing", root=self.plugin), [])
        self.assertFalse((self.tmp / "missing").exists())

    def test_real_templates_install_byte_identical(self):
        got = dict(_canon.ensure(self.vault))
        for src, dst in _canon.CANON:
            if (ROOT / src).is_file():
                self.assertEqual(got[dst], "installed")
                self.assertEqual((self.vault / dst).read_bytes(), (ROOT / src).read_bytes())
            else:
                self.assertNotIn(dst, got)

    def test_cli_prints_one_status_per_file(self):
        env = {**os.environ}
        r = subprocess.run([sys.executable, str(self.plugin / "hooks" / "scripts" / "_canon.py")],
                           capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("shared/memory-rules.md\tinstalled", r.stdout)
        self.assertIn(".gitattributes\tinstalled", r.stdout)
        r2 = subprocess.run([sys.executable, str(self.plugin / "hooks" / "scripts" / "_canon.py")],
                            capture_output=True, text=True, env=env, timeout=60)
        self.assertIn("shared/memory-rules.md\tcurrent", r2.stdout)

    def test_cli_never_writes_beside_a_running_sync(self):
        """/mem-install's call waits for the sync lock (a hook-spawned pull may
        be running) and gives up without writing when it stays held."""
        import contextlib
        import io
        from _lock import file_lock  # type: ignore
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(_canon, "CLI_SYNC_TIMEOUT", 0.3), \
                mock.patch.object(_canon, "plugin_root", lambda: self.plugin), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with file_lock("sync", timeout=5):
                self.assertEqual(_canon._cli([]), 1)
            self.assertFalse(self.rules().exists())
            self.assertFalse((self.vault / ".gitattributes").exists())
            self.assertIn("sync is in progress", err.getvalue())
            self.assertEqual(_canon._cli([]), 0, "positive control: free lock → delivered")
        self.assertEqual(self.rules().read_text(), V2)
        self.assertIn("shared/memory-rules.md\tinstalled", out.getvalue())


# ── delivery through the real sync / bootstrap entry points ───────────────

class SyncDeliveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gowth_canon_sync_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.plugin = make_plugin(self.tmp / "plugin")
        self.bare = self.tmp / "bare.git"
        git(self.tmp, "init", "-q", "--bare", "-b", "main", str(self.bare))
        self.a, self.b = self.tmp / "A", self.tmp / "B"
        git(self.tmp, "clone", "-q", str(self.bare), str(self.a))
        ws = self.a / "workspaces" / "demo"
        (ws / "docs").mkdir(parents=True)
        (ws / "t").mkdir()
        (ws / "workspace.json").write_text('{"name": "demo"}')
        (ws / "docs" / "handoff.md").write_text("## 2026-11-07 s\n- host:a 2026-11-07 [next] start\n")
        (ws / "t" / "00-README.md").write_text("---\nslug: t\ntitle: T\n---\n# T\n")
        (ws / "t" / "2026-11-07-a.md").write_text("---\nslug: t-a\n---\n- [exp] numbat colonies nest under logs\n")
        (self.a / "shared").mkdir()
        (self.a / "settings.json").write_text('{"layout_version": 3}')
        (self.a / ".gitignore").write_text("state.json\nindex.db\n.locks/\nconfig.json\nlogs/\n")
        git(self.a, "add", "-A")
        git(self.a, "commit", "-q", "-m", "base")
        git(self.a, "push", "-q", "origin", "main")
        git(self.tmp, "clone", "-q", str(self.bare), str(self.b))
        # A runs an OLDER plugin: it installed and pushed memory-rules V1 after B cloned
        (self.a / "shared" / "memory-rules.md").write_text(V1)
        git(self.a, "add", "-A")
        git(self.a, "commit", "-q", "-m", "A: canon v1")
        git(self.a, "push", "-q", "origin", "main")
        (self.b / "config.json").write_text(json.dumps({"remote": str(self.bare), "branch": "main",
                                                        "host_id": "hostb"}))
        self.aspect_b = self.b / "workspaces" / "demo" / "t" / "2026-11-07-a.md"
        self.aspect_b.write_text(self.aspect_b.read_text() + "- [exp] uncommitted edit on B must survive\n")
        self.env = {k: v for k, v in os.environ.items() if k not in SCRUB}
        self.env.update(GIT_ENV)
        self.env["GOWTH_MEM_HOME"] = str(self.b)

    def sync(self, *args: str, home: "Path | None" = None) -> subprocess.CompletedProcess:
        env = dict(self.env)
        if home is not None:
            env["GOWTH_MEM_HOME"] = str(home)
        r = subprocess.run([sys.executable, str(self.plugin / "hooks" / "scripts" / "auto-sync.py"), *args],
                           capture_output=True, text=True, env=env, timeout=180)
        self.assertNotIn("Traceback", r.stderr)
        return r

    def porcelain(self) -> list:
        return git(self.b, "status", "--porcelain").stdout.splitlines()

    def test_pull_brings_the_older_canon_then_it_is_updated_in_place(self):
        r = self.sync("--pull-only")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("canon shared/memory-rules.md updated", r.stdout)
        rules = self.b / "shared" / "memory-rules.md"
        self.assertEqual(rules.read_text(), V2)
        # the update is committed right away (review safety-sync#1) — as a
        # modification of the peer's tracked copy, not an add/add
        self.assertIn("committed the delivered canon (3 file(s))", r.stdout)
        changed = git(self.b, "show", "--name-status", "--format=", "HEAD").stdout.splitlines()
        self.assertIn("M\tshared/memory-rules.md", changed, "a tracked modification, not an add/add")
        self.assertIn("A\tshared/research/data-quality-2026.md", changed)
        self.assertIn("A\t.gitattributes", changed)
        self.assertEqual(git(self.b, "show", "HEAD~1:shared/memory-rules.md").stdout, V1, "the peer's copy came first")
        st = self.porcelain()
        self.assertEqual(st, [" M workspaces/demo/t/2026-11-07-a.md"], "no canon path left uncommitted")
        self.assertFalse(any(ln[:2] in ("AA", "UU", "DU", "UD") for ln in st), st)
        self.assertFalse((self.b / "SYNC-CONFLICT.md").exists())
        self.assertEqual(git(self.b, "stash", "list").stdout.strip(), "", "no stash left behind")
        self.assertIn("uncommitted edit on B must survive", self.aspect_b.read_text())
        self.assertEqual((self.b / "shared" / "research" / "data-quality-2026.md").read_text(), DQ)
        self.assertIn(_canon.UNION_LINE, (self.b / ".gitattributes").read_text())
        # a second sync changes nothing
        before = rules.read_bytes()
        r2 = self.sync("--pull-only")
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertNotIn("canon", r2.stdout)
        self.assertEqual(rules.read_bytes(), before)

    def test_delivering_before_the_pull_with_another_version_is_a_visible_conflict(self):
        """Positive control for the ordering above: the SAME scenario with the
        canon delivered BEFORE the pull (B's V2 meets the peer's tracked V1 —
        two plugin versions) is the add/add §3.5 avoids. The CLI commits what
        it delivers (review safety-sync#1), so it surfaces as a visible
        SYNC-CONFLICT.md (spec §11), no longer as the silent stranded stash an
        UNTRACKED copy caused ("already exists, no checkout"); B's uncommitted
        edit is in the stash the conflict message names. (Same version on both
        sides: tests/test_canon_commit_fix.py — the pull drops the duplicate.)"""
        r0 = subprocess.run([sys.executable, str(self.plugin / "hooks" / "scripts" / "_canon.py")],
                            capture_output=True, text=True, env=self.env, timeout=60)
        self.assertIn("shared/memory-rules.md\tinstalled", r0.stdout)
        self.assertIn("committed 3 delivered file(s)", r0.stderr)
        r = self.sync("--pull-only")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("already exists", r.stderr)
        self.assertIn("conflict — wrote", r.stderr)
        self.assertIn("shared/memory-rules.md", (self.b / "SYNC-CONFLICT.md").read_text())
        self.assertIn("dirty changes preserved in stash", r.stderr)
        self.assertIn("uncommitted edit on B must survive", git(self.b, "stash", "show", "-p").stdout)

    def test_full_sync_delivers_after_the_pull_and_still_pushes(self):
        handoff = self.b / "workspaces" / "demo" / "docs" / "handoff.md"
        handoff.write_text(handoff.read_text() + "- host:b 2026-11-07 [doing] local commit on B\n")
        git(self.b, "commit", "-q", "-am", "B: local work")
        r = self.sync("--pull-rebase-push")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.b / "shared" / "memory-rules.md").read_text(), V2)
        log = git(self.bare, "log", "--format=%s", "main").stdout
        self.assertIn("A: canon v1", log)
        self.assertIn("B: local work", log, "B's commit was replayed and pushed")
        self.assertEqual(git(self.b, "rev-list", "--count", "origin/main..HEAD").stdout.strip(), "0", "pushed")
        # updated after the pull and committed before the push: it went out with it
        self.assertIn("update(shared): deliver gowth-mem canon (3 files)", log)
        self.assertEqual(git(self.bare, "show", "main:shared/memory-rules.md").stdout, V2)
        self.assertFalse(any("shared/" in ln or ".gitattributes" in ln for ln in self.porcelain()),
                         self.porcelain())
        self.assertEqual(git(self.b, "stash", "list").stdout.strip(), "")
        self.assertFalse((self.b / ".git" / "rebase-merge").exists())

    def test_no_remote_vault_gets_the_canon_from_auto_sync(self):
        c = self.tmp / "C"
        (c / "shared").mkdir(parents=True)
        (c / "settings.json").write_text('{"layout_version": 3}')
        r = self.sync("--pull-only", home=c)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((c / "shared" / "memory-rules.md").read_text(), V2)
        self.assertIn(_canon.UNION_LINE, (c / ".gitattributes").read_text())

    def test_failed_pull_delivers_nothing(self):
        cfg = json.loads((self.b / "config.json").read_text())
        cfg["remote"] = str(self.tmp / "no-such-remote.git")
        (self.b / "config.json").write_text(json.dumps(cfg))
        git(self.b, "remote", "set-url", "origin", cfg["remote"])
        r = self.sync("--pull-only")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.b / "shared" / "memory-rules.md").exists(), "canon must wait for a good pull")
        self.assertIn("uncommitted edit on B must survive", self.aspect_b.read_text())


class BootstrapDeliveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gowth_canon_boot_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.vault = self.tmp / "vault"
        ws = self.vault / "workspaces" / "demo"
        (ws / "docs").mkdir(parents=True)
        (ws / "workspace.json").write_text('{"name": "demo"}')
        (self.vault / "shared").mkdir()
        (self.vault / "settings.json").write_text('{"layout_version": 3}')
        (self.tmp / "claude").mkdir()
        self.env = {k: v for k, v in os.environ.items() if k not in SCRUB}
        self.env.update({"GOWTH_MEM_HOME": str(self.vault), "CLAUDE_CONFIG_DIR": str(self.tmp / "claude"),
                         "GOWTH_MEM_NO_AUTOHEAL": "1", "GOWTH_WORKSPACE": "demo"})

    def boot(self) -> None:
        r = subprocess.run([sys.executable, str(SCRIPTS / "bootstrap-load.py")],
                           input=json.dumps({"source": "startup", "cwd": str(self.tmp), "session_id": "b1"}),
                           capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def shipped(self) -> list:
        return [dst for src, dst in _canon.CANON if (ROOT / src).is_file()]

    def test_vault_without_git_gets_the_canon_at_session_start(self):
        self.boot()
        for dst in self.shipped():
            self.assertTrue((self.vault / dst).is_file(), dst)
        self.assertIn(_canon.UNION_LINE, (self.vault / ".gitattributes").read_text())

    def test_git_but_no_remote_gets_the_canon(self):
        git(self.vault, "init", "-q")
        self.boot()
        self.assertTrue(all((self.vault / dst).is_file() for dst in self.shipped()))

    def test_synced_vault_waits_for_auto_sync(self):
        git(self.vault, "init", "-q")
        (self.vault / "config.json").write_text(json.dumps({"remote": str(self.tmp / "r.git")}))
        self.boot()
        for dst in self.shipped():
            self.assertFalse((self.vault / dst).exists(), f"{dst}: delivered beside a possible pull")
        self.assertFalse((self.vault / ".gitattributes").exists())


if __name__ == "__main__":
    unittest.main()
