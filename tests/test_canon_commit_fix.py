"""Review safety-sync#1 / gaps-docs#1 / gaps-docs#2 (v4.9 wave A): delivered canon
is COMMITTED, never left untracked across syncs.

The defect: `ensure_canon` wrote shared/memory-rules.md, the research canon and
`.gitattributes` as UNTRACKED files. Two machines on the SAME build both
delivered; A pushed; B's next SessionStart `--pull-only` ran `stash push -u`,
which took B's untracked copies together with B's uncommitted edits; the pull
brought A's tracked copies and `stash pop` failed with "already exists, no
checkout" (even for byte-identical content): rc 2, a stranded pre-pull stash,
and on git 2.33 (first on PATH on the author's Mac) B's edits to TRACKED files
were left only in that stash — the next full sync pushed the vault without
them. `/mem-install` re-runs reached the same state by running `_canon.py`
with no pull first.

Now:
  * `_canon.commit_delivered()` commits exactly what `ensure()` installed or
    updated (`git add -- <paths>` + `git commit --only -- <paths>`, gowth-mem
    identity) — from auto-sync's `ensure_canon` (every call site, under the
    sync lock) and from the `_canon.py` CLI (inside its sync lock);
  * auto-sync's pull commits any canon path still untracked right before its
    `stash push -u` (`_canon.commit_untracked`, defence in depth);
  * a peer's identical delivery then rebases to an empty commit, which git
    drops — rc 0, `git stash list` empty, every edit kept;
  * `_restore_stash` reports an "already exists, no checkout" pop truthfully.

Every sync runs the REAL auto-sync.py / _canon.py of a copied plugin tree
(fixture canon templates) under an env with NO git identity anywhere
(`user.useConfigOnly=true`, empty HOME), so each commit these paths make must
carry the gowth-mem identity or fail. Assertions are on rc and the stash list,
not only on content: on git >= 2.34 the edits survive the failed pop anyway.
"""
from __future__ import annotations

import hashlib
import importlib.util
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
from _sync import _DEFAULT_GITIGNORE  # noqa: E402

SCRUB = ("AI_AGENT", "GOWTH_WORKSPACE", "CLAUDE_SUBAGENT", "GOWTH_MEM_OFF", "CLAUDE_CODE_ENTRYPOINT",
         "CLAUDE_PLUGIN_ROOT", "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
         "GIT_COMMITTER_EMAIL", "EMAIL", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_PARAMETERS",
         "GIT_DIR", "GIT_WORK_TREE", "GOWTH_MEM_GIT_TOKEN")
SETUP_ID = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@x", "GIT_CONFIG_NOSYSTEM": "1"}
V1 = "# Memory rules\n\nRule one: look it up before you guess.\n"
V2 = V1 + "Rule two: never edit an old entry; append a correction that names it.\n"
DQ = "# Data quality (fixture)\n\nKeep what is durable, sourced and reusable.\n"
RULES = "shared/memory-rules.md"
DQ_REL = "shared/research/data-quality-2026.md"
GA = ".gitattributes"
CANON3 = [RULES, DQ_REL, GA]           # what the fixture plugin delivers into a fresh vault
GA_BODY = f"{_canon._UNION_COMMENT}\n{_canon.UNION_LINE}\n"
EDIT = "- host:b 2026-11-07 [doing] UNCOMMITTED WORK ON B must survive\n"
SESSION = "workspaces/demo/journal/sessions/2026-11-07-abcd1234.md"


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def git(cwd: Path, *args: str, env: "dict | None" = None, check: bool = True) -> subprocess.CompletedProcess:
    base = {k: v for k, v in os.environ.items() if k not in SCRUB}
    return subprocess.run(["git", "-c", "init.defaultBranch=main", "-c", "core.hooksPath=/dev/null", *args],
                          cwd=str(cwd), capture_output=True, text=True,
                          env=env if env is not None else {**base, **SETUP_ID}, check=check)


def no_identity_env(tmp: Path, **extra: str) -> dict:
    """tests/test_git_identity.py's pattern: no identity anywhere, and git
    refuses to guess one (user.useConfigOnly)."""
    home = tmp / "empty-home"
    home.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in SCRUB and not k.startswith("GIT_CONFIG_")}
    env.update({"HOME": str(home), "XDG_CONFIG_HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "user.useConfigOnly",
                "GIT_CONFIG_VALUE_0": "true", "GOWTH_MEM_NO_AUTOHEAL": "1",
                "CLAUDE_CONFIG_DIR": str(tmp / "claude-config")})
    env.update(extra)
    return env


