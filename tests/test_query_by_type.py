"""Tests for _query.py v3.4 query_by_type API.

Covers:
  - query_by_type with tag filter returns only matching tag rows
  - query_by_type with BM25 query ranks results
  - query_by_type with empty tag = no filter (returns all tags)
  - query_by_type returns empty list when DB absent
  - query_by_type returns empty list when DB is pre-migration (no tag column)
  - workspace filter (_path_in_ws helper)
  - CLI __main__ smoke-test
"""
import hashlib
import importlib.util
import os
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


def _make_db(tmp_dir: str) -> sqlite3.Connection:
    """Create a fully-migrated index.db in tmp_dir and return open connection."""
    os.environ["GOWTH_MEM_HOME"] = tmp_dir
    idx = load_module("gowth_idx_qbt", SCRIPTS / "_index.py")
    db_path = Path(tmp_dir) / "index.db"
    db = sqlite3.connect(str(db_path))
    db.execute("PRAGMA journal_mode=WAL")
    idx._ensure_schema(db, sample_dim=0, use_vec=False)
    db.commit()
    return db


def _insert(db: sqlite3.Connection, path: str, content: str, tag: str) -> None:
    h = hashlib.sha1(content.encode()).hexdigest()[:16]
    db.execute(
        "INSERT INTO chunks (path, heading, content, mtime, hash, tag) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (path, "", content, 1.0, h, tag),
    )
    cid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute(
        "INSERT INTO chunks_fts(rowid, tag, content) VALUES (?, ?, ?)",
        (cid, tag, content),
    )
    db.commit()


class QueryByTypeFilterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_qbt_")
        self.db = _make_db(self.tmp)
        self.qmod = load_module("gowth_query_filter", SCRIPTS / "_query.py")

        # Seed: 2 decision, 1 exp, 1 ref chunks across 2 workspaces.
        _insert(self.db,
                "workspaces/ws1/misc/2026-05-18-note.md",
                "[decision] use atomic writes for all file operations",
                "decision")
        _insert(self.db,
                "workspaces/ws1/misc/2026-05-18-note2.md",
                "[decision] prefer sqlite wal mode for concurrent access",
                "decision")
        _insert(self.db,
                "workspaces/ws1/misc/2026-05-18-exp.md",
                "[exp] lesson about overfit backtests causing live losses",
                "exp")
        _insert(self.db,
                "workspaces/ws2/misc/2026-05-18-ref.md",
                "[ref] gold futures average daily range is 15-20 dollars",
                "ref")

    def tearDown(self):
        self.db.close()
        os.environ.pop("GOWTH_MEM_HOME", None)

    # ------------------------------------------------------------------
    def test_filter_decision_returns_only_decision_rows(self):
        hits = self.qmod.query_by_type(ws="ws1", tag="decision")
        self.assertTrue(len(hits) >= 2, f"expected >=2, got {len(hits)}: {hits}")
        for h in hits:
            self.assertEqual(h["tag"], "decision", f"unexpected tag in hit: {h}")

    def test_filter_exp_returns_only_exp_rows(self):
        hits = self.qmod.query_by_type(ws="ws1", tag="exp")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["tag"], "exp")
        self.assertIn("backtest", hits[0]["content"])

    def test_filter_ref_cross_workspace(self):
        # ref is in ws2; query with ws="" (all workspaces) should find it.
        hits = self.qmod.query_by_type(ws="", tag="ref")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["tag"], "ref")

    def test_empty_tag_returns_all_tags(self):
        # tag="" means no tag filter — all 4 chunks should be reachable.
        hits = self.qmod.query_by_type(ws="", tag="", limit=10)
        self.assertGreaterEqual(len(hits), 4,
                                f"expected >=4 hits with no tag filter, got {len(hits)}")

    def test_nonexistent_tag_returns_empty(self):
        hits = self.qmod.query_by_type(ws="ws1", tag="skill-ref")
        self.assertEqual(hits, [])

    def test_limit_respected(self):
        hits = self.qmod.query_by_type(ws="ws1", tag="decision", limit=1)
        self.assertEqual(len(hits), 1)

    def test_result_has_required_keys(self):
        hits = self.qmod.query_by_type(ws="ws1", tag="decision", limit=1)
        self.assertTrue(hits)
        h = hits[0]
        for key in ("path", "line_no", "content", "tag", "bm25_score"):
            self.assertIn(key, h, f"missing key {key!r} in hit dict")


