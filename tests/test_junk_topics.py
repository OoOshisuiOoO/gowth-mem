"""v4.7.6 — `_validate.py` finds and removes the README-only junk folders the
pre-4.7.6 router minted (`<topic>-<aspect>/00-README.md`, 31 in the live vault).

Deletion is only ever of a PROVABLY empty folder: a real (non-symlink)
top-level directory holding only a README that is the pristine default
skeleton — frontmatter included — and named after the frontmatter slug of a
file in another folder; and only on an explicit `--prune-junk`. Every "leave it alone" branch is pinned, each fixture
is built with the REAL `ensure_topic_folder()` (exactly how the old router
produced the junk), and every case goes through the real `_validate.py` CLI.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "hooks" / "scripts"

ASPECT = ("---\nslug: alpha-zebra-quartz\ntitle: Zebra\ntype: aspect\ndate: 2026-09-01\n"
          "topic: alpha\naspect: zebra-quartz\nstatus: active\n---\n\n"
          "- [exp] zebra quartz lantern migration because the lantern cache went stale\n")


def _env(home: Path) -> dict:
    env = dict(os.environ, GOWTH_MEM_HOME=str(home))
    for k in ("GOWTH_WORKSPACE", "AI_AGENT", "CLAUDE_SUBAGENT"):
        env.pop(k, None)
    return env


def _py(home: Path, code: str) -> str:
    r = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1])\n" + code,
                        str(SCRIPTS)], env=_env(home), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout


def _validate(home: Path, *args: str) -> str:
    r = subprocess.run([sys.executable, str(SCRIPTS / "_validate.py"), *args, "--ws", "w1"],
                       env=_env(home), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout


class JunkTopicFolderTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.home = Path(self._td.name)
        self.ws = self.home / "workspaces" / "w1"
        (self.ws / "alpha").mkdir(parents=True)
        (self.ws / "workspace.json").write_text("{}")
        _py(self.home, "from _topic import ensure_topic_folder\nensure_topic_folder('alpha', ws='w1')\n")
        (self.ws / "alpha" / "2026-09-01-zebra-quartz.md").write_text(ASPECT)

    def tearDown(self):
        self._td.cleanup()

    def _mint(self, slug: str) -> Path:
        # Exactly what the pre-4.7.6 route() did with an aspect's frontmatter slug.
        _py(self.home, f"from _topic import ensure_topic_folder\nensure_topic_folder({slug!r}, ws='w1')\n")
        return self.ws / slug

    def test_minted_folder_is_reported_then_removed_and_real_topic_untouched(self):
        junk = self._mint("alpha-zebra-quartz")
        before = {p: p.read_bytes() for p in (self.ws / "alpha").iterdir()}
        scan = json.loads(_validate(self.home, "--scan", "--json"))
        flagged = [f for f in scan if any(i.startswith("junk-topic-folder:") for i in f["issues"])]
        self.assertEqual([Path(f["file"]).parent.name for f in flagged], ["alpha-zebra-quartz"])
        self.assertTrue(flagged[0]["issues"][0].endswith("2026-09-01-zebra-quartz.md"))

        out = _validate(self.home, "--prune-junk")
        self.assertIn("-junk folder: workspaces/w1/alpha-zebra-quartz", out)
        self.assertIn("removed 1 junk README-only folder(s)", out)
        self.assertFalse(junk.exists())
        after = {p: p.read_bytes() for p in (self.ws / "alpha").iterdir()}
        self.assertEqual(before, after)   # the real topic is byte-identical

    def test_repeated_fix_is_a_noop(self):
        self._mint("alpha-zebra-quartz")
        _validate(self.home, "--prune-junk")
        snapshot = sorted(str(p.relative_to(self.ws)) for p in self.ws.rglob("*"))
        for _ in range(2):
            self.assertIn("removed 0 junk", _validate(self.home, "--prune-junk"))
            self.assertEqual(sorted(str(p.relative_to(self.ws)) for p in self.ws.rglob("*")), snapshot)

    def test_finder_ds_store_is_tolerated(self):
        junk = self._mint("alpha-zebra-quartz")
        (junk / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")
        self.assertIn("removed 1 junk", _validate(self.home, "--prune-junk"))
        self.assertFalse(junk.exists())

    def test_skeleton_not_named_after_any_slug_is_left_alone(self):
        # e.g. a placeholder topic made on purpose with /mem-topic --ensure.
        keep = self._mint("ema-cross")
        self.assertIn("removed 0 junk", _validate(self.home, "--prune-junk"))
        self.assertTrue((keep / "00-README.md").is_file())

    def test_edited_readme_is_left_alone(self):
        junk = self._mint("alpha-zebra-quartz")
        readme = junk / "00-README.md"
        readme.write_text(readme.read_text().replace("Cốt lõi 1 dòng (TODO).", "Real summary a user wrote."))
        self.assertIn("removed 0 junk", _validate(self.home, "--prune-junk"))
        self.assertIn("Real summary", readme.read_text())

    def test_folder_that_gained_a_file_is_left_alone(self):
        # The stranded-lesson case: README + a real lessons.md must never go.
        junk = self._mint("alpha-zebra-quartz")
        (junk / "lessons.md").write_text("# Lessons\n\n## Entries\n\n## [2026-07-16] real lesson\n")
        self.assertIn("removed 0 junk", _validate(self.home, "--prune-junk"))
        self.assertTrue((junk / "lessons.md").is_file())

    def test_nested_folders_are_never_candidates(self):
        nested = self.ws / "domain" / "alpha-zebra-quartz"
        _py(self.home, "from _topic import ensure_topic_folder\n"
                       "ensure_topic_folder('alpha-zebra-quartz', ws='w1', parents=['domain'])\n")
        self.assertIn("removed 0 junk", _validate(self.home, "--prune-junk"))
        self.assertTrue((nested / "00-README.md").is_file())

    def test_workspace_map_no_longer_lists_the_junk(self):
        self._mint("alpha-zebra-quartz")
        _py(self.home, "from _moc import rebuild_workspace_moc\nrebuild_workspace_moc('w1')\n")
        self.assertIn("alpha-zebra-quartz", (self.ws / "_MAP.md").read_text())   # listed before
        _validate(self.home, "--prune-junk")
        self.assertNotIn("alpha-zebra-quartz", (self.ws / "_MAP.md").read_text())
        self.assertIn("alpha", (self.ws / "_MAP.md").read_text())

    def test_fix_alone_never_deletes(self):
        junk = self._mint("alpha-zebra-quartz")
        out = _validate(self.home, "--fix")
        self.assertNotIn("junk", out)
        self.assertTrue((junk / "00-README.md").is_file())

    def test_slug_stamped_by_the_same_run_is_not_evidence(self):
        # Review H1: an aspect with NO frontmatter could never have minted a
        # folder, but --fix stamps `slug: gc-ema` on it; pruning in that same
        # run used to delete the user's deliberately made `gc-ema/`.
        (self.ws / "gc").mkdir()
        (self.ws / "gc" / "2026-09-01-ema.md").write_text("- [exp] ema cross on gold works because trend\n")
        keep = self._mint("gc-ema")
        out = _validate(self.home, "--fix", "--prune-junk")
        self.assertIn("removed 0 junk", out)
        self.assertIn("+frontmatter: workspaces/w1/gc/2026-09-01-ema.md", out)
        self.assertTrue((keep / "00-README.md").is_file())

    def test_curated_frontmatter_is_never_junk(self):
        # Review M2: aliases/status/type/title are curation, even with an
        # untouched body. `[[zq-runbook]]` must keep resolving.
        junk = self._mint("alpha-zebra-quartz")
        readme = junk / "00-README.md"
        readme.write_text(readme.read_text().replace("aliases: []", "aliases: [zq-runbook]"))
        self.assertIn("removed 0 junk", _validate(self.home, "--prune-junk"))
        self.assertIn("zq-runbook", readme.read_text())

    def test_typed_titled_topic_is_never_junk(self):
        _py(self.home, "from _topic import ensure_topic_folder\n"
                       "ensure_topic_folder('alpha-zebra-quartz', ws='w1', topic_type='strategy',"
                       " title='Zebra quartz playbook')\n")
        self.assertIn("removed 0 junk", _validate(self.home, "--prune-junk"))
        self.assertTrue((self.ws / "alpha-zebra-quartz" / "00-README.md").is_file())

    def test_symlinked_folder_is_never_touched(self):
        # Review M1: `--prune-junk` followed a junk-named symlink, unlinked the
        # files at its TARGET (outside the vault), then failed on rmdir.
        outside = Path(self._td.name) / "outside" / "alpha-zebra-quartz"
        outside.mkdir(parents=True)
        _py(self.home, "from _topic import ensure_topic_folder\nensure_topic_folder('alpha-zebra-quartz', ws='w1')\n")
        (self.ws / "alpha-zebra-quartz" / "00-README.md").rename(outside / "00-README.md")
        (self.ws / "alpha-zebra-quartz").rmdir()
        (outside / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")
        (self.ws / "alpha-zebra-quartz").symlink_to(outside, target_is_directory=True)
        scan = json.loads(_validate(self.home, "--scan", "--json"))
        self.assertFalse([f for f in scan if any(i.startswith("junk-topic-folder") for i in f["issues"])])
        self.assertIn("removed 0 junk", _validate(self.home, "--prune-junk"))
        self.assertEqual(sorted(x.name for x in outside.iterdir()), [".DS_Store", "00-README.md"])
        self.assertTrue((self.ws / "alpha-zebra-quartz").is_symlink())

    def test_in_vault_alias_symlink_is_never_touched(self):
        # The layer the resolved-parent check cannot cover: an alias whose
        # target sits in the workspace root itself (review M1, in-vault case).
        junk = self._mint("alpha-zebra-quartz")
        target = self.ws / "zz-target"
        junk.rename(target)
        (self.ws / "alpha-zebra-quartz").symlink_to(target, target_is_directory=True)
        scan = json.loads(_validate(self.home, "--scan", "--json"))
        self.assertFalse([f for f in scan if any(i.startswith("junk-topic-folder") for i in f["issues"])])
        self.assertIn("removed 0 junk", _validate(self.home, "--prune-junk"))
        self.assertTrue((target / "00-README.md").is_file())

    def test_symlinked_readme_is_never_junk(self):
        junk = self._mint("alpha-zebra-quartz")
        real = Path(self._td.name) / "elsewhere.md"
        (junk / "00-README.md").rename(real)
        (junk / "00-README.md").symlink_to(real)
        scan = json.loads(_validate(self.home, "--scan", "--json"))   # not even reported
        self.assertFalse([f for f in scan if any(i.startswith("junk-topic-folder") for i in f["issues"])])
        self.assertIn("removed 0 junk", _validate(self.home, "--prune-junk"))
        self.assertTrue(real.is_file())
        self.assertTrue((junk / "00-README.md").is_symlink())

    def test_edit_racing_the_delete_is_restored_not_lost(self):
        # Review L4: an edit landing between the pristine check and the unlink.
        junk = self._mint("alpha-zebra-quartz")
        code = (
            "import sys, os; sys.path.insert(0, sys.argv[1])\n"
            "from pathlib import Path\n"
            "import _validate as V\n"
            "folder = Path(sys.argv[2])\n"
            "real = V._junk_reason\n"
            "def racing(*a, **k):\n"
            "    src = real(*a, **k)\n"
            "    r = folder / '00-README.md'\n"
            "    r.write_text(r.read_text() + '\\nUSER: flush the lantern cache hourly\\n')\n"
            "    return src\n"
            "V._junk_reason = racing\n"
            "print(V.remove_junk_folder(folder, 'w1'))\n"
        )
        r = subprocess.run([sys.executable, "-c", code, str(SCRIPTS), str(junk)], env=_env(self.home),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "False")
        self.assertIn("USER: flush the lantern cache hourly", (junk / "00-README.md").read_text())
        self.assertEqual(sorted(x.name for x in junk.iterdir()), ["00-README.md"])

    def _run_patched(self, junk: Path, patch: str) -> subprocess.CompletedProcess:
        code = ("import sys, os; sys.path.insert(0, sys.argv[1])\n"
                "from pathlib import Path\n"
                "import _validate as V\n"
                "folder = Path(sys.argv[2])\n" + patch +
                "print(V.remove_junk_folder(folder, 'w1'))\n")
        return subprocess.run([sys.executable, "-c", code, str(SCRIPTS), str(junk)], env=_env(self.home),
                              capture_output=True, text=True, timeout=60)

    def test_interrupt_mid_prune_puts_the_readme_back(self):
        # Round-2 review L1: a Ctrl-C between the rename and the delete left
        # only `.00-README.md.pruning-<pid>` — invisible to scan/prune/index.
        junk = self._mint("alpha-zebra-quartz")
        before = (junk / "00-README.md").read_bytes()
        r = self._run_patched(junk, (
            "real = V._is_pristine_skeleton\n"
            "calls = []\n"
            "def boom(*a, **k):\n"
            "    calls.append(1)\n"
            "    if len(calls) == 2: raise KeyboardInterrupt\n"   # the held-bytes re-check
            "    return real(*a, **k)\n"
            "V._is_pristine_skeleton = boom\n"))
        self.assertIn("KeyboardInterrupt", r.stderr)
        self.assertEqual((junk / "00-README.md").read_bytes(), before)
        self.assertEqual(sorted(x.name for x in junk.iterdir()), ["00-README.md"])

    def test_file_landing_before_rmdir_keeps_folder_and_landing(self):
        junk = self._mint("alpha-zebra-quartz")
        r = self._run_patched(junk, (
            "real_rmdir = V.Path.rmdir\n"
            "def racing(self):\n"
            "    (self / 'lessons.md').write_text('## [2026-09-28] a lesson filed in the last instant\\n')\n"
            "    return real_rmdir(self)\n"
            "V.Path.rmdir = racing\n"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "False")
        self.assertEqual(sorted(x.name for x in junk.iterdir()), ["00-README.md", "lessons.md"])
        self.assertIn("# Alpha Zebra Quartz", (junk / "00-README.md").read_text())   # landing is back

    def test_dangling_ds_store_symlink_disqualifies(self):
        junk = self._mint("alpha-zebra-quartz")
        (junk / ".DS_Store").symlink_to(Path(self._td.name) / "gone")
        scan = json.loads(_validate(self.home, "--scan", "--json"))   # not even reported
        self.assertFalse([f for f in scan if any(i.startswith("junk-topic-folder") for i in f["issues"])])
        self.assertIn("removed 0 junk", _validate(self.home, "--prune-junk"))
        self.assertTrue((junk / "00-README.md").is_file())

    def test_abandoned_prune_leftover_is_recovered(self):
        # A prune killed hard (no chance to restore): its held README comes back
        # on the next run once that process is gone.
        junk = self._mint("alpha-zebra-quartz")
        readme = junk / "00-README.md"
        readme.write_text(readme.read_text().replace("Cốt lõi 1 dòng (TODO).", "Kept summary."))
        dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                              capture_output=True, text=True).stdout.strip()
        readme.rename(junk / f".00-README.md.pruning-{dead}")
        _validate(self.home, "--prune-junk")
        self.assertEqual(sorted(x.name for x in junk.iterdir()), ["00-README.md"])
        self.assertIn("Kept summary.", readme.read_text())

    def test_restore_never_clobbers_a_newer_readme(self):
        junk = self._mint("alpha-zebra-quartz")
        readme = junk / "00-README.md"
        held = junk / ".00-README.md.pruning-1"
        code = ("import sys; sys.path.insert(0, sys.argv[1])\n"
                "from pathlib import Path\nimport _validate as V\n"
                "held, readme = Path(sys.argv[2]), Path(sys.argv[3])\n"
                "V._restore(held, readme, pristine=(sys.argv[4] == '1'))\n")
        for pristine in ("1", "0"):
            held.write_text("USER EDIT that raced the prune\n")
            readme.write_text("# newer README written meanwhile\n")
            r = subprocess.run([sys.executable, "-c", code, str(SCRIPTS), str(held), str(readme), pristine],
                               env=_env(self.home), capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(readme.read_text(), "# newer README written meanwhile\n")
            self.assertFalse(held.exists())
        conflicts = [x for x in junk.iterdir() if x.name.startswith("00-README.conflict-")]
        self.assertEqual(len(conflicts), 1)                      # only the EDITED copy is kept
        self.assertIn("USER EDIT", conflicts[0].read_text())

    def test_file_landing_before_the_relist_is_never_deleted(self):
        # Round-3 review M1: only the re-list stood between the delete loop and
        # a lesson filed right after the held README was verified. Two layers
        # now (the re-list, and a loop that unlinks tolerated extras only);
        # this pins the invariant — a mutation must remove both to break it.
        junk = self._mint("alpha-zebra-quartz")
        r = self._run_patched(junk, (
            "real = V._is_pristine_skeleton\n"
            "calls = []\n"
            "def racing(*a, **k):\n"
            "    calls.append(1)\n"
            "    if len(calls) == 2:\n"
            "        (folder / 'lessons.md').write_text('## [2026-09-28] filed mid-prune\\n')\n"
            "    return real(*a, **k)\n"
            "V._is_pristine_skeleton = racing\n"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "False")
        self.assertEqual(sorted(x.name for x in junk.iterdir()), ["00-README.md", "lessons.md"])

    def test_one_undeletable_file_does_not_abort_the_run(self):
        # Round-3 review L1: a PermissionError on one folder's .DS_Store used to
        # propagate and leave every later folder unprocessed.
        (self.ws / "alpha" / "2026-09-02-nebula.md").write_text(
            ASPECT.replace("alpha-zebra-quartz", "alpha-nebula").replace("zebra-quartz", "nebula"))
        first = self._mint("alpha-nebula")
        second = self._mint("alpha-zebra-quartz")
        (first / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")
        code = ("import sys; sys.path.insert(0, sys.argv[1])\n"
                "from pathlib import Path\nimport _validate as V\n"
                "real = V.Path.unlink\n"
                "def stuck(self, *a, **k):\n"
                "    if self.name == '.DS_Store' and self.parent.name == 'alpha-nebula':\n"
                "        raise PermissionError(1, 'Operation not permitted')\n"
                "    return real(self, *a, **k)\n"
                "V.Path.unlink = stuck\n"
                "print(V._prune_junk('w1'))\n")
        r = subprocess.run([sys.executable, "-c", code, str(SCRIPTS)], env=_env(self.home),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip().splitlines()[-1], "1")
        self.assertTrue((first / "00-README.md").is_file())      # restored, not lost
        self.assertFalse(second.exists())                         # the run went on

    def test_stale_leftover_is_recovered_even_when_its_pid_is_reused(self):
        # Round-3 review L3: a leftover named after a LIVE pid (reuse, pid 1,
        # containers) was stranded forever and invisible to --scan.
        junk = self._mint("alpha-zebra-quartz")
        readme = junk / "00-README.md"
        readme.write_text(readme.read_text().replace("Cốt lõi 1 dòng (TODO).", "Kept summary."))
        readme.rename(junk / f".00-README.md.pruning-{os.getpid()}")   # this test runner: alive
        self.assertIn("removed 0 junk", _validate(self.home, "--prune-junk"))
        self.assertFalse(readme.exists())                           # fresh + live pid: in use
        code = ("import sys, time; sys.path.insert(0, sys.argv[1])\n"
                "import _validate as V\n"
                "now = time.time\n"
                "V.time.time = lambda: now() + 2 * 3600\n"                # an hour+ later
                "print([h.name for h in V.abandoned_prunes(V.workspace_dir('w1'))])\n"
                "print(V._prune_junk('w1'))\n")
        r = subprocess.run([sys.executable, "-c", code, str(SCRIPTS)], env=_env(self.home),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(".00-README.md.pruning-", r.stdout.splitlines()[0])   # reported
        self.assertIn("Kept summary.", readme.read_text())                 # and restored

    def test_recovery_never_replaces_an_existing_readme(self):
        junk = self._mint("alpha-zebra-quartz")
        dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                              capture_output=True, text=True).stdout.strip()
        leftover = junk / f".00-README.md.pruning-{dead}"
        leftover.write_text("an old held copy\n")
        (junk / "00-README.md").write_text("# the current README\n")
        _validate(self.home, "--prune-junk")
        self.assertEqual((junk / "00-README.md").read_text(), "# the current README\n")
        self.assertEqual(leftover.read_text(), "an old held copy\n")

    def test_prune_backfills_the_ignore_rule_and_git_honours_it(self):
        gi = self.home / ".gitignore"
        gi.write_text("config.json\nstate.json\n")                 # an old vault's ignore file
        _validate(self.home, "--prune-junk")
        self.assertIn(".*.pruning-*", gi.read_text().splitlines())
        (self.home / "workspaces" / "w1" / "alpha" / ".00-README.md.pruning-7").write_text("x")
        subprocess.run(["git", "init", "-q", str(self.home)], check=True, capture_output=True)
        r = subprocess.run(["git", "-C", str(self.home), "check-ignore", "-q",
                            "workspaces/w1/alpha/.00-README.md.pruning-7"])
        self.assertEqual(r.returncode, 0)                            # ignored

    def test_scan_reports_an_abandoned_prune(self):
        # Round-4 review L3: the --scan report itself was untested.
        junk = self._mint("alpha-zebra-quartz")
        dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                              capture_output=True, text=True).stdout.strip()
        (junk / "00-README.md").rename(junk / f".00-README.md.pruning-{dead}")
        scan = json.loads(_validate(self.home, "--scan", "--json"))
        found = [f for f in scan if any(i.startswith("abandoned-prune") for i in f["issues"])]
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0]["file"].endswith(f".00-README.md.pruning-{dead}"))


if __name__ == "__main__":
    unittest.main()
