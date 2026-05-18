"""Tests for _dedup.py v3.4 tag-aware SHA-256 dedup.

Covers:
  - [decision] foo + [exp] foo => different hashes => BOTH stored (not dedup'd)
  - [decision] foo twice in 300s window => second is duplicate
  - is_duplicate() cross-file/cross-time check via SQLite index
  - is_duplicate() fails open when DB absent
  - legacy seen_recently / record API unaffected
"""
import hashlib
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


class TagDigestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_dedup_tag_")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        self.dedup = load_module("gowth_dedup_tag", SCRIPTS / "_dedup.py")

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)

    def test_same_content_different_tag_produces_different_digest(self):
        d1 = self.dedup._tag_digest("decision", "[decision] foo bar baz")
        d2 = self.dedup._tag_digest("exp", "[exp] foo bar baz")
        self.assertNotEqual(d1, d2)

    def test_same_tag_same_content_produces_same_digest(self):
        d1 = self.dedup._tag_digest("ref", "[ref] gold futures corr with DXY")
        d2 = self.dedup._tag_digest("ref", "[ref] gold futures corr with DXY")
        self.assertEqual(d1, d2)

    def test_empty_tag_and_no_tag_differ_from_tagged(self):
        d_tagged = self.dedup._tag_digest("decision", "[decision] use stop loss")
        d_empty = self.dedup._tag_digest("", "use stop loss")
        self.assertNotEqual(d_tagged, d_empty)


class CheckAndRecordTagAwareTests(unittest.TestCase):
    """check_and_record uses tag-aware digest in hot-path window."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_dedup_car_")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        self.dedup = load_module("gowth_dedup_car", SCRIPTS / "_dedup.py")

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)

    def test_same_content_same_tag_second_is_duplicate(self):
        text = "[decision] foo bar baz qux"
        self.assertFalse(self.dedup.check_and_record(text))
        self.assertTrue(self.dedup.check_and_record(text))

    def test_same_content_different_tag_both_accepted(self):
        text_a = "[decision] foo bar baz qux"
        text_b = "[exp] foo bar baz qux"
        # First inserts of each tag must pass.
        self.assertFalse(self.dedup.check_and_record(text_a))
        self.assertFalse(self.dedup.check_and_record(text_b))

    def test_untagged_then_tagged_both_accepted(self):
        text_plain = "foo bar baz qux"
        text_tagged = "[decision] foo bar baz qux"
        self.assertFalse(self.dedup.check_and_record(text_plain))
        self.assertFalse(self.dedup.check_and_record(text_tagged))


class IsDuplicateTests(unittest.TestCase):
    """is_duplicate() queries SQLite index — cross-file, cross-session."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_isdup_")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        self.dedup = load_module("gowth_isdup", SCRIPTS / "_dedup.py")
        self.idx = load_module("gowth_idx_for_isdup", SCRIPTS / "_index.py")
        # Create a properly migrated DB.
        db_path = Path(self.tmp) / "index.db"
        self.db = sqlite3.connect(str(db_path))
        self.db.execute("PRAGMA journal_mode=WAL")
        self.idx._ensure_schema(self.db, sample_dim=0, use_vec=False)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        os.environ.pop("GOWTH_MEM_HOME", None)

    def _insert_chunk(self, content: str, tag: str, path: str = "workspaces/ws1/misc/note.md"):
        h = hashlib.sha1(content.encode()).hexdigest()[:16]
        self.db.execute(
            "INSERT INTO chunks (path, heading, content, mtime, hash, tag) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (path, "", content, 1.0, h, tag),
        )
        self.db.commit()

    def test_duplicate_same_tag_and_hash_detected(self):
        content = "[decision] use atomic writes for all file ops"
        tag = "decision"
        self._insert_chunk(content, tag)
        self.assertTrue(self.dedup.is_duplicate(self.tmp, tag, content))

    def test_different_tag_same_content_not_duplicate(self):
        content = "[decision] use atomic writes for all file ops"
        self._insert_chunk(content, "decision")
        # Same content text, but querying with tag="exp" — should NOT be duplicate.
        self.assertFalse(self.dedup.is_duplicate(self.tmp, "exp", content))

    def test_unseen_content_not_duplicate(self):
        self.assertFalse(
            self.dedup.is_duplicate(self.tmp, "ref", "[ref] brand new fact never seen")
        )

    def test_cross_file_duplicate_detected(self):
        """Same (tag, content_hash) in a different file — still flagged."""
        content = "[exp] lesson about overfit backtests"
        tag = "exp"
        self._insert_chunk(content, tag, path="workspaces/ws1/ema/2026-01-01-note.md")
        # Check from a "different" file context — is_duplicate is path-agnostic.
        self.assertTrue(self.dedup.is_duplicate(self.tmp, tag, content))

    def test_fails_open_when_db_absent(self):
        """is_duplicate must return False (not raise) when DB doesn't exist."""
        no_db_tmp = tempfile.mkdtemp(prefix="gowth_nodb_")
        os.environ["GOWTH_MEM_HOME"] = no_db_tmp
        try:
            result = self.dedup.is_duplicate(no_db_tmp, "decision", "[decision] test")
            self.assertFalse(result)
        finally:
            os.environ["GOWTH_MEM_HOME"] = self.tmp

    def test_fails_open_when_db_pre_migration(self):
        """is_duplicate must return False when DB has no tag column."""
        pre_tmp = tempfile.mkdtemp(prefix="gowth_premig_")
        old_home = os.environ.get("GOWTH_MEM_HOME")
        os.environ["GOWTH_MEM_HOME"] = pre_tmp
        try:
            db_path = Path(pre_tmp) / "index.db"
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
            result = self.dedup.is_duplicate(pre_tmp, "decision", "[decision] test")
            self.assertFalse(result)
        finally:
            if old_home is not None:
                os.environ["GOWTH_MEM_HOME"] = old_home
            else:
                os.environ["GOWTH_MEM_HOME"] = self.tmp


if __name__ == "__main__":
    unittest.main()
