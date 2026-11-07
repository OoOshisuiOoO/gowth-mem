"""v4.9 §3.3: `_prune.py` writes only the files it changed, keeps their trailing
newline, holds the `entries-<ws>` lock per file, and never reads the gap ledger.

Before: `prune_file` rewrote EVERY file it scanned — `"\\n".join(splitlines())`
dropped the trailing newline, so `new_text != text` was always true — giving each
clean topic file new bytes, a new mtime and a diff in the next sync, on every
journal cadence. All cases run through the real entry point (`_prune.py
--all-workspaces`, the Stop hook's call) with a positive control.
"""
from __future__ import annotations

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

SCRUB = ("AI_AGENT", "GOWTH_WORKSPACE", "CLAUDE_SUBAGENT", "GOWTH_MEM_OFF", "CLAUDE_CODE_ENTRYPOINT")
CLEAN = ("---\nslug: t-clean\n---\n"
         "- [exp] wombat burrows face north in the alpine survey data\n"
         "- [ref] numbat colonies nest under fallen logs. Source: field notes 2026\n")
DIRTY = ("---\nslug: t-dirty\n---\n"
         "- [exp] alpha finding about wombats (deprecated) replaced by beta\n"
         "- [exp] beta finding about wombats stands on its own evidence here\n")
OLD = 1_000_000_000          # a fixed past mtime (2001-09-09)


class PruneSafetyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gowth_prune_safety_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        ws = self.tmp / "workspaces" / "demo"
        (ws / "t").mkdir(parents=True)
        (ws / "docs").mkdir()
        (ws / "workspace.json").write_text(json.dumps({"name": "demo"}))
        (ws / "t" / "00-README.md").write_text("---\nslug: t\ntitle: T\n---\n# T\n")
        self.clean = ws / "t" / "2026-09-10-clean.md"
        self.clean.write_text(CLEAN)
        self.dirty = ws / "t" / "2026-09-10-dirty.md"
        self.dirty.write_text(DIRTY)
        self.ref = ws / "docs" / "ref.md"
        self.ref.write_text("# ref\n\n- [ref] heron-cache eviction is LRU. Source: heron docs v2.3\n")
        # a gap ledger with a line every prune rule would delete if it were scanned
        self.questions = ws / "docs" / "questions.md"
        self.questions.write_text(
            "# Open questions — demo\n\n## Open\n"
            "- Q-1a2b3c4d · ask · 2026-11-07 · Is the wombat survey still running this year?\n"
            "- [exp] a user note in the ledger (deprecated) that prune must never see\n"
            "- [exp] a user note in the ledger (deprecated) that prune must never see!\n"
            "## Closed\n")
        (self.tmp / "settings.json").write_text(json.dumps({"layout_version": 3}))
        (self.tmp / "config.json").write_text(json.dumps({"active_workspace": "demo"}))
        for p in (self.clean, self.dirty, self.ref, self.questions):
            os.utime(p, (OLD, OLD))
        patcher = mock.patch.dict(os.environ, {"GOWTH_MEM_HOME": str(self.tmp)})
        patcher.start()
        self.addCleanup(patcher.stop)
        for k in SCRUB:
            os.environ.pop(k, None)
        self.env = dict(os.environ)

    def prune(self) -> subprocess.CompletedProcess:
        r = subprocess.run([sys.executable, str(SCRIPTS / "_prune.py"), "--all-workspaces"],
                           capture_output=True, text=True, env=self.env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        return r

    @staticmethod
    def state(p: Path) -> tuple:
        return p.read_bytes(), os.stat(p).st_mtime_ns

    def test_unchanged_files_keep_bytes_and_mtime(self):
        before = {p: self.state(p) for p in (self.clean, self.ref, self.questions)}
        r = self.prune()
        for p, st in before.items():
            self.assertEqual(self.state(p), st, f"{p.name} was rewritten without a deletion")
        # positive control: the deprecated entry IS pruned, the file keeps its newline
        self.assertIn("2026-09-10-dirty.md", r.stdout)
        text = self.dirty.read_text()
        self.assertNotIn("alpha finding", text)
        self.assertIn("beta finding", text)
        self.assertTrue(text.endswith("here\n"), repr(text[-20:]))
        self.assertNotEqual(os.stat(self.dirty).st_mtime_ns, OLD * 10**9)

    def test_three_passes_are_byte_identical_after_the_first(self):
        self.prune()
        first = self.state(self.dirty)
        for _ in range(2):
            self.prune()
            self.assertEqual(self.state(self.dirty), first)
        self.assertEqual(self.dirty.read_text(), DIRTY.replace(
            "- [exp] alpha finding about wombats (deprecated) replaced by beta\n", ""))

    def test_no_trailing_newline_is_not_invented(self):
        self.dirty.write_text(DIRTY.rstrip("\n"))
        self.prune()
        self.assertEqual(self.dirty.read_text(), DIRTY.replace(
            "- [exp] alpha finding about wombats (deprecated) replaced by beta\n", "").rstrip("\n"))

    def test_gap_ledger_is_never_collected(self):
        import _prune  # type: ignore
        files = _prune.collect_files("demo")
        self.assertNotIn(self.questions, files)
        self.assertIn(self.ref, files, "positive control: other docs/*.md are still pruned")
        before = self.state(self.questions)
        self.prune()
        self.assertEqual(self.state(self.questions), before)
        self.assertEqual(self.questions.read_text().count("(deprecated)"), 2)

    def test_entries_lock_held_skips_the_file(self):
        from _lock import file_lock  # type: ignore
        before = self.state(self.dirty)
        with file_lock("entries-demo", timeout=5):
            t0 = time.time()
            r = self.prune()
            waited = time.time() - t0
        self.assertEqual(self.state(self.dirty), before, "pruned while another writer held the lock")
        self.assertIn("skipped", r.stdout)
        self.assertIn("entries lock held", r.stdout)
        self.assertLess(waited, 30, "one 5 s wait, then the rest of the workspace is tried once")
        # released: the next prune does the work
        self.prune()
        self.assertNotIn("alpha finding", self.dirty.read_text())

    def test_prune_file_reports_a_skip_in_process(self):
        import _prune  # type: ignore
        from _lock import file_lock  # type: ignore
        skipped: list = []
        with file_lock("entries-demo", timeout=5):
            got = _prune.prune_file(self.dirty, False, "2026-11-07", ws="demo", lock_timeout=0.0,
                                    skipped=skipped)
        self.assertEqual(got, (0, 0))
        self.assertEqual(skipped, [self.dirty])
        self.assertIn("alpha finding", self.dirty.read_text())
        self.assertEqual(_prune.prune_file(self.dirty, False, "2026-11-07", ws="demo"), (1, 1))

    def test_dry_run_takes_no_lock_and_writes_nothing(self):
        import _prune  # type: ignore
        from _lock import file_lock  # type: ignore
        before = self.state(self.dirty)
        with file_lock("entries-demo", timeout=5):
            self.assertEqual(_prune.prune_file(self.dirty, True, "2026-11-07", ws="demo"), (1, 1))
        self.assertEqual(self.state(self.dirty), before)


if __name__ == "__main__":
    unittest.main()
