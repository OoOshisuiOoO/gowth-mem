"""Tests for archive indexing + orphan sweep (v4.3, audit D6/D7).

D7: `_forget.py` gzip-archives raw journal into `.archive/`, but nothing indexed it,
so archived memory was unfindable — recoverable only by manually gunzipping or
digging through git history. Forgetting was supposed to mean "out of the bootstrap",
not "permanently unsearchable".

D6: 4,031 chunk rows (27% of the live index) pointed at 237 files that no longer
exist. Recall cited files the user could not open, and the dead rows polluted BM25
statistics. `_index.py` only GC'd vanished paths under `--full`.

Order matters: archives must be indexed BEFORE the sweep runs, because until then
index.db is the ONLY searchable copy of archived content. The sweep therefore
classifies each orphan by recoverability and refuses to delete unrecoverable rows
unless forced.
"""
import gzip
import importlib.util
import os
import shutil
import sqlite3
import subprocess
import sys
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


class ArchiveIndexTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_arch_")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        self.home = Path(self.tmp)
        wsd = self.home / "workspaces" / "demo"
        (wsd / "t").mkdir(parents=True, exist_ok=True)
        (wsd / "workspace.json").write_text('{"name": "demo"}')
        (wsd / "t" / "2026-08-05-live.md").write_text(
            "---\nslug: t\n---\n\n## [ref] Live entry\n\nSource: live-source\n")
        arch = self.home / ".archive" / "journal" / "demo"
        arch.mkdir(parents=True, exist_ok=True)
        with gzip.open(arch / "2026-06-01-1780000000.md.gz", "wt") as fh:
            fh.write("# 2026-06-01\n\n## [exp] Archived lesson about zebrafinch tuning\n\n"
                     "Root cause: forgotten but not lost.\n")
        self.idx = load_module("gowth_idx_arch", SCRIPTS / "_index.py")
        self.q = load_module("gowth_q_arch", SCRIPTS / "_query.py")

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _reindex(self):
        r = subprocess.run([sys.executable, str(SCRIPTS / "_index.py"), "--full"],
                           capture_output=True, text=True,
                           env={**os.environ, "GOWTH_MEM_HOME": self.tmp})
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_archived_gz_is_indexed(self):
        self._reindex()
        db = sqlite3.connect(str(self.home / "index.db"))
        try:
            paths = [r[0] for r in db.execute(
                "SELECT DISTINCT path FROM chunks WHERE path LIKE '.archive/%'")]
        finally:
            db.close()
        self.assertTrue(paths, "archived .gz must be indexed")
        self.assertTrue(paths[0].endswith(".md.gz"))

    def test_archived_content_is_searchable_when_requested(self):
        self._reindex()
        hits = self.q.query_by_type(ws="*", tag="", query="zebrafinch",
                                    include_archive=True)
        self.assertTrue(hits, "archived content must be findable with include_archive")
        self.assertTrue(any(h["path"].startswith(".archive/") for h in hits))

    def test_archive_excluded_from_recall_by_default(self):
        """Archived raw transcript must not pollute normal recall."""
        self._reindex()
        hits = self.q.query_by_type(ws="*", tag="", query="zebrafinch")
        self.assertEqual([h for h in hits if h["path"].startswith(".archive/")], [])

    def test_live_content_still_found_by_default(self):
        self._reindex()
        hits = self.q.query_by_type(ws="*", tag="", query="live-source")
        self.assertTrue(hits)

    def test_corrupt_gz_does_not_break_indexing(self):
        bad = self.home / ".archive" / "journal" / "demo" / "broken.md.gz"
        bad.write_bytes(b"this is not gzip")
        out = self._reindex()
        self.assertIn("indexed:", out)
        hits = self.q.query_by_type(ws="*", tag="", query="live-source")
        self.assertTrue(hits, "a corrupt archive must not stop the indexer")


class SweepOrphansTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_sweep_")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        self.home = Path(self.tmp)
        wsd = self.home / "workspaces" / "demo"
        (wsd / "journal").mkdir(parents=True, exist_ok=True)
        (wsd / "workspace.json").write_text('{"name": "demo"}')
        self.idx = load_module("gowth_idx_sweep", SCRIPTS / "_index.py")
        db = sqlite3.connect(str(self.home / "index.db"))
        self.idx._ensure_schema(db, sample_dim=0, use_vec=False)
        db.commit()
        db.close()

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, rel, text="## [ref] X\n\nSource: y\n"):
        p = self.home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        self.idx.reindex_paths([p])
        return p

    def _rows(self, rel=None):
        db = sqlite3.connect(str(self.home / "index.db"))
        try:
            if rel:
                return db.execute("SELECT count(*) FROM chunks WHERE path=?",
                                  (rel,)).fetchone()[0]
            return db.execute("SELECT count(*) FROM chunks").fetchone()[0]
        finally:
            db.close()

    def test_dry_run_deletes_nothing(self):
        rel = "workspaces/demo/journal/2026-06-01.md"
        p = self._seed(rel)
        p.unlink()
        before = self._rows()
        res = self.idx.sweep_orphans(apply=False)
        self.assertEqual(self._rows(), before, "dry-run must not delete")
        self.assertEqual(res["orphan_paths"], 1)

    def test_apply_removes_rows_for_archived_file(self):
        rel = "workspaces/demo/journal/2026-06-02.md"
        p = self._seed(rel)
        arch = self.home / ".archive" / "journal" / "demo"
        arch.mkdir(parents=True, exist_ok=True)
        with gzip.open(arch / "2026-06-02-1780000001.md.gz", "wt") as fh:
            fh.write("archived copy\n")
        p.unlink()
        res = self.idx.sweep_orphans(apply=True)
        self.assertEqual(self._rows(rel), 0)
        self.assertEqual(res["deleted_paths"], 1)

    def test_unrecoverable_orphan_is_kept_without_force(self):
        """No archive copy and no git history = index.db is the last copy. Keep it."""
        rel = "workspaces/demo/journal/2026-06-03.md"
        p = self._seed(rel)
        p.unlink()
        res = self.idx.sweep_orphans(apply=True)
        self.assertEqual(self._rows(rel), 1, "must not delete the last searchable copy")
        self.assertEqual(res["unrecoverable"], 1)
        self.assertEqual(res["deleted_paths"], 0)

    def test_force_deletes_unrecoverable(self):
        rel = "workspaces/demo/journal/2026-06-04.md"
        p = self._seed(rel)
        p.unlink()
        self.idx.sweep_orphans(apply=True, force=True)
        self.assertEqual(self._rows(rel), 0)

    def test_existing_files_are_never_touched(self):
        rel = "workspaces/demo/journal/2026-06-05.md"
        self._seed(rel)
        self.idx.sweep_orphans(apply=True, force=True)
        self.assertGreater(self._rows(rel), 0)

    def test_archive_rows_are_not_treated_as_orphans(self):
        """Archive rows point at .gz files that DO exist; they must be left alone."""
        arch = self.home / ".archive" / "journal" / "demo"
        arch.mkdir(parents=True, exist_ok=True)
        gz = arch / "2026-06-06-1780000002.md.gz"
        with gzip.open(gz, "wt") as fh:
            fh.write("archived\n")
        self.idx.reindex_paths([gz])
        res = self.idx.sweep_orphans(apply=True, force=True)
        self.assertEqual(res["orphan_paths"], 0)

    def test_fts_consistent_after_sweep(self):
        rel = "workspaces/demo/journal/2026-06-07.md"
        p = self._seed(rel, "## [ref] A\n\nSource: a\n\n## [tool] B\n\nversion 1.0.0\n")
        p.unlink()
        self.idx.sweep_orphans(apply=True, force=True)
        db = sqlite3.connect(str(self.home / "index.db"))
        try:
            self.assertEqual(db.execute("SELECT count(*) FROM chunks").fetchone()[0],
                             db.execute("SELECT count(*) FROM chunks_fts").fetchone()[0])
            db.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH ?",
                       ('"B"',)).fetchone()
        finally:
            db.close()

    def test_never_raises_without_db(self):
        (self.home / "index.db").unlink()
        res = self.idx.sweep_orphans(apply=True)
        self.assertEqual(res["orphan_paths"], 0)


if __name__ == "__main__":
    unittest.main()


class ArchiveWorkspaceFilterTest(unittest.TestCase):
    """`--ws <name> --archive` must work: archive paths are `.archive/<kind>/<ws>/…`,
    so the live `workspaces/<ws>/%` prefix would match nothing and silently return
    zero rows — the same silent-empty class as D1/D2/D4."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_archws_")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        self.home = Path(self.tmp)
        idx = load_module("gowth_idx_archws", SCRIPTS / "_index.py")
        db = sqlite3.connect(str(self.home / "index.db"))
        idx._ensure_schema(db, sample_dim=0, use_vec=False)
        db.commit()
        db.close()
        for ws, word in (("trade", "goldpivot"), ("devops", "clusterpivot")):
            d = self.home / ".archive" / "journal" / ws
            d.mkdir(parents=True, exist_ok=True)
            gz = d / f"2026-06-01-178000000{len(ws)}.md.gz"
            with gzip.open(gz, "wt") as fh:
                fh.write(f"# archived\n\n[exp] {word} lesson\n")
            idx.reindex_paths([gz])
        self.q = load_module("gowth_q_archws", SCRIPTS / "_query.py")

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_workspace_scoped_archive_search_finds_its_own(self):
        hits = self.q.query_by_type(ws="trade", tag="", query="goldpivot",
                                    include_archive=True)
        self.assertTrue(hits, "--ws trade --archive must find trade's archive")
        self.assertIn("/trade/", hits[0]["path"])

    def test_workspace_scoped_archive_search_excludes_others(self):
        hits = self.q.query_by_type(ws="trade", tag="", query="clusterpivot",
                                    include_archive=True)
        self.assertEqual(hits, [], "must not leak another workspace's archive")


class ArchiveSettingTest(unittest.TestCase):
    """`retrieval.index_archive` must actually be read — a documented setting that
    does nothing is worse than no setting."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_archset_")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        self.home = Path(self.tmp)
        wsd = self.home / "workspaces" / "demo"
        wsd.mkdir(parents=True, exist_ok=True)
        (wsd / "workspace.json").write_text('{"name": "demo"}')
        d = self.home / ".archive" / "journal" / "demo"
        d.mkdir(parents=True, exist_ok=True)
        with gzip.open(d / "2026-06-01-1780000009.md.gz", "wt") as fh:
            fh.write("# a\n\n[exp] settingprobe lesson\n")

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self):
        r = subprocess.run([sys.executable, str(SCRIPTS / "_index.py"), "--full"],
                           capture_output=True, text=True,
                           env={**os.environ, "GOWTH_MEM_HOME": self.tmp})
        self.assertEqual(r.returncode, 0, r.stderr)
        db = sqlite3.connect(str(self.home / "index.db"))
        try:
            return db.execute(
                "SELECT count(*) FROM chunks WHERE path LIKE '.archive/%'").fetchone()[0]
        finally:
            db.close()

    def test_enabled_by_default(self):
        self.assertGreater(self._run(), 0)

    def test_setting_false_skips_archive(self):
        (self.home / "settings.json").write_text(
            '{"retrieval": {"index_archive": false}}')
        self.assertEqual(self._run(), 0)
