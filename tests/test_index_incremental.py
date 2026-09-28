"""v4.8 Task 6: `_index.incremental()` keeps index.db fresh from the Stop hook.

Audit: 275 files were newer than their index rows and 161 unindexed — only
routed writes reindexed, nothing swept. Incremental indexing walks the sources
(≈2,000 stats), re-indexes files whose mtime differs from the stored row or
that have no row, drops rows of deleted files, at most `max_files` per run.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "hooks" / "scripts"
sys.path.insert(0, str(SCRIPTS))


class IncrementalIndexTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_incr_")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        self.home = Path(self.tmp)
        self.ws = "demo"
        self.wsd = self.home / "workspaces" / self.ws
        (self.wsd / "t").mkdir(parents=True)
        (self.wsd / "docs").mkdir()
        (self.wsd / "workspace.json").write_text('{"name": "demo"}')
        (self.wsd / "t" / "00-README.md").write_text("---\nslug: t\ntitle: T\n---\n# T\n")
        (self.wsd / "t" / "2026-09-10-a.md").write_text("---\nslug: t-a\n---\n- [exp] first entry about pelicans here\n")
        self.env = {**os.environ, "GOWTH_MEM_HOME": self.tmp}
        r = subprocess.run([sys.executable, str(SCRIPTS / "_index.py")], capture_output=True, text=True, env=self.env)
        assert r.returncode == 0, r.stderr
        import _index  # type: ignore
        self.ix = _index

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _rows(self, like: str) -> int:
        db = sqlite3.connect(str(self.home / "index.db"))
        try:
            return db.execute("SELECT count(*) FROM chunks WHERE path LIKE ?", (f"%{like}%",)).fetchone()[0]
        finally:
            db.close()

    def _content_rows(self, term: str) -> int:
        db = sqlite3.connect(str(self.home / "index.db"))
        try:
            return db.execute("SELECT count(*) FROM chunks WHERE content LIKE ?", (f"%{term}%",)).fetchone()[0]
        finally:
            db.close()

    def test_new_file_is_indexed(self):
        (self.wsd / "t" / "2026-09-11-b.md").write_text("---\nslug: t-b\n---\n- [exp] second entry about herons here\n")
        rep = self.ix.incremental()
        self.assertEqual(rep["files"], 1)
        self.assertGreaterEqual(self._rows("2026-09-11-b.md"), 1)

    def test_modified_file_is_reindexed(self):
        f = self.wsd / "t" / "2026-09-10-a.md"
        f.write_text("---\nslug: t-a\n---\n- [exp] first entry about flamingos now\n")
        fut = time.time() + 5
        os.utime(f, (fut, fut))
        rep = self.ix.incremental()
        self.assertEqual(rep["files"], 1)
        self.assertEqual(self._content_rows("flamingos"), 1)
        self.assertEqual(self._content_rows("pelicans"), 0)

    def test_unchanged_index_is_a_noop(self):
        rep = self.ix.incremental()
        self.assertEqual(rep["files"], 0)
        self.assertEqual(rep["dropped"], 0)

    def test_cap_of_200_files_per_run(self):
        for i in range(201):
            (self.wsd / "t" / f"2026-08-{(i % 28) + 1:02d}-f{i:03d}.md").write_text(
                f"---\nslug: t-f{i}\n---\n- [exp] bulk entry number {i} for the cap test\n")
        rep = self.ix.incremental()
        self.assertEqual(rep["files"], 200)
        rep2 = self.ix.incremental()
        self.assertEqual(rep2["files"], 1)

    def test_deleted_file_rows_are_dropped(self):
        (self.wsd / "t" / "2026-09-10-a.md").unlink()
        rep = self.ix.incremental()
        self.assertEqual(rep["dropped"], 1)
        self.assertEqual(self._rows("2026-09-10-a.md"), 0)

    def test_workspace_without_workspace_json_is_indexed(self):
        other = self.home / "workspaces" / "loose"
        (other / "docs").mkdir(parents=True)
        (other / "docs" / "ref.md").write_text("# ref\n\n- [ref] loose workspace entry about ospreys. Source: t\n")
        rep = self.ix.incremental()
        self.assertGreaterEqual(rep["files"], 1)
        self.assertGreaterEqual(self._rows("workspaces/loose/docs/ref.md"), 1)

    def test_missing_index_is_a_noop(self):
        (self.home / "index.db").unlink()
        rep = self.ix.incremental()
        self.assertEqual(rep["files"], 0)
        self.assertFalse((self.home / "index.db").exists(), "incremental must never create index.db")

    def test_cli_flag_prints_summary(self):
        (self.wsd / "t" / "2026-09-12-c.md").write_text("---\nslug: t-c\n---\n- [exp] third entry about cranes here\n")
        r = subprocess.run([sys.executable, str(SCRIPTS / "_index.py"), "--incremental"],
                           capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("incremental: 1 files, 0 dropped", r.stdout)


if __name__ == "__main__":
    unittest.main()
