"""Tests for v4.3 retrieval repairs (Zero-Mem audit findings D1-D4).

D1/D2  natural-language + punctuation queries must return hits, not silence
D3     `## [decision] …` block headings must populate the `tag` column
D4     the workspace filter must run in SQL, BEFORE the LIMIT
plus    recall rows must carry `heading` (it holds the `[type] title` anchor)
        and errors must be distinguishable from "no results" (query_ex).
"""
import hashlib
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


class TestTagFromHeading(unittest.TestCase):
    """D3: split_chunks lifts `## [decision] Title` into `heading`, so
    _extract_tag must consult the heading — otherwise the 5.0 BM25 tag weight
    and the --type filter apply to almost nothing (live: 54 of 15,145 rows)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        self.idx = load_module("gowth_idx_v43", SCRIPTS / "_index.py")

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _index_file(self, rel: str, text: str) -> sqlite3.Connection:
        p = Path(self.tmp) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        # list_workspaces() only sees folders carrying workspace.json
        ws_root = Path(self.tmp) / "workspaces" / rel.split("/")[1]
        (ws_root / "workspace.json").write_text('{"name": "demo"}')
        r = subprocess.run([sys.executable, str(SCRIPTS / "_index.py"), "--full"],
                           capture_output=True, text=True,
                           env={**os.environ, "GOWTH_MEM_HOME": self.tmp})
        self.assertEqual(r.returncode, 0, r.stderr)
        return sqlite3.connect(str(Path(self.tmp) / "index.db"))

    def test_block_heading_tag_is_extracted(self):
        db = self._index_file(
            "workspaces/demo/topicx/2026-08-05-thing.md",
            "---\nslug: topicx\n---\n\n"
            "## [decision] Use FTS5 over vector search\n\n"
            "Rationale: no pip deps in the runtime path.\n",
        )
        tags = [r[0] for r in db.execute("SELECT tag FROM chunks WHERE tag<>''")]
        self.assertIn("decision", tags)

    def test_bullet_tag_still_extracted(self):
        """Regression guard: a chunk whose BODY starts with [type] must keep working."""
        db = self._index_file(
            "workspaces/demo/topicy/2026-08-05-thing.md",
            "---\nslug: topicy\n---\n\n## Lessons\n\n"
            "[exp] Something broke. Root cause: stale index.\n",
        )
        tags = [r[0] for r in db.execute("SELECT tag FROM chunks WHERE tag<>''")]
        self.assertIn("exp", tags)

    def test_heading_tag_is_searchable_via_type_filter(self):
        self._index_file(
            "workspaces/demo/topicz/2026-08-05-thing.md",
            "---\nslug: topicz\n---\n\n"
            "## [decision] Adopt closure over graph propagation\n\n"
            "Rationale: measured MRR did not improve with the graph view.\n",
        )
        q = load_module("gowth_q_v43a", SCRIPTS / "_query.py")
        hits = q.query_by_type(ws="demo", tag="decision", query="closure")
        self.assertTrue(hits, "a [decision] heading must be findable by --type decision")


class TestQueryRows(unittest.TestCase):
    """Recall rows must expose `heading` — it carries the `[type] title` and
    `turn N — HH:MM` anchors, which are otherwise unreachable through FTS5."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        idx = load_module("gowth_idx_v43b", SCRIPTS / "_index.py")
        self.db = sqlite3.connect(str(Path(self.tmp) / "index.db"))
        idx._ensure_schema(self.db, sample_dim=0, use_vec=False)
        self.db.commit()
        self.q = load_module("gowth_q_v43b", SCRIPTS / "_query.py")

    def tearDown(self):
        self.db.close()
        os.environ.pop("GOWTH_MEM_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _insert(self, path, heading, content, tag="", keywords=""):
        h = hashlib.sha1(content.encode()).hexdigest()[:16]
        cid = self.db.execute(
            "INSERT INTO chunks (path, heading, content, mtime, hash, tag, keywords) "
            "VALUES (?,?,?,?,?,?,?)",
            (path, heading, content, 1.0, h, tag, keywords),
        ).lastrowid
        self.db.execute(
            "INSERT INTO chunks_fts(rowid, tag, keywords, content) VALUES (?,?,?,?)",
            (cid, tag, keywords, content),
        )
        self.db.commit()

    def test_rows_include_heading(self):
        self._insert("workspaces/w1/t/2026-08-05-a.md",
                     "[decision] Pick FTS5", "Rationale: stdlib only.", "decision")
        hits = self.q.query_by_type(ws="w1", tag="", query="stdlib")
        self.assertTrue(hits)
        self.assertIn("heading", hits[0])
        self.assertEqual(hits[0]["heading"], "[decision] Pick FTS5")

    def test_workspace_filter_applied_before_limit(self):
        """D4: 30 noise rows in w2 + 1 real row in w1. With limit=5 the SQL must
        already exclude w2, otherwise Python post-filtering returns nothing."""
        for i in range(30):
            self._insert(f"workspaces/w2/t/2026-08-05-{i}.md", "", "python noise row")
        self._insert("workspaces/w1/t/2026-08-05-real.md", "", "python signal row")
        hits = self.q.query_by_type(ws="w1", tag="", query="python", limit=5)
        self.assertTrue(hits, "workspace filter must run in SQL, before LIMIT")
        self.assertTrue(all(h["path"].startswith("workspaces/w1/") for h in hits))

    def test_shared_workspace_filter(self):
        self._insert("shared/research/x.md", "", "shared python note")
        self._insert("workspaces/w2/t/2026-08-05-b.md", "", "workspace python note")
        hits = self.q.query_by_type(ws="shared", tag="", query="python")
        self.assertTrue(hits)
        self.assertTrue(all(h["path"].startswith("shared/") for h in hits))

    def test_natural_language_query_returns_hits(self):
        """D1: bare multi-word text is implicit-AND in FTS5 and returned 0 rows."""
        self._insert("workspaces/w1/t/2026-08-05-c.md", "",
                     "We dropped the vector recall path in v3.2 because of token cost.")
        hits = self.q.query_by_type(ws="w1", tag="",
                                    query="why did we drop vector recall")
        self.assertTrue(hits, "natural-language query must not return zero hits")

    def test_punctuation_query_does_not_silently_fail(self):
        """D2: hyphens/colons raised OperationalError, swallowed into '(no results)'."""
        self._insert("workspaces/w1/t/2026-08-05-d.md", "",
                     "The stop-loss fills intrabar when price touches it.")
        for q in ("stop-loss", "forget: ttl", "OOM (fatal)", "a AND", "NEAR("):
            res = self.q.query_ex(ws="w1", tag="", query=q)
            self.assertIsNone(res["error"], f"{q!r} must not error: {res['error']}")
        self.assertTrue(self.q.query_by_type(ws="w1", tag="", query="stop-loss"))

    def test_query_ex_reports_hits_and_error_keys(self):
        res = self.q.query_ex(ws="w1", tag="", query="anything")
        self.assertIn("hits", res)
        self.assertIn("error", res)
        self.assertIsInstance(res["hits"], list)

    def test_query_ex_flags_unsearchable_query(self):
        """A stopword-only query is 'nothing searchable', not 'no results'."""
        res = self.q.query_ex(ws="w1", tag="", query="the and of")
        self.assertEqual(res["hits"], [])
        self.assertIsNotNone(res["error"])

    def test_query_by_type_still_returns_list_of_dicts(self):
        """Public contract: 21 existing tests assert list[dict]; do not break it."""
        out = self.q.query_by_type(ws="w1", tag="", query="anything")
        self.assertIsInstance(out, list)


if __name__ == "__main__":
    unittest.main()