class QueryByTypeBM25Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_qbt_bm25_")
        self.db = _make_db(self.tmp)
        self.qmod = load_module("gowth_query_bm25", SCRIPTS / "_query.py")

        _insert(self.db,
                "workspaces/ws1/misc/2026-05-18-a.md",
                "[decision] use stop loss always before entry",
                "decision")
        _insert(self.db,
                "workspaces/ws1/misc/2026-05-18-b.md",
                "[decision] never skip position sizing calculation",
                "decision")
        _insert(self.db,
                "workspaces/ws1/misc/2026-05-18-c.md",
                "[exp] stop loss triggered on false breakout",
                "exp")

    def tearDown(self):
        self.db.close()
        os.environ.pop("GOWTH_MEM_HOME", None)

    def test_bm25_query_filters_by_tag(self):
        hits = self.qmod.query_by_type(ws="ws1", tag="decision", query="stop loss")
        # Must return only decision-tagged rows matching "stop loss".
        self.assertTrue(hits, "expected at least one hit for 'stop loss' in decision")
        for h in hits:
            self.assertEqual(h["tag"], "decision")

    def test_bm25_query_empty_tag_returns_cross_tag(self):
        # No tag filter: "stop loss" matches both decision and exp rows.
        hits = self.qmod.query_by_type(ws="ws1", tag="", query="stop loss")
        tags_found = {h["tag"] for h in hits}
        self.assertIn("decision", tags_found)
        self.assertIn("exp", tags_found)

    def test_bm25_score_present(self):
        hits = self.qmod.query_by_type(ws="ws1", tag="decision", query="stop")
        self.assertTrue(hits)
        # FTS5 bm25 score is non-zero (negative float for relevance).
        self.assertNotEqual(hits[0]["bm25_score"], 0.0)


class QueryByTypeEdgeCasesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_qbt_edge_")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        self.qmod = load_module("gowth_query_edge", SCRIPTS / "_query.py")

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)

    def test_returns_empty_when_db_absent(self):
        result = self.qmod.query_by_type(ws="ws1", tag="decision")
        self.assertEqual(result, [])

    def test_returns_empty_when_db_pre_migration(self):
        db_path = Path(self.tmp) / "index.db"
        db = sqlite3.connect(str(db_path))
        db.executescript("""
            CREATE TABLE chunks (
                id INTEGER PRIMARY KEY,
                path TEXT NOT NULL,
                heading TEXT,
                content TEXT NOT NULL,
                mtime REAL NOT NULL,
                hash TEXT NOT NULL
            );
        """)
        db.commit()
        db.close()
        result = self.qmod.query_by_type(ws="ws1", tag="decision")
        self.assertEqual(result, [])


class PathInWsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_pws_")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        self.qmod = load_module("gowth_query_pws", SCRIPTS / "_query.py")

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)

    def test_workspaces_path_matched(self):
        self.assertTrue(
            self.qmod._path_in_ws("workspaces/myws/misc/note.md", "myws"))

    def test_different_ws_not_matched(self):
        self.assertFalse(
            self.qmod._path_in_ws("workspaces/otherws/misc/note.md", "myws"))

    def test_shared_matched(self):
        self.assertTrue(
            self.qmod._path_in_ws("shared/AGENTS.md", "shared"))

    def test_shared_not_matched_for_non_shared_ws(self):
        self.assertFalse(
            self.qmod._path_in_ws("shared/AGENTS.md", "myws"))

    def test_empty_ws_always_true(self):
        # ws="" means all workspaces — _path_in_ws should return True.
        self.assertTrue(
            self.qmod._path_in_ws("workspaces/myws/note.md", ""))


class QueryCLISmokeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_qbt_cli_")
        os.environ["GOWTH_MEM_HOME"] = self.tmp

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)

    def test_cli_no_db_prints_no_results(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "_query.py"),
             "--ws", "ws1", "--type", "decision"],
            env={**os.environ, "GOWTH_MEM_HOME": self.tmp},
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("no results", result.stdout)

    def test_cli_compiles(self):
        subprocess.run(
            [sys.executable, "-m", "py_compile", str(SCRIPTS / "_query.py")],
            check=True,
        )


if __name__ == "__main__":
    unittest.main()
