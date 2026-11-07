"""Review safety-sync#2: every commit boundary sanitizes every CHANGED synced
markdown file, not only `<ws>/memory/*.md`.

docs/handoff.md and topic files are edited directly — the memory teammate,
/mem-save and the gap flow use the Edit tool, never `safe_write`. The v4.8
prune happened to rewrite nearly every docs/topic file through `safe_write`
(and so redacted a pasted token); the v4.9 prune writes only what it changed,
so the token went to the remote raw. `_memsan.sanitize_before_commit()` now
runs before `git add -A` in auto-sync `commit_local`, at both `_sync.py`
commit sites, in `_conflict`'s mid-rebase fold and in `_conflict.py
--continue`; `_prune` redacts as a second layer.

Per CLAUDE.md: real entry points (the hook script, the CLI, the prune), the
real hostile input, positive-signal asserts, 3 passes byte-identical, and a
growth probe (no new regex: the existing sanitizer over a bounded list).
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "hooks" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from _sync import _DEFAULT_GITIGNORE  # noqa: E402

SCRUB = ("AI_AGENT", "GOWTH_WORKSPACE", "CLAUDE_SUBAGENT", "GOWTH_MEM_OFF", "CLAUDE_CODE_ENTRYPOINT",
         "CLAUDE_PLUGIN_ROOT", "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GOWTH_MEM_GIT_TOKEN")
IDENT = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
         "GIT_COMMITTER_EMAIL": "t@x"}
SECRET = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
MARK = "[REDACTED:github-pat]"
OLD = 1_000_000_000                      # a fixed past mtime (2001-09-09)

HANDOFF = "workspaces/demo/docs/handoff.md"
REF = "workspaces/demo/docs/ref.md"
ASPECT = "workspaces/demo/wombats/2026-11-07-survey.md"
TOOLS = "shared/tools.md"


def _layout(vault: Path) -> None:
    ws = vault / "workspaces" / "demo"
    (ws / "docs").mkdir(parents=True)
    (ws / "wombats").mkdir()
    (ws / "workspace.json").write_text('{"name": "demo"}')
    (vault / HANDOFF).write_text("## 2026-11-07 s\n- host:a 2026-11-07 [next] ship the curator\n")
    (vault / REF).write_text("# ref\n\n- [ref] heron cache is LRU. Source: heron docs v2.3\n")
    (ws / "wombats" / "00-README.md").write_text("---\nslug: wombats\ntitle: Wombats\n---\n# Wombats\n")
    (vault / ASPECT).write_text("---\nslug: wombats-survey\n---\n"
                                "- [exp] wombat burrows face north in the alpine survey data\n")
    (vault / "shared").mkdir()
    (vault / TOOLS).write_text("# tools\n\n- [tool] ripgrep 14.1 — fast recursive grep\n")
    (vault / "settings.json").write_text('{"layout_version": 3}')
    (vault / "config.json").write_text(json.dumps({"active_workspace": "demo"}))
    (vault / ".gitignore").write_text(_DEFAULT_GITIGNORE)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class _Vault(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gowth_commitpriv_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.vault = self.tmp / "vault"
        self.vault.mkdir()
        (self.tmp / "home").mkdir()
        patcher = mock.patch.dict(os.environ, {
            "GOWTH_MEM_HOME": str(self.vault), "HOME": str(self.tmp / "home"),
            "CLAUDE_CONFIG_DIR": str(self.tmp / "cfg"), "GIT_CONFIG_NOSYSTEM": "1",
            "GOWTH_MEM_NO_AUTOHEAL": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)
        for k in SCRUB:
            os.environ.pop(k, None)
        self.env = dict(os.environ)
        _layout(self.vault)

    # ── helpers ──
    def git(self, *args: str, cwd: "Path | None" = None, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-c", "init.defaultBranch=main", "-c", "core.hooksPath=/dev/null", *args],
                              cwd=str(cwd or self.vault), capture_output=True, text=True,
                              env={**self.env, **IDENT}, check=check)

    def blob(self, ref_path: str, cwd: "Path | None" = None) -> bytes:
        r = subprocess.run(["git", "show", ref_path], cwd=str(cwd or self.vault), capture_output=True,
                           env={**self.env, **IDENT})
        return r.stdout if r.returncode == 0 else b""

    def run_script(self, script: str, *args: str, rc: int = 0) -> subprocess.CompletedProcess:
        r = subprocess.run([sys.executable, str(SCRIPTS / script), *args], capture_output=True, text=True,
                           env=self.env, timeout=180)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(r.returncode, rc, r.stdout + r.stderr)
        return r

    def init_repo(self) -> None:
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")

    def append(self, rel: str, text: str) -> Path:
        p = self.vault / rel
        with open(p, "a", encoding="utf-8") as f:   # a raw write: no safe_write, no sanitizer
            f.write(text)
        return p

    def head_count(self) -> int:
        return int(self.git("rev-list", "--count", "HEAD").stdout.strip())

    def status(self) -> str:
        return self.git("status", "--porcelain", "--untracked-files=all").stdout

    def assert_redacted(self, data: bytes, *kept: str) -> None:
        text = data.decode("utf-8")
        self.assertNotIn(SECRET, text)
        self.assertIn(MARK, text)
        for k in kept:
            self.assertIn(k, text)


# ── the commit boundary (auto-sync commit_local) ─────────────────────────────

class CommitOnlyTest(_Vault):
    def setUp(self):
        super().setUp()
        self.init_repo()

    def test_hand_edited_handoff_and_topic_aspect_are_redacted_in_the_commit(self):
        self.append(HANDOFF, f"- host:b 2026-11-07 [next] deploy with token {SECRET} from the CI page\n")
        self.append(ASPECT, f"- [exp] the survey export used {SECRET} once\n")
        before = self.head_count()
        self.run_script("auto-sync.py", "--commit-only")
        self.assertEqual(self.head_count(), before + 1, "the hook made no commit")
        self.assert_redacted(self.blob(f"HEAD:{HANDOFF}"), "[next] ship the curator", "from the CI page")
        self.assert_redacted(self.blob(f"HEAD:{ASPECT}"), "alpine survey data", "once")
        self.assert_redacted((self.vault / HANDOFF).read_bytes(), "from the CI page")
        self.assert_redacted((self.vault / ASPECT).read_bytes())
        self.assertEqual(self.status(), "", "everything must be committed")

    def test_shared_markdown_is_redacted_too(self):
        self.append(TOOLS, f"- [tool] gh 2.60 — auth with {SECRET}\n")
        self.run_script("auto-sync.py", "--commit-only")
        self.assert_redacted(self.blob(f"HEAD:{TOOLS}"), "ripgrep 14.1", "gh 2.60")

    def test_a_new_folder_of_untracked_files_is_scanned_file_by_file(self):
        new = self.vault / "workspaces" / "demo" / "numbats" / "2026-11-07-nest.md"
        new.parent.mkdir()
        new.write_text(f"- [exp] numbat nest camera token {SECRET}\n")
        self.run_script("auto-sync.py", "--commit-only")
        self.assert_redacted(self.blob("HEAD:workspaces/demo/numbats/2026-11-07-nest.md"), "numbat nest")

    def test_names_with_spaces_unicode_and_glob_characters(self):
        rel = "workspaces/demo/wombats/2026-11-07-café notes [x]*.md"
        (self.vault / rel).write_text(f"- [exp] odd name, token {SECRET}\n")
        self.run_script("auto-sync.py", "--commit-only")
        self.assert_redacted(self.blob(f"HEAD:{rel}"), "odd name")

    def test_a_clean_changed_file_keeps_its_bytes_and_mtime(self):
        import _memsan  # type: ignore
        raw = "# ref\r\n\r\n- [ref] café — heron cache is LRU. Source: heron docs v2.3\r\nno final newline".encode()
        ref = self.vault / REF
        ref.write_bytes(raw)
        new = self.vault / "workspaces" / "demo" / "numbats" / "2026-11-07-nest.md"
        new.parent.mkdir()
        new.write_bytes(b"- [exp] numbats nest in hollow logs, see the 2026 survey\n")
        for p in (ref, new):
            os.utime(p, (OLD, OLD))
        report = _memsan.sanitize_changed_files()
        self.assertEqual(report["scanned"], 2, report)          # positive: both were looked at
        self.assertEqual((report["sanitized"], report["skipped"]), ([], []))
        self.run_script("auto-sync.py", "--commit-only")
        self.assertEqual(self.blob(f"HEAD:{REF}"), raw)
        self.assertEqual(self.blob("HEAD:workspaces/demo/numbats/2026-11-07-nest.md"), new.read_bytes())
        for p, data in ((ref, raw), (new, b"- [exp] numbats nest in hollow logs, see the 2026 survey\n")):
            self.assertEqual(p.read_bytes(), data)
            self.assertEqual(p.stat().st_mtime_ns, OLD * 10 ** 9, f"{p.name} was rewritten")

    def test_three_passes_are_byte_identical_after_the_first(self):
        import _memsan  # type: ignore
        self.append(HANDOFF, f"- host:b [next] token {SECRET}\n")
        self.append(ASPECT, f"- [exp] key {SECRET}\n")
        r1 = _memsan.sanitize_before_commit()
        self.assertEqual(sorted(r1["sanitized"]), sorted([HANDOFF, ASPECT]))
        self.assertEqual(r1["secrets"], 2)
        after = {rel: ((self.vault / rel).read_bytes(), (self.vault / rel).stat().st_mtime_ns)
                 for rel in (HANDOFF, ASPECT)}
        for _ in range(2):
            r = _memsan.sanitize_before_commit()
            self.assertGreaterEqual(r["scanned"], 2, "the still-uncommitted files must be re-scanned")
            self.assertEqual((r["sanitized"], r["skipped"]), ([], []))
            for rel, (data, mtime) in after.items():
                self.assertEqual((self.vault / rel).read_bytes(), data)
                self.assertEqual((self.vault / rel).stat().st_mtime_ns, mtime)
        self.assert_redacted(after[HANDOFF][0], "ship the curator")

    def test_the_pass_reads_only_the_changed_files(self):
        """Bounded by the changed-file list: committed files are never walked —
        even one that already holds a token in HEAD is left alone."""
        import _memsan  # type: ignore
        folder = self.vault / "workspaces" / "demo" / "archive-notes"
        folder.mkdir()
        for i in range(40):
            (folder / f"2026-01-{i % 28 + 1:02d}-n{i}.md").write_text(f"- [exp] note {i} token {SECRET}\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "forty committed files")
        committed = {p: p.read_bytes() for p in folder.iterdir()}
        self.append(HANDOFF, f"- host:b [next] token {SECRET}\n")
        (self.vault / REF).write_text("# ref\n\n- [ref] a clean edit. Source: notes\n")
        report = _memsan.sanitize_changed_files()
        self.assertEqual(report["scanned"], 2, report)
        self.assertEqual(report["sanitized"], [HANDOFF])
        for p, data in committed.items():
            self.assertEqual(p.read_bytes(), data)


class FailClosedTest(_Vault):
    """A file that holds a secret but cannot be rewritten is kept out of the
    commit; everything else is still committed (positive signal)."""

    def setUp(self):
        super().setUp()
        self.init_repo()

    def _changes(self) -> None:
        self.append(HANDOFF, f"- host:b [next] token {SECRET}\n")
        self.append(ASPECT, f"- [exp] key {SECRET}\n")
        (self.vault / REF).write_text("# ref\n\n- [ref] heron cache is ARC now. Source: heron docs v3\n")

    def test_entries_lock_held_keeps_the_secret_files_out(self):
        from _lock import file_lock  # type: ignore
        self._changes()
        before = self.head_count()
        with file_lock("entries-demo", timeout=1.0):        # a teammate's write in flight
            r = self.run_script("auto-sync.py", "--commit-only")
        self.assertIn("left out of this commit", r.stderr)
        self.assertEqual(self.head_count(), before + 1, "the clean change must still be committed")
        self.assertIn(b"heron cache is ARC now", self.blob(f"HEAD:{REF}"))
        for rel in (HANDOFF, ASPECT):
            self.assertNotIn(SECRET.encode(), self.blob(f"HEAD:{rel}"), f"{rel} was committed raw")
            self.assertIn(SECRET, (self.vault / rel).read_text(), "never written while the lock is held")
            self.assertIn(rel, self.status())
        # the lock released: the next commit carries both files, redacted
        self.run_script("auto-sync.py", "--commit-only")
        self.assert_redacted(self.blob(f"HEAD:{HANDOFF}"), "ship the curator")
        self.assert_redacted(self.blob(f"HEAD:{ASPECT}"), "alpine survey data")
        self.assertEqual(self.status(), "")

    def test_the_file_writers_own_lock_is_honoured(self):
        """docs/handoff.md is rotated under `handoff-<ws>`: with only that lock
        held, handoff.md waits while the aspect (entries-<ws>) goes through."""
        from _lock import file_lock  # type: ignore
        self._changes()
        with file_lock("handoff-demo", timeout=1.0):
            self.run_script("auto-sync.py", "--commit-only")
        self.assertNotIn(SECRET.encode(), self.blob(f"HEAD:{HANDOFF}"))
        self.assertIn(SECRET, (self.vault / HANDOFF).read_text())
        self.assert_redacted(self.blob(f"HEAD:{ASPECT}"), "alpine survey data")

    def test_shared_lock_held_keeps_a_shared_file_out(self):
        from _lock import file_lock  # type: ignore
        self.append(TOOLS, f"- [tool] gh 2.60 — {SECRET}\n")
        (self.vault / REF).write_text("# ref\n\n- [ref] heron cache is ARC now. Source: heron docs v3\n")
        with file_lock("shared-entries", timeout=1.0):
            self.run_script("auto-sync.py", "--commit-only")
        self.assertNotIn(SECRET.encode(), self.blob(f"HEAD:{TOOLS}"))
        self.assertIn(b"ARC now", self.blob(f"HEAD:{REF}"))
        self.run_script("auto-sync.py", "--commit-only")
        self.assert_redacted(self.blob(f"HEAD:{TOOLS}"), "gh 2.60")

    def test_glob_characters_in_a_kept_out_name_are_literal(self):
        from _lock import file_lock  # type: ignore
        rel = "workspaces/demo/wombats/2026-11-07-[ab]*?.md"
        (self.vault / rel).write_text(f"- [exp] token {SECRET}\n")
        decoy = "workspaces/demo/wombats/2026-11-07-ax.md"    # what the name matches as a glob
        (self.vault / decoy).write_text("- [exp] a clean decoy entry about wombats\n")
        with file_lock("entries-demo", timeout=1.0):
            self.run_script("auto-sync.py", "--commit-only")
        self.assertNotIn(SECRET.encode(), self.blob(f"HEAD:{rel}"))
        self.assertIn(b"clean decoy", self.blob(f"HEAD:{decoy}"))

    def test_undecodable_file_with_a_secret_is_kept_out_without_one_committed(self):
        bad = self.vault / "workspaces" / "demo" / "wombats" / "2026-11-07-latin1.md"
        bad.write_bytes(b"- [exp] caf\xe9 token " + SECRET.encode() + b"\n")
        plain = self.vault / "workspaces" / "demo" / "wombats" / "2026-11-07-latin1-clean.md"
        plain.write_bytes(b"- [exp] caf\xe9 au lait, no secrets here\n")
        r = self.run_script("auto-sync.py", "--commit-only")
        self.assertIn("left out of this commit", r.stderr)
        self.assertEqual(self.blob("HEAD:workspaces/demo/wombats/2026-11-07-latin1.md"), b"")
        self.assertEqual(bad.read_bytes(), b"- [exp] caf\xe9 token " + SECRET.encode() + b"\n",
                         "an undecodable file is never rewritten lossily")
        self.assertEqual(self.blob("HEAD:workspaces/demo/wombats/2026-11-07-latin1-clean.md"),
                         b"- [exp] caf\xe9 au lait, no secrets here\n")

    def test_a_symlinked_markdown_target_is_never_rewritten(self):
        outside = self.tmp / "outside.md"
        outside.write_text(f"token {SECRET} outside the vault\n")
        link = self.vault / "workspaces" / "demo" / "docs" / "linked.md"
        os.symlink(outside, link)
        self.run_script("auto-sync.py", "--commit-only")
        self.assertEqual(outside.read_text(), f"token {SECRET} outside the vault\n")
        self.assertEqual(self.blob("HEAD:workspaces/demo/docs/linked.md"), str(outside).encode(),
                         "git stores the link's target path, never its content")


class LockTableTest(unittest.TestCase):
    def test_each_file_is_rewritten_under_its_writers_locks(self):
        import _memsan  # type: ignore
        cases = {
            "workspaces/demo/docs/handoff.md": ("entries-demo", "handoff-demo"),
            "workspaces/demo/docs/questions.md": ("entries-demo", "gaps-demo"),
            "workspaces/demo/docs/ref.md": ("entries-demo",),
            "workspaces/demo/journal/sessions/2026-11-07-ab12cd34.md":
                ("entries-demo", "capture-demo", "journal-demo"),
            "workspaces/demo/journal/2026-11-07.md": ("entries-demo", "journal-demo"),
            "workspaces/demo/journal/_salvage.md": ("entries-demo", "journal-demo"),
            "workspaces/demo/wombats/2026-11-07-survey.md": ("entries-demo", "topic-forget-demo"),
            "workspaces/demo/wombats/lessons.md": ("entries-demo", "topic-forget-demo"),
            "workspaces/demo/wombats/00-README.md": ("entries-demo", "topic-forget-demo", "moc"),
            "workspaces/demo/skills/deploy.md": ("entries-demo",),
            "workspaces/demo/research/heron/distilled.md": ("entries-demo",),
            "workspaces/demo/_MAP.md": ("entries-demo", "moc"),
            "workspaces/demo/memory/MEMORY.md": ("memfile-demo",),
            "workspaces/_MAP.md": ("shared-entries", "moc"),
            "shared/secrets.md": ("shared-entries",),
            "shared/_MAP.md": ("shared-entries", "moc"),
            # a workspace named "shared" can never produce the shared/ lock name
            "workspaces/shared/docs/ref.md": ("entries-shared",),
        }
        for rel, want in cases.items():
            self.assertEqual(_memsan.lock_names_for(rel), want, rel)
        # one lock for shared/: the commit-boundary pass and _topic's [secret-ref]
        # appends must serialise on the SAME name (the two drafts had diverged)
        import _topic  # type: ignore
        self.assertEqual(_memsan.SHARED_LOCK, _topic.SHARED_ENTRIES_LOCK)


class RefusalReplayTest(_Vault):
    """CLAUDE.md: the new early return (`_unstage` failed → commit nothing) sits
    in front of commit_local's guards — replay HEAD's refusal cases through it."""

    def setUp(self):
        super().setUp()
        self.init_repo()

    def test_marker_guard_still_refuses_with_a_file_kept_out(self):
        from _lock import file_lock  # type: ignore
        self.append(HANDOFF, f"- host:b [next] token {SECRET}\n")
        (self.vault / REF).write_text("# ref\n<<<<<<< HEAD\nmine\n=======\ntheirs\n>>>>>>> peer\n")
        before = self.head_count()
        with file_lock("entries-demo", timeout=1.0):
            r = self.run_script("auto-sync.py", "--commit-only")
        self.assertIn("raw conflict markers", r.stderr)
        self.assertEqual(self.head_count(), before)
        self.assertNotIn(b"<<<<<<<", self.blob(f"HEAD:{REF}"))
        self.assertNotIn(SECRET.encode(), self.blob(f"HEAD:{HANDOFF}"))

    def test_unmerged_guard_still_refuses_first(self):
        from _lock import file_lock  # type: ignore
        self.git("checkout", "-q", "-b", "peer")
        (self.vault / REF).write_text("# ref\npeer line\n")
        self.git("commit", "-qam", "peer")
        self.git("checkout", "-q", "main")
        (self.vault / REF).write_text("# ref\nmain line\n")
        self.git("commit", "-qam", "main")
        self.git("merge", "peer", check=False)                  # leaves REF unmerged
        self.append(HANDOFF, f"- host:b [next] token {SECRET}\n")
        before = self.head_count()
        with file_lock("entries-demo", timeout=1.0):
            r = self.run_script("auto-sync.py", "--commit-only")
        self.assertIn("unmerged path", r.stderr)
        self.assertEqual(self.head_count(), before)

    def test_a_file_that_cannot_be_unstaged_stops_the_whole_commit(self):
        mod = _load("gowth_auto_sync_refusal", SCRIPTS / "auto-sync.py")
        import _memsan  # type: ignore
        self.append(HANDOFF, f"- host:b [next] token {SECRET}\n")
        (self.vault / REF).write_text("# ref\n\n- [ref] a clean edit. Source: notes\n")
        before = self.head_count()
        err = io.StringIO()
        with mock.patch.object(_memsan, "sanitize_before_commit",
                               return_value={"scanned": 2, "sanitized": [], "secrets": 0, "skipped": [HANDOFF]}), \
                mock.patch.object(_memsan, "keep_out_of_commit", side_effect=lambda gh, rels: list(rels)), \
                contextlib.redirect_stderr(err):
            made = mod.commit_local(self.vault, "box", quiet=True)
        self.assertFalse(made)
        self.assertIn("REFUSING to commit", err.getvalue())
        self.assertEqual(self.head_count(), before, "nothing may be committed while a secret file is staged")


# ── the manual commit paths (_sync.py) ───────────────────────────────────────

class ManualSyncTest(_Vault):
    def _remote(self) -> Path:
        bare = self.tmp / "remote.git"
        self.git("init", "-q", "--bare", "-b", "main", str(bare), cwd=self.tmp)
        cfg = json.loads((self.vault / "config.json").read_text())
        cfg.update({"remote": str(bare), "branch": "main", "host_id": "box-a"})
        (self.vault / "config.json").write_text(json.dumps(cfg))
        return bare

    def test_push_only_redacts_before_the_commit_and_the_push(self):
        bare = self._remote()
        self.init_repo()
        self.git("remote", "add", "origin", str(bare))
        self.git("push", "-q", "-u", "origin", "main")
        self.append(HANDOFF, f"- host:b [next] token {SECRET}\n")
        r = self.run_script("_sync.py", "--push-only")
        self.assertIn("redacted 1 secret(s) before commit", r.stdout)
        self.assert_redacted(self.blob(f"main:{HANDOFF}", cwd=bare), "ship the curator")

    def test_push_only_keeps_a_locked_secret_file_out_of_commit_and_push(self):
        from _lock import file_lock  # type: ignore
        bare = self._remote()
        self.init_repo()
        self.git("remote", "add", "origin", str(bare))
        self.git("push", "-q", "-u", "origin", "main")
        self.append(ASPECT, f"- [exp] key {SECRET}\n")
        (self.vault / REF).write_text("# ref\n\n- [ref] heron cache is ARC now. Source: heron docs v3\n")
        with file_lock("entries-demo", timeout=1.0):
            self.run_script("_sync.py", "--push-only")
        self.assertNotIn(SECRET.encode(), self.blob(f"main:{ASPECT}", cwd=bare))
        self.assertIn(b"ARC now", self.blob(f"main:{REF}", cwd=bare))

    def test_init_on_an_unborn_branch_keeps_a_locked_secret_file_out(self):
        """`restore --staged` needs a HEAD: on the first commit it failed and
        the raw file went into the initial commit and the push."""
        from _lock import file_lock  # type: ignore
        bare = self._remote()
        self.append(HANDOFF, f"- host:a [next] token {SECRET}\n")
        with file_lock("entries-demo", timeout=1.0):
            r = self.run_script("_sync.py", "--init")
        self.assertIn("init: pushed to origin/main", r.stdout)
        tree = self.git("ls-tree", "-r", "--name-only", "main", cwd=bare).stdout
        self.assertIn(REF, tree, "the rest of the vault was committed and pushed")
        self.assertNotIn(HANDOFF, tree, "the raw file went into the initial commit")
        self.assertIn(SECRET, (self.vault / HANDOFF).read_text())
        # lock released: the next /mem-sync commits it redacted
        self.run_script("_sync.py")
        self.assert_redacted(self.blob(f"main:{HANDOFF}", cwd=bare), "ship the curator")


# ── second layer: the prune ──────────────────────────────────────────────────

class PruneSecondLayerTest(_Vault):
    def prune(self) -> None:
        self.run_script("_prune.py", "--all-workspaces")

    def test_a_secret_is_redacted_with_nothing_to_delete(self):
        aspect = self.append(ASPECT, f"- [exp] the export used {SECRET} once\n")
        ref = self.vault / REF
        os.utime(ref, (OLD, OLD))
        ref_bytes = ref.read_bytes()
        self.prune()
        self.assert_redacted(aspect.read_bytes(), "alpine survey data", "once")
        self.assertTrue(aspect.read_text().endswith("once\n"), "the trailing newline is kept")
        after = (aspect.read_bytes(), aspect.stat().st_mtime_ns)
        for _ in range(2):
            self.prune()
            self.assertEqual((aspect.read_bytes(), aspect.stat().st_mtime_ns), after)
        self.assertEqual(ref.read_bytes(), ref_bytes)
        self.assertEqual(ref.stat().st_mtime_ns, OLD * 10 ** 9, "a clean file must keep its mtime")

    def test_the_new_write_branch_keeps_heads_refusals(self):
        """CLAUDE.md: the secret branch sits in front of the prune's existing
        refusals — a dry run writes nothing, a held entries lock skips the file."""
        from _lock import file_lock  # type: ignore
        aspect = self.append(ASPECT, f"- [exp] the export used {SECRET} once\n")
        os.utime(aspect, (OLD, OLD))
        raw = aspect.read_bytes()
        self.run_script("_prune.py", "--all-workspaces", "--dry-run")
        self.assertEqual((aspect.read_bytes(), aspect.stat().st_mtime_ns), (raw, OLD * 10 ** 9))
        with file_lock("entries-demo", timeout=1.0):
            r = self.run_script("_prune.py", "--all-workspaces")
        self.assertIn("entries lock held", r.stdout)
        self.assertEqual((aspect.read_bytes(), aspect.stat().st_mtime_ns), (raw, OLD * 10 ** 9))
        self.prune()                                          # positive control: free lock → redacted
        self.assert_redacted(aspect.read_bytes(), "once")


# ── the mid-rebase fold in _conflict.package_conflict ───────────────────────

class FoldTest(unittest.TestCase):
    """Review R5 extended: the fold (`git add -u` of a file written while the
    rebase was stopped) runs the full commit-boundary pass first, so a token
    pasted into docs/handoff.md mid-rebase is not folded into a replayed commit."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gowth_commitpriv_fold_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        patcher = mock.patch.dict(os.environ, {"HOME": str(self.tmp), "GIT_CONFIG_NOSYSTEM": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)
        for k in SCRUB:
            os.environ.pop(k, None)
        self.bare = self.tmp / "bare.git"
        self.a, self.b = self.tmp / "A", self.tmp / "B"
        self.git(self.tmp, "init", "-q", "--bare", "-b", "main", str(self.bare))
        self.git(self.tmp, "clone", "-q", str(self.bare), str(self.a))
        _layout(self.a)
        (self.a / "workspaces" / "demo" / "memory").mkdir()
        os.environ["GOWTH_MEM_HOME"] = str(self.a)
        import _memfile  # type: ignore
        self.mem_rel = "workspaces/demo/memory/MEMORY.md"
        (self.a / self.mem_rel).write_text(_memfile.render("demo") + "- base note\n")
        self.git(self.a, "add", "-A")
        self.git(self.a, "commit", "-q", "-m", "base")
        self.git(self.a, "push", "-q", "origin", "main")
        self.git(self.tmp, "clone", "-q", str(self.bare), str(self.b))

    @staticmethod
    def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-c", "init.defaultBranch=main", "-c", "core.hooksPath=/dev/null", *args],
                              cwd=str(cwd), capture_output=True, text=True, env={**os.environ, **IDENT},
                              check=check)

    def test_a_handoff_token_written_mid_rebase_is_redacted_before_the_fold(self):
        mem_a, mem_b = self.a / self.mem_rel, self.b / self.mem_rel
        mem_a.write_text(mem_a.read_text() + "- note from A\n")
        self.git(self.a, "commit", "-q", "-am", "A note")
        self.git(self.a, "push", "-q", "origin", "main")
        for n in (1, 2):
            mem_b.write_text(mem_b.read_text() + f"- note from B{n}\n")
            self.git(self.b, "commit", "-q", "-am", f"B note {n}")
        r = self.git(self.b, "pull", "--rebase", "origin", "main", check=False)
        self.assertIn("CONFLICT", r.stdout + r.stderr)
        os.environ["GOWTH_MEM_HOME"] = str(self.b)
        handoff = self.b / HANDOFF
        import _conflict  # type: ignore
        real = _conflict.merge_memfile

        def racing(gh, rel):
            ok = real(gh, rel)
            with open(handoff, "a", encoding="utf-8") as f:   # another session's Edit-tool write
                f.write(f"- host:b [next] token {SECRET} pasted mid-rebase\n")
            return ok

        with mock.patch.object(_conflict, "merge_memfile", racing):
            result = _conflict.package_conflict(host="box-b")
        self.assertIsNone(result)
        self.assertFalse((self.b / ".git" / "rebase-merge").exists())
        for ref in ("HEAD", "HEAD~1"):
            shown = self.git(self.b, "show", f"{ref}:{HANDOFF}", check=False).stdout
            self.assertNotIn(SECRET, shown, f"{ref} carries the raw secret")
        self.assertIn(MARK, self.git(self.b, "show", f"HEAD:{HANDOFF}").stdout)
        self.assertIn("pasted mid-rebase", handoff.read_text())
        self.assertNotIn(SECRET, handoff.read_text())