def make_plugin(dst: Path) -> Path:
    """A plugin tree: the real hooks/scripts + fixture canon templates (V2 rules,
    DQ research, both in the shipped hashes)."""
    scripts = dst / "hooks" / "scripts"
    scripts.mkdir(parents=True)
    for f in SCRIPTS.iterdir():
        if f.suffix in (".py", ".sh"):
            shutil.copy2(f, scripts / f.name)
    tpl = dst / "templates" / "dot-gowth-mem" / "shared"
    (tpl / "research").mkdir(parents=True)
    (tpl / "memory-rules.md").write_text(V2)
    (tpl / "research" / "data-quality-2026.md").write_text(DQ)
    (dst / "templates" / "canon-hashes.json").write_text(json.dumps(
        {RULES: [sha1(V1), sha1(V2)], DQ_REL: [sha1(DQ)]}))
    return dst


def load_auto_sync():
    spec = importlib.util.spec_from_file_location("gowth_autosync_canon_fix", SCRIPTS / "auto-sync.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def pin_scratch_home(case: unittest.TestCase, tmp: Path) -> None:
    """In-process calls (log_debug → <home>/logs/hook-errors.log, locks) must
    never reach the real ~/.gowth-mem, whatever env the suite runs under."""
    patcher = mock.patch.dict(os.environ, {"GOWTH_MEM_HOME": str(tmp / "scratch-home")})
    patcher.start()
    case.addCleanup(patcher.stop)


class _TwoMachines(unittest.TestCase):
    """A bare remote and two clones of a pre-v4.9 vault (no canon, no
    .gitattributes anywhere), both machines on the SAME plugin build."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gowth_canon_commit_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        pin_scratch_home(self, self.tmp)
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
        (self.a / "shared" / "AGENTS.md").write_text("# Operating rules\n")
        (self.a / "settings.json").write_text('{"layout_version": 3}')
        (self.a / ".gitignore").write_text(_DEFAULT_GITIGNORE)
        git(self.a, "add", "-A")
        git(self.a, "commit", "-q", "-m", "base")
        git(self.a, "push", "-q", "origin", "main")
        git(self.tmp, "clone", "-q", str(self.bare), str(self.b))
        for home, host in ((self.a, "hosta"), (self.b, "hostb")):
            (home / "config.json").write_text(json.dumps({"remote": str(self.bare), "branch": "main",
                                                          "host_id": host}))
        self.handoff_b = self.b / "workspaces" / "demo" / "docs" / "handoff.md"

    # ── entry points ────────────────────────────────────────────────────────
    def env(self, home: Path) -> dict:
        return no_identity_env(self.tmp, GOWTH_MEM_HOME=str(home))

    def sync(self, home: Path, *args: str) -> subprocess.CompletedProcess:
        r = subprocess.run([sys.executable, str(self.plugin / "hooks" / "scripts" / "auto-sync.py"), *args],
                           capture_output=True, text=True, env=self.env(home), timeout=180)
        self.assertNotIn("Traceback", r.stderr)
        return r

    def canon_cli(self, home: Path) -> subprocess.CompletedProcess:
        r = subprocess.run([sys.executable, str(self.plugin / "hooks" / "scripts" / "_canon.py")],
                           capture_output=True, text=True, env=self.env(home), timeout=120)
        self.assertNotIn("Traceback", r.stderr)
        return r

    def a_delivers_and_pushes(self) -> None:
        r = self.sync(self.a, "--pull-only")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = self.sync(self.a, "--pull-rebase-push")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("pushed origin/main", r.stdout)
        self.assertEqual(sorted(set(CANON3) & set(self.remote_files())), sorted(CANON3),
                         "A pushed its canon")

    def b_keeps_working(self) -> Path:
        """An uncommitted edit to a TRACKED file plus a brand-new file."""
        self.handoff_b.write_text(self.handoff_b.read_text() + EDIT)
        session = self.b / SESSION
        session.parent.mkdir(parents=True)
        session.write_text("**User:** go\n**Claude:** done\n")
        return session

    # ── observations ────────────────────────────────────────────────────────
    def stash_list(self, home: Path) -> str:
        return git(home, "stash", "list").stdout.strip()

    def ahead(self, home: Path) -> str:
        return git(home, "rev-list", "--count", "origin/main..HEAD").stdout.strip()

    def untracked_canon(self, home: Path) -> list:
        out = git(home, "ls-files", "--others", "--exclude-standard", "--", *_canon.canon_destinations()).stdout
        return out.split()

    def head_files(self, home: Path) -> list:
        return sorted(git(home, "show", "--name-only", "--format=", "HEAD").stdout.split())

    def remote_files(self) -> list:
        return git(self.bare, "ls-tree", "-r", "--name-only", "main").stdout.split()

    def head_meta(self, home: Path) -> str:
        return git(home, "log", "-1", "--format=%s|%an <%ae>|%cn <%ce>").stdout.strip()

    def assert_canon_content(self, home: Path) -> None:
        self.assertEqual((home / RULES).read_text(), V2)
        self.assertEqual((home / DQ_REL).read_text(), DQ)
        self.assertEqual((home / GA).read_text(), GA_BODY)

    def assert_clean_pull(self, r: subprocess.CompletedProcess, home: Path) -> None:
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.stash_list(home), "", "a pre-pull stash was stranded")
        self.assertFalse((home / "SYNC-CONFLICT.md").exists())
        self.assertFalse((home / ".git" / "rebase-merge").exists())
        self.assertFalse((home / ".git" / "rebase-apply").exists())
        self.assertNotIn("already exists", r.stderr)


class TwoMachineDeliveryTest(_TwoMachines):
    def test_the_env_has_no_identity(self):
        """Positive control: in this env git itself refuses to commit, so every
        commit below proves the gowth-mem identity was passed."""
        probe = git(self.b, "commit", "--allow-empty", "-q", "-m", "probe", env=self.env(self.b), check=False)
        self.assertNotEqual(probe.returncode, 0, "an identity leaked into the env — the tests would be vacuous")

    def test_both_deliver_a_pushes_b_pull_only_keeps_every_edit(self):
        # 1. B's SessionStart pull delivers — and commits — the canon
        r = self.sync(self.b, "--pull-only")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("sync: canon shared/memory-rules.md installed", r.stdout)
        self.assertIn("sync: committed the delivered canon (3 file(s))", r.stdout)
        self.assertEqual(self.untracked_canon(self.b), [], "delivered canon must never stay untracked")
        self.assertEqual(git(self.b, "status", "--porcelain").stdout.strip(), "")
        self.assertEqual(self.head_files(self.b), sorted(CANON3), "the commit holds exactly the canon")
        self.assertEqual(self.head_meta(self.b),
                         "update(shared): deliver gowth-mem canon (3 files)"
                         "|gowth-mem <gowth-mem@hostb>|gowth-mem <gowth-mem@hostb>")
        self.assertEqual(self.ahead(self.b), "1", "committed locally; the next full sync pushes it")
        self.assert_canon_content(self.b)
        # 2. A, same build, delivers too and its full sync pushes the identical canon
        self.a_delivers_and_pushes()
        # 3. B keeps working without committing (the Stop autosync is debounced)
        session = self.b_keeps_working()
        # 4. B's next SessionStart pull: no stash may be stranded, nothing may be lost
        r = self.sync(self.b, "--pull-only")
        self.assert_clean_pull(r, self.b)
        self.assertIn("sync: restored stashed changes", r.stdout)
        self.assertIn(EDIT, self.handoff_b.read_text(), "the tracked edit left the working tree")
        self.assertTrue(session.is_file())
        self.assertEqual(self.ahead(self.b), "0", "B's identical canon commit was dropped by the rebase")
        self.assert_canon_content(self.b)
        st = git(self.b, "status", "--porcelain").stdout
        self.assertIn(" M workspaces/demo/docs/handoff.md", st)
        self.assertNotIn("shared/", st)
        self.assertNotIn(GA, st)
        # 5. B's next full sync publishes the edit and the new file
        r = self.sync(self.b, "--pull-rebase-push")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.stash_list(self.b), "")
        self.assertIn(EDIT, git(self.bare, "show", "main:workspaces/demo/docs/handoff.md").stdout)
        self.assertIn(SESSION, self.remote_files())

    def test_no_edit_variant(self):
        """Without any edit B's tree was 'dirty' only with its untracked canon:
        that alone stranded a stash holding nothing but the canon (rc 2)."""
        self.assertEqual(self.sync(self.b, "--pull-only").returncode, 0)
        self.assertEqual(self.untracked_canon(self.b), [])
        self.a_delivers_and_pushes()
        r = self.sync(self.b, "--pull-only")
        self.assert_clean_pull(r, self.b)
        self.assertIn("sync: pulled origin/main", r.stdout)
        self.assertEqual(self.ahead(self.b), "0")
        self.assertEqual(git(self.b, "status", "--porcelain").stdout.strip(), "")
        self.assert_canon_content(self.b)

    def test_repeated_pulls_change_nothing_after_the_first_delivery(self):
        """3 passes: one canon commit, then byte-identical files and the same HEAD."""
        self.assertEqual(self.sync(self.b, "--pull-only").returncode, 0)
        head = git(self.b, "rev-parse", "HEAD").stdout.strip()
        snap = {rel: (self.b / rel).read_bytes() for rel in CANON3}
        for _ in range(3):
            r = self.sync(self.b, "--pull-only")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("committed", r.stdout)
            self.assertEqual(git(self.b, "rev-parse", "HEAD").stdout.strip(), head)
            self.assertEqual({rel: (self.b / rel).read_bytes() for rel in CANON3}, snap)
        self.assertEqual(int(git(self.b, "rev-list", "--count", "HEAD").stdout), 2, "base + one canon commit")

    def test_full_sync_pushes_the_canon_commit_with_it(self):
        r = self.sync(self.b, "--pull-rebase-push")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("sync: committed the delivered canon (3 file(s))", r.stdout)
        self.assertEqual(self.ahead(self.b), "0", "the canon commit went out with the push")
        self.assertEqual(git(self.bare, "show", f"main:{RULES}").stdout, V2)
        self.assertEqual(git(self.b, "status", "--porcelain").stdout.strip(), "")

    def test_no_remote_git_vault_commits_its_delivery_under_the_sync_lock(self):
        cfg = json.loads((self.b / "config.json").read_text())
        cfg.pop("remote")
        (self.b / "config.json").write_text(json.dumps(cfg))
        # the sync lock is honoured: while another git op holds it, nothing is
        # delivered (or committed) beside it ...
        from _lock import file_lock  # type: ignore
        with mock.patch.dict(os.environ, {"GOWTH_MEM_HOME": str(self.b)}):
            with file_lock("sync", timeout=5):
                r = self.sync(self.b, "--pull-only")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((self.b / RULES).exists(), "delivered beside a git op that holds the sync lock")
        # ... and once it is free the delivery is committed
        r = self.sync(self.b, "--pull-only")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("no remote configured", r.stdout)
        self.assertIn("sync: committed the delivered canon (3 file(s))", r.stdout)
        self.assertEqual(self.untracked_canon(self.b), [])
        self.assertEqual(self.head_files(self.b), sorted(CANON3))
        self.assert_canon_content(self.b)


class CliDeliveryTest(_TwoMachines):
    def test_cli_before_the_pull_commits_and_the_pull_drops_the_duplicate(self):
        """gaps-docs#2's ordering, same build on both machines: the bare CLI runs
        before B pulled A's canon. Its delivery is committed, so the pull rebases
        it onto A's identical copy and drops it."""
        self.a_delivers_and_pushes()
        session = self.b_keeps_working()
        r = self.canon_cli(self.b)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"{RULES}\tinstalled", r.stdout)
        self.assertIn(f"{GA}\tinstalled", r.stdout)
        self.assertIn("committed 3 delivered file(s)", r.stderr)
        self.assertEqual(self.untracked_canon(self.b), [])
        self.assertEqual(self.head_files(self.b), sorted(CANON3), "only the canon — not B's edits")
        self.assertEqual(self.head_meta(self.b),
                         "update(shared): deliver gowth-mem canon (3 files)"
                         "|gowth-mem <gowth-mem@hostb>|gowth-mem <gowth-mem@hostb>")
        self.assertIn(" M workspaces/demo/docs/handoff.md", git(self.b, "status", "--porcelain").stdout)
        r = self.sync(self.b, "--pull-only")
        self.assert_clean_pull(r, self.b)
        self.assertIn(EDIT, self.handoff_b.read_text())
        self.assertTrue(session.is_file())
        self.assertEqual(self.ahead(self.b), "0")
        self.assert_canon_content(self.b)

    def test_install_step0_command_pulls_before_it_delivers(self):
        """/mem-install Step 0 on an installed vault now runs `auto-sync.py
        --pull-only`: A's canon arrives first, B's delivery finds it `current`
        and nothing is stashed against it."""
        self.a_delivers_and_pushes()
        session = self.b_keeps_working()
        r = self.sync(self.b, "--pull-only")
        self.assert_clean_pull(r, self.b)
        self.assertNotIn("sync: canon", r.stdout, "A's copies arrived first: nothing to install")
        self.assertIn(EDIT, self.handoff_b.read_text())
        self.assertTrue(session.is_file())
        self.assertEqual(self.ahead(self.b), "0")
        self.assert_canon_content(self.b)
        doc = (ROOT / "commands" / "mem-install.md").read_text()
        step0 = doc.split("## Step 0", 1)[1].split("## Step 1", 1)[0]
        branch = next(b for b in step0.split("\n- ") if b.startswith("`status=installed layout_version=3`"))
        self.assertIn('python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/auto-sync.py" --pull-only', branch)
        self.assertNotIn("hooks/scripts/_canon.py", step0, "Step 0 must not run the bare CLI before a pull")
        step4b = doc.split("## Step 4b", 1)[1].split("## Step 5", 1)[0]
        self.assertIn('python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_canon.py"', step4b)
        skill = (ROOT / "skills" / "mem-install" / "SKILL.md").read_text()
        line = next(ln for ln in skill.splitlines() if ln.startswith("- `layout_version = 3`"))
        self.assertIn('python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/auto-sync.py" --pull-only', line)
        self.assertNotIn("hooks/scripts/_canon.py", line)

    def test_a_user_modified_canon_is_never_committed_by_the_helper(self):
        mine1 = "# My own rules\n\nI wrote these.\n"
        mine2 = mine1 + "And edited them since.\n"
        (self.b / "shared").mkdir(exist_ok=True)
        (self.b / RULES).write_text(mine1)
        git(self.b, "add", "--", RULES)
        git(self.b, "commit", "-q", "-m", "my rules")
        (self.b / RULES).write_text(mine2)                   # uncommitted user edit
        r = self.canon_cli(self.b)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"{RULES}\tuser-modified", r.stdout)
        self.assertIn(f"{DQ_REL}\tinstalled", r.stdout)
        self.assertIn("committed 2 delivered file(s)", r.stderr, "positive control: the rest was committed")
        self.assertEqual(self.head_files(self.b), sorted([DQ_REL, GA]))
        self.assertEqual(git(self.b, "show", f"HEAD:{RULES}").stdout, mine1)
        self.assertEqual((self.b / RULES).read_text(), mine2, "the user's edit is untouched")
        self.assertIn(f" M {RULES}", git(self.b, "status", "--porcelain").stdout)
        # the helper itself: user-modified / current statuses are never committed
        head = git(self.b, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(_canon.commit_delivered(self.b, [(RULES, "user-modified"), (DQ_REL, "current"),
                                                          (GA, "current")], "hostb"), [])
        self.assertEqual(git(self.b, "rev-parse", "HEAD").stdout.strip(), head)

    def test_the_commit_carries_only_the_canon_paths(self):
        staged = self.b / "workspaces" / "demo" / "t" / "2026-11-07-staged.md"
        staged.write_text("---\nslug: t-staged\n---\n- [exp] staged by hand, not committed\n")
        git(self.b, "add", "--", str(staged.relative_to(self.b)))
        session = self.b_keeps_working()
        r = self.canon_cli(self.b)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.head_files(self.b), sorted(CANON3))
        self.assertEqual(git(self.b, "diff", "--cached", "--name-only").stdout.split(),
                         ["workspaces/demo/t/2026-11-07-staged.md"], "the staged file stays staged, uncommitted")
        st = git(self.b, "status", "--porcelain").stdout
        self.assertIn(" M workspaces/demo/docs/handoff.md", st)
        self.assertIn("?? workspaces/demo/journal/", st)
        self.assertTrue(session.is_file())
        # a non-canon path handed to the helper is dropped, never committed
        self.handoff_b.write_text(self.handoff_b.read_text() + "- one more line\n")
        (self.b / RULES).write_text(V2 + "local tweak\n")
        done = _canon.commit_paths(self.b, ["workspaces/demo/docs/handoff.md", RULES], "hostb")
        self.assertEqual(done, [RULES])
        self.assertEqual(self.head_files(self.b), [RULES])
        self.assertIn(" M workspaces/demo/docs/handoff.md", git(self.b, "status", "--porcelain").stdout)


