"""Tests for _index.py v3.4 tag column migration.

Covers:
  - tag column added on fresh schema creation
  - migration idempotent on already-migrated DB
  - migration on a pre-existing DB without tag column (ALTER TABLE path)
  - chunk insertion populates tag correctly from [tag] prefix
  - unknown tag stored as empty string
  - tag with no leading marker stored as empty string
"""
import importlib.util
import os
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


class TagColumnMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_idx_tag_")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        self.idx = load_module("gowth_index", SCRIPTS / "_index.py")

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)

    def _open_db(self):
        db_path = Path(self.tmp) / "index.db"
        db = sqlite3.connect(str(db_path))
        db.execute("PRAGMA journal_mode=WAL")
        return db, db_path

    def _ensure_schema(self, db):
        self.idx._ensure_schema(db, sample_dim=0, use_vec=False)
        db.commit()

    # ------------------------------------------------------------------
    def test_tag_column_exists_after_fresh_schema(self):
        db, _ = self._open_db()
        self._ensure_schema(db)
        cols = {row[1] for row in db.execute("PRAGMA table_info(chunks)")}
        self.assertIn("tag", cols)
        db.close()

    def test_tag_index_exists(self):
        db, _ = self._open_db()
        self._ensure_schema(db)
        indexes = {row[1] for row in db.execute(
            "SELECT * FROM sqlite_master WHERE type='index'"
        )}
        self.assertTrue(any("tag" in idx for idx in indexes),
                        f"no tag index found in: {indexes}")
        db.close()

    def test_migration_idempotent(self):
        """Running _ensure_schema twice on the same DB must not raise."""
        db, _ = self._open_db()
        self._ensure_schema(db)
        # Second call — idempotent
        try:
            self._ensure_schema(db)
        except Exception as e:
            self.fail(f"Second _ensure_schema raised: {e}")
        db.close()

    def test_migration_on_old_db_without_tag_column(self):
        """Simulate a pre-v3.4 DB created without tag column, then migrate."""
        db, _ = self._open_db()
        # Create old-style schema without tag column.
        db.executescript("""
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY,
                path TEXT NOT NULL,
                heading TEXT,
                content TEXT NOT NULL,
                mtime REAL NOT NULL,
                hash TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path);
        """)
        # Insert an old-style row without tag.
        db.execute(
            "INSERT INTO chunks (path, heading, content, mtime, hash) "
            "VALUES (?, ?, ?, ?, ?)",
            ("workspaces/ws1/topic/2026-01-01-note.md", "Heading",
             "[decision] use sqlite for storage", 1.0, "aabbccdd11223344"),
        )
        db.commit()
        # Now run migration via _ensure_schema.
        self._ensure_schema(db)
        # tag column must exist.
        cols = {row[1] for row in db.execute("PRAGMA table_info(chunks)")}
        self.assertIn("tag", cols)
        # Backfilled row must have tag = "decision".
        row = db.execute("SELECT tag FROM chunks LIMIT 1").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "decision",
                         f"backfill produced tag={row[0]!r}, expected 'decision'")
        db.close()

    def test_no_data_loss_after_migration(self):
        """Existing content must survive migration."""
        db, _ = self._open_db()
        db.executescript("""
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY,
                path TEXT NOT NULL,
                heading TEXT,
                content TEXT NOT NULL,
                mtime REAL NOT NULL,
                hash TEXT NOT NULL
            );
        """)
        db.execute(
            "INSERT INTO chunks (path, heading, content, mtime, hash) VALUES (?, ?, ?, ?, ?)",
            ("p.md", "h", "[exp] important lesson about trading", 2.0, "deadbeef12345678"),
        )
        db.commit()
        self._ensure_schema(db)
        row = db.execute("SELECT content, tag FROM chunks LIMIT 1").fetchone()
        self.assertIsNotNone(row)
        self.assertIn("important lesson", row[0])
        self.assertEqual(row[1], "exp")
        db.close()


class TagExtractionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_idx_ext_")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        self.idx = load_module("gowth_index_ext", SCRIPTS / "_index.py")

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)

    def test_known_tag_extracted(self):
        for tag in ("decision", "exp", "ref", "tool", "reflection",
                    "skill-ref", "secret-ref", "goal", "hypothesis"):
            result = self.idx._extract_tag(f"[{tag}] some content here")
            self.assertEqual(result, tag, f"failed for tag={tag!r}")

    def test_unknown_tag_returns_empty(self):
        result = self.idx._extract_tag("[foobar] unknown tag type")
        self.assertEqual(result, "")

    def test_no_tag_returns_empty(self):
        result = self.idx._extract_tag("plain content without any tag marker")
        self.assertEqual(result, "")

    def test_tag_in_middle_of_content_not_extracted(self):
        # Tag must be at the START of content (after optional whitespace).
        result = self.idx._extract_tag("some text [decision] in the middle")
        self.assertEqual(result, "")

    def test_tag_with_leading_whitespace(self):
        result = self.idx._extract_tag("  [ref] fact about gold futures")
        self.assertEqual(result, "ref")

    def test_insertion_populates_tag_column(self):
        """Insert a chunk via raw SQL into a migrated DB; tag populated."""
        db_path = Path(self.tmp) / "index.db"
        db = sqlite3.connect(str(db_path))
        db.execute("PRAGMA journal_mode=WAL")
        self.idx._ensure_schema(db, sample_dim=0, use_vec=False)
        db.commit()

        content = "[decision] use atomic writes for all file ops"
        tag = self.idx._extract_tag(content)
        import hashlib
        h = hashlib.sha1(content.encode()).hexdigest()[:16]
        cid = db.execute(
            "INSERT INTO chunks (path, heading, content, mtime, hash, tag) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("workspaces/ws1/misc/2026-05-18-note.md", "", content, 1.0, h, tag),
        ).lastrowid
        db.commit()

        row = db.execute("SELECT tag FROM chunks WHERE id=?", (cid,)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "decision")
        db.close()


class KeywordsColumnTests(unittest.TestCase):
    """v4.0 keywords column migration + population."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_idx_kw_")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        self.idx = load_module("gowth_index_kw", SCRIPTS / "_index.py")

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)

    def _open_db(self):
        db = sqlite3.connect(str(Path(self.tmp) / "index.db"))
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def test_keywords_column_after_fresh_schema(self):
        db = self._open_db()
        self.idx._ensure_schema(db, 0, False)
        db.commit()
        cols = {row[1] for row in db.execute("PRAGMA table_info(chunks)")}
        self.assertIn("keywords", cols)
        # FTS carries the keywords column too.
        fts = db.execute(
            "SELECT sql FROM sqlite_master WHERE name='chunks_fts'").fetchone()[0]
        self.assertIn("keywords", fts)
        db.close()

    def test_keywords_migration_idempotent(self):
        db = self._open_db()
        self.idx._ensure_schema(db, 0, False)
        db.commit()
        try:
            self.idx._ensure_schema(db, 0, False)
            db.commit()
        except Exception as e:
            self.fail(f"second _ensure_schema raised: {e}")
        db.close()

    def test_keywords_migration_on_v34_db(self):
        """A v3.4 DB (tag but no keywords) gains the keywords column + FTS rebuild."""
        db = self._open_db()
        db.executescript("""
            CREATE TABLE chunks (
                id INTEGER PRIMARY KEY, path TEXT NOT NULL, heading TEXT,
                content TEXT NOT NULL, mtime REAL NOT NULL, hash TEXT NOT NULL,
                tag TEXT NOT NULL DEFAULT '');
        """)
        db.execute(
            "CREATE VIRTUAL TABLE chunks_fts USING fts5("
            "tag, content, content='chunks', content_rowid='id', tokenize='unicode61')")
        db.execute(
            "INSERT INTO chunks(path,heading,content,mtime,hash,tag) VALUES(?,?,?,?,?,?)",
            ("p.md", "", "[exp] lesson with #alpha #beta inline tags", 1.0, "h", "exp"))
        db.commit()
        self.idx._ensure_schema(db, 0, False)
        db.commit()
        cols = {row[1] for row in db.execute("PRAGMA table_info(chunks)")}
        self.assertIn("keywords", cols)
        # Backfilled from inline #tags in the content.
        kw = db.execute("SELECT keywords FROM chunks LIMIT 1").fetchone()[0]
        self.assertIn("alpha", kw)
        self.assertIn("beta", kw)
        db.close()

    def test_chunk_keywords_from_inline_and_frontmatter(self):
        content = "[decision] use FTS5  #fts5 #recall"
        file_text = "---\ntags: [gowth-mem, release]\n---\n\n" + content
        # First chunk: inline tags + frontmatter tags.
        kw = self.idx._chunk_keywords(content, file_text)
        for expect in ("fts5", "recall", "gowth-mem", "release"):
            self.assertIn(expect, kw.split())
        # Non-first chunk: inline only.
        kw2 = self.idx._chunk_keywords(content, None)
        self.assertNotIn("release", kw2.split())
        self.assertIn("fts5", kw2.split())


if __name__ == "__main__":
    unittest.main()