# ── growth (CLAUDE.md rule 3: no new regex — the existing sanitizer over a
#    bounded file list; ~linear per doubling on adversarial shapes) ──────────

SHAPES = {
    "unclosed-private": lambda n: "<private>" * (n // 9),
    "whitespace-after-key": lambda n: "token=" + " \t" * (n // 2) + "x",
    "token-prefix-flood": lambda n: "ghp_" * (n // 4),
    "unclosed-pem": lambda n: "-----BEGIN RSA PRIVATE KEY-----\n" + ("QUJD" * 16 + "\n") * (n // 65),
    "separator-flood": lambda n: "-----" * (n // 5),
    "secret-flood": lambda n: (f"token {SECRET} ") * (n // 47),
}


class GrowthTest(_Vault):
    def setUp(self):
        super().setUp()
        self.init_repo()

    def test_the_pass_is_linear_on_adversarial_shapes(self):
        import _memsan  # type: ignore
        target = self.vault / "workspaces" / "demo" / "wombats" / "2026-11-07-flood.md"
        for shape, make in SHAPES.items():
            times = []
            for n in (10_000, 20_000, 40_000):
                best = float("inf")
                for _ in range(3):           # best of 3, re-seeded: one pass includes git + I/O jitter
                    target.write_text(make(n))
                    t0 = time.perf_counter()
                    report = _memsan.sanitize_changed_files()
                    best = min(best, time.perf_counter() - t0)
                    self.assertEqual(report["scanned"], 1, f"{shape}: {report}")   # positive: it was read
                    self.assertEqual(report["skipped"], [], shape)
                times.append(best)
            self.assertLess(times[2], 2.0, f"{shape}: {times}")
            if times[2] > 0.05:
                self.assertLess(times[2], 6 * max(times[0], 1e-3), f"{shape} not linear: {times}")
        self.assertNotIn(SECRET, target.read_text())          # the last shape was rewritten
        self.assertIn(MARK, target.read_text())


if __name__ == "__main__":
    unittest.main()