class UntrackedGuardTest(_TwoMachines):
    def test_untracked_canon_from_an_earlier_build_is_committed_before_the_stash(self):
        """Defence in depth: copies an earlier v4.9 build (or bootstrap-load
        before a remote existed) left UNTRACKED are committed right before the
        pull's `stash push -u`, so they never ride the stash."""
        self.a_delivers_and_pushes()
        got = dict(_canon.ensure(self.b, root=self.plugin))     # delivers, does not commit
        self.assertEqual(got, {RULES: "installed", DQ_REL: "installed", GA: "installed"})
        self.assertEqual(sorted(self.untracked_canon(self.b)), sorted(CANON3), "precondition: untracked")
        session = self.b_keeps_working()
        r = self.sync(self.b, "--pull-only")
        self.assert_clean_pull(r, self.b)
        self.assertIn("sync: committed 3 untracked canon file(s) before the pull", r.stdout)
        self.assertIn(EDIT, self.handoff_b.read_text())
        self.assertTrue(session.is_file())
        self.assertEqual(self.ahead(self.b), "0", "the tracking commit matched A's and was dropped")
        self.assert_canon_content(self.b)

    def test_the_guard_replays_the_commit_boundary_refusals(self):
        """The guard is a new commit in front of the pull: it refuses what
        commit_local refuses at HEAD (unmerged paths, raw conflict markers) and
        never commits mid-rebase — replayed through the new entry point."""
        auto_sync = load_auto_sync()
        head = git(self.b, "rev-parse", "HEAD").stdout.strip()

        def guard() -> None:
            auto_sync._commit_untracked_canon(self.b, True, "hostb")

        (self.b / "shared").mkdir(exist_ok=True)
        (self.b / RULES).write_text("<<<<<<< Updated upstream\nA\n=======\nB\n>>>>>>> Stashed changes\n")
        guard()
        self.assertEqual(git(self.b, "rev-parse", "HEAD").stdout.strip(), head, "raw markers committed")
        self.assertIn(RULES, self.untracked_canon(self.b))
        (self.b / RULES).write_text(V2)
        (self.b / ".git" / "rebase-merge").mkdir()
        guard()
        self.assertEqual(git(self.b, "rev-parse", "HEAD").stdout.strip(), head, "committed mid-rebase")
        (self.b / ".git" / "rebase-merge").rmdir()
        # unmerged paths: a conflicted stash pop elsewhere in the vault
        self.handoff_b.write_text("stashed side\n")
        git(self.b, "stash", "push", "-q", "--", "workspaces/demo/docs/handoff.md")
        self.handoff_b.write_text("committed side\n")
        git(self.b, "commit", "-q", "-am", "B: other side")
        head = git(self.b, "rev-parse", "HEAD").stdout.strip()
        self.assertNotEqual(git(self.b, "stash", "pop", check=False).returncode, 0)
        self.assertTrue(git(self.b, "ls-files", "-u").stdout.strip(), "precondition: unmerged")
        guard()
        self.assertEqual(git(self.b, "rev-parse", "HEAD").stdout.strip(), head, "committed over unmerged paths")
        self.assertIn(RULES, self.untracked_canon(self.b))
        # positive control: once the tree is quiet the same guard commits it
        git(self.b, "checkout", "-q", "HEAD", "--", "workspaces/demo/docs/handoff.md")
        guard()
        self.assertEqual(self.head_files(self.b), [RULES])
        self.assertEqual(git(self.b, "log", "-1", "--format=%s").stdout.strip(),
                         "update(shared): track gowth-mem canon before the pull (1 file)")

    def test_the_guard_never_commits_other_untracked_files(self):
        session = self.b_keeps_working()
        auto_sync = load_auto_sync()
        head = git(self.b, "rev-parse", "HEAD").stdout.strip()
        auto_sync._commit_untracked_canon(self.b, True, "hostb")
        self.assertEqual(git(self.b, "rev-parse", "HEAD").stdout.strip(), head, "no canon untracked → no commit")
        self.assertIn("?? workspaces/demo/journal/", git(self.b, "status", "--porcelain").stdout)
        self.assertTrue(session.is_file())


