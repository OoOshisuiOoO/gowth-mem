"""Tests for _index.reindex_paths() — write-time index freshness (v4.3, audit D5).

Defect: _topic.append_entry() / _lesson.append_lesson() wrote the file and never
touched index.db, so a just-captured memory was NOT recallable until someone
remembered to run /mem-reindex. The live vault's index.db was 5 days stale.

reindex_paths() refreshes only the files just written — incremental, lock-guarded,
and best-effort (it runs on the Stop-hook path, which must never raise).

Deletion order matters: chunks_fts is an FTS5 external-content table
(content='chunks'), so its rowids must be deleted BEFORE the chunks rows they
mirror, otherwise the FTS index is left referencing rows that no longer exist.
"""
import importlib.util
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "hooks" / "scripts"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class ReindexPathsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_fresh_")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        self.home = Path(self.tmp)
        self.wsd = self.home / "workspaces" / "demo"
        (self.wsd / "topicx").mkdir(parents=True, exist_ok=True)
        (self.wsd / "workspace.json").write_text('{"name": "demo"}')
        self.idx = load_module("gowth_idx_fresh", SCRIPTS / "_index.py")

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_db(self):
        db = sqlite3.connect(str(self.home / "index.db"))
        self.idx._ensure_schema(db, sample_dim=0, use_vec=False)
        db.commit()
        db.close()

    def _write(self, rel, text):
        p = self.home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def _rows(self, rel):
        db = sqlite3.connect(str(self.home / "index.db"))
        try:
            return db.execute("SELECT count(*) FROM chunks WHERE path=?", (rel,)).fetchone()[0]
        finally:
            db.close()

    def test_no_db_is_a_noop_not_a_crash(self):
        """A missing index must not be half-built from one file on a hook path."""
        p = self._write("workspaces/demo/topicx/2026-08-05-a.md", "## [ref] X\n\nSource: y\n")
        self.assertEqual(self.idx.reindex_paths([p]), 0)
        self.assertFalse((self.home / "index.db").exists())

    def test_new_file_becomes_indexed(self):
        self._make_db()
        rel = "workspaces/demo/topicx/2026-08-05-b.md"
        p = self._write(rel, "## [decision] Adopt closure\n\nRationale: measured.\n")
        n = self.idx.reindex_paths([p])
        self.assertGreater(n, 0)
        self.assertGreater(self._rows(rel), 0)

    def test_changed_file_is_replaced_not_duplicated(self):
        self._make_db()
        rel = "workspaces/demo/topicx/2026-08-05-c.md"
        p = self._write(rel, "## [ref] First\n\nSource: a\n")
        self.idx.reindex_paths([p])
        first = self._rows(rel)
        p.write_text("## [ref] First\n\nSource: a\n\n## [ref] Second\n\nSource: b\n")
        self.idx.reindex_paths([p])
        second = self._rows(rel)
        self.assertGreater(second, first)
        db = sqlite3.connect(str(self.home / "index.db"))
        try:
            contents = [r[0] for r in db.execute(
                "SELECT content FROM chunks WHERE path=?", (rel,))]
        finally:
            db.close()
        self.assertEqual(len(contents), len(set(contents)), "no duplicated chunk rows")

    def test_vanished_file_rows_are_removed(self):
        self._make_db()
        rel = "workspaces/demo/topicx/2026-08-05-d.md"
        p = self._write(rel, "## [ref] Gone\n\nSource: z\n")
        self.idx.reindex_paths([p])
        self.assertGreater(self._rows(rel), 0)
        p.unlink()
        self.idx.reindex_paths([p])
        self.assertEqual(self._rows(rel), 0)

    def test_fts_stays_consistent_with_chunks(self):
        """External-content FTS5: row counts must match after replace + delete."""
        self._make_db()
        rel = "workspaces/demo/topicx/2026-08-05-e.md"
        p = self._write(rel, "## [ref] One\n\nSource: a\n")
        self.idx.reindex_paths([p])
        p.write_text("## [ref] One\n\nSource: a\n\n## [tool] Two\n\nversion 1.2.3\n")
        self.idx.reindex_paths([p])
        db = sqlite3.connect(str(self.home / "index.db"))
        try:
            chunks = db.execute("SELECT count(*) FROM chunks").fetchone()[0]
            fts = db.execute("SELECT count(*) FROM chunks_fts").fetchone()[0]
            self.assertEqual(chunks, fts)
            # and the FTS index must actually be queryable
            db.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH ?",
                       ('"Two"',)).fetchone()
        finally:
            db.close()

    def test_never_raises_on_bad_input(self):
        self._make_db()
        for bad in ([Path("/nonexistent/nope.md")], [], [Path(self.tmp)]):
            self.idx.reindex_paths(bad)  # must not raise


class AppendEntryFreshnessTest(unittest.TestCase):
    """D5 end to end: a routed write must be immediately recallable."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_fresh2_")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        os.environ["GOWTH_WORKSPACE"] = "demo"
        self.home = Path(self.tmp)
        wsd = self.home / "workspaces" / "demo"
        wsd.mkdir(parents=True, exist_ok=True)
        (wsd / "workspace.json").write_text('{"name": "demo"}')
        (self.home / "settings.json").write_text('{"layout_version": 3}')
        idx = load_module("gowth_idx_fresh2", SCRIPTS / "_index.py")
        db = sqlite3.connect(str(self.home / "index.db"))
        idx._ensure_schema(db, sample_dim=0, use_vec=False)
        db.commit()
        db.close()

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        os.environ.pop("GOWTH_WORKSPACE", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_appended_entry_is_recallable_without_manual_reindex(self):
        topic = load_module("gowth_topic_fresh", SCRIPTS / "_topic.py")
        q = load_module("gowth_query_fresh", SCRIPTS / "_query.py")
        entry = ("[decision] Route recall through a safe FTS5 expression. "
                 "Rationale: bare terms are implicit-AND so questions returned nothing.")
        path, written = topic.append_entry(entry, ws="demo")
        self.assertTrue(written, "entry must pass the gate for this test to mean anything")
        hits = q.query_by_type(ws="demo", tag="", query="safe FTS5 expression")
        self.assertTrue(hits, "a just-written entry must be recallable immediately")


if __name__ == "__main__":
    unittest.main()