class CommitRefusalTest(unittest.TestCase):
    """commit_paths() only commits from a quiet, committed tree."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gowth_canon_refuse_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        pin_scratch_home(self, self.tmp)
        self.v = self.tmp / "vault"
        (self.v / "shared").mkdir(parents=True)
        (self.v / "settings.json").write_text('{"layout_version": 3}')
        (self.v / "notes.md").write_text("base\n")

    def init(self) -> None:
        git(self.v, "init", "-q")
        git(self.v, "add", "-A")
        git(self.v, "commit", "-q", "-m", "base")

    def deliver(self) -> None:
        (self.v / RULES).write_text(V2)
        (self.v / GA).write_text(GA_BODY)

    def commits(self) -> int:
        r = git(self.v, "rev-list", "--count", "HEAD", check=False)
        return int(r.stdout.strip()) if r.returncode == 0 else 0

    def refused(self, why: str) -> None:
        before = self.commits()
        self.assertEqual(_canon.commit_paths(self.v, [RULES, GA], "hostr"), [], why)
        self.assertEqual(self.commits(), before, why)

    def test_positive_control(self):
        self.init()
        self.deliver()
        self.assertEqual(_canon.commit_paths(self.v, [RULES, GA], "hostr"), [RULES, GA])
        self.assertEqual(git(self.v, "log", "-1", "--format=%ae").stdout.strip(), "gowth-mem@hostr")

    def test_no_git_directory(self):
        self.deliver()
        self.refused("no .git")
        self.assertFalse((self.v / ".git").exists())

    def test_unborn_branch(self):
        git(self.v, "init", "-q")
        self.deliver()
        self.refused("no commit yet: the first commit is _sync.py --init's")

    def test_rebase_or_merge_in_progress(self):
        self.init()
        self.deliver()
        for marker, is_dir in (("rebase-merge", True), ("rebase-apply", True), ("MERGE_HEAD", False),
                               ("CHERRY_PICK_HEAD", False), ("REVERT_HEAD", False)):
            p = self.v / ".git" / marker
            if is_dir:
                p.mkdir()
            else:
                p.write_text(git(self.v, "rev-parse", "HEAD").stdout)
            self.refused(marker)
            if is_dir:
                p.rmdir()
            else:
                p.unlink()
        self.assertEqual(_canon.commit_paths(self.v, [RULES, GA], "hostr"), [RULES, GA], "positive control")

    def test_unmerged_paths(self):
        """A conflicted stash pop leaves unmerged paths and no MERGE_HEAD."""
        self.init()
        (self.v / "notes.md").write_text("stashed side\n")
        git(self.v, "stash", "push", "-q")
        (self.v / "notes.md").write_text("committed side\n")
        git(self.v, "commit", "-q", "-am", "other side")
        self.assertNotEqual(git(self.v, "stash", "pop", check=False).returncode, 0)
        self.assertTrue(git(self.v, "ls-files", "-u").stdout.strip(), "precondition: unmerged")
        self.deliver()
        self.refused("unmerged paths")

    def test_conflict_markers_and_symlinks_are_skipped(self):
        self.init()
        self.deliver()
        (self.v / RULES).write_text("<<<<<<< Updated upstream\nA\n=======\nB\n>>>>>>> Stashed changes\n")
        self.assertEqual(_canon.commit_paths(self.v, [RULES, GA], "hostr"), [GA])
        self.assertEqual(git(self.v, "show", "--name-only", "--format=", "HEAD").stdout.split(), [GA])
        outside = self.tmp / "outside.md"
        outside.write_text(V2)
        (self.v / RULES).unlink()
        (self.v / RULES).symlink_to(outside)
        self.assertEqual(_canon.commit_paths(self.v, [RULES], "hostr"), [])
        linked = self.tmp / "linked-shared"
        (linked / "research").mkdir(parents=True)
        (linked / "research" / "data-quality-2026.md").write_text(DQ)
        shutil.rmtree(self.v / "shared")
        (self.v / "shared").symlink_to(linked, target_is_directory=True)
        self.assertEqual(_canon.commit_paths(self.v, [DQ_REL], "hostr"), [], "never through a linked dir")

    def test_ignored_and_unchanged_paths_are_skipped(self):
        self.init()
        self.assertEqual(_canon.commit_paths(self.v, [RULES, GA], "hostr"), [], "nothing delivered")
        (self.v / ".gitignore").write_text("shared/\n")
        git(self.v, "add", ".gitignore")
        git(self.v, "commit", "-q", "-m", "ignore shared")
        self.deliver()
        self.assertEqual(_canon.commit_paths(self.v, [RULES, GA], "hostr"), [GA], "an ignored path stays out")

    def test_never_raises(self):
        self.assertEqual(_canon.commit_paths(self.tmp / "missing", [RULES]), [])
        self.assertEqual(_canon.commit_delivered(self.tmp / "missing", [("x",), None, (RULES, "installed")]), [])
        self.assertEqual(_canon.commit_untracked(self.tmp / "missing"), [])
        self.assertEqual(_canon.untracked(self.tmp / "missing"), [])


class StashPopMessageTest(_TwoMachines):
    """_restore_stash: a pop that failed ONLY on 'already exists, no checkout'
    no longer claims conflict markers and a blocked commit."""

    def test_untracked_collision_is_reported_truthfully(self):
        # a NON-canon path both machines created; A pushed it first
        rel = "workspaces/demo/t/2026-11-07-twin.md"
        (self.a / rel).write_text("---\nslug: t-twin\n---\n- [exp] written on A\n")
        git(self.a, "add", "-A")
        git(self.a, "commit", "-q", "-m", "A: twin")
        git(self.a, "push", "-q", "origin", "main")
        (self.b / rel).write_text("---\nslug: t-twin\n---\n- [exp] written on B\n")
        self.handoff_b.write_text(self.handoff_b.read_text() + EDIT)
        r = self.sync(self.b, "--pull-only")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("could not restore 1 untracked file(s)", r.stderr)
        self.assertIn(rel, r.stderr)
        self.assertIn("No conflict markers were written", r.stderr)
        self.assertNotIn("CONFLICTED", r.stderr)
        self.assertNotIn("Nothing will be committed", r.stderr)
        self.assertIn("auto-sync pre-pull stash", self.stash_list(self.b), "the stash is kept, not dropped")
        self.assertIn("written on B", git(self.b, "show", "stash@{0}^3:" + rel).stdout,
                      "B's copy is in the kept stash")

    def test_a_real_conflict_keeps_the_conflict_message(self):
        """Positive control: a tracked change that conflicts on pop still says so."""
        h = self.a / "workspaces" / "demo" / "docs" / "handoff.md"
        h.write_text("## 2026-11-07 s\n- host:a 2026-11-07 [next] changed on A\n")
        git(self.a, "commit", "-q", "-am", "A: handoff")
        git(self.a, "push", "-q", "origin", "main")
        self.handoff_b.write_text("## 2026-11-07 s\n- host:a 2026-11-07 [next] changed on B\n")
        r = self.sync(self.b, "--pull-only")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("stash pop CONFLICTED", r.stderr)
        self.assertNotIn("could not restore", r.stderr)

    def test_the_parser_on_captured_git_output(self):
        mod = load_auto_sync()
        err = (".gitattributes already exists, no checkout\n"
               "shared/memory-rules.md already exists, no checkout\n"
               "error: could not restore untracked files from stash\n")
        kept = "The stash entry is kept in case you need it again.\n"
        status_254 = ("On branch main\nChanges not staged for commit:\n\tmodified:   handoff.md\n\n" + kept)
        self.assertEqual(mod._untracked_collisions(self.b, err, kept), [GA, RULES])          # git 2.33
        self.assertEqual(mod._untracked_collisions(self.b, err, status_254), [GA, RULES])    # git 2.54
        conflict = "Auto-merging handoff.md\nCONFLICT (content): Merge conflict in handoff.md\n" + kept
        self.assertEqual(mod._untracked_collisions(self.b, err, conflict), [], "a real conflict is not hidden")
        self.assertEqual(mod._untracked_collisions(self.b, "", conflict), [])
        self.assertEqual(mod._untracked_collisions(self.b, err + "error: something else\n", kept), [])
        self.assertEqual(mod._untracked_collisions(self.b, "error: could not restore untracked files from stash\n",
                                                   kept), [], "no path named")


if __name__ == "__main__":
    unittest.main()
