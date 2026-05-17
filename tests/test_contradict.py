"""Tests for _contradict.py — heuristic polarity-pair contradiction detection."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hooks" / "scripts"))

from _contradict import (  # type: ignore  # noqa: E402
    _polarity_signature,
    _is_opposite,
    find_contradictions,
)


class PolaritySignatureTests(unittest.TestCase):
    def test_positive_marker(self):
        sig = _polarity_signature("the feature is enabled")
        self.assertIn("enabled", sig)
        self.assertNotIn("enabled:neg", sig)

    def test_negation_marker(self):
        sig = _polarity_signature("the feature is not enabled")
        self.assertIn("enabled:neg", sig)
        self.assertNotIn("enabled", sig)

    def test_no_polarity_marker(self):
        self.assertEqual(_polarity_signature("the feature exists"), set())


class IsOppositeTests(unittest.TestCase):
    def test_polarity_pair_opposites(self):
        a = _polarity_signature("foo is enabled")
        b = _polarity_signature("foo is disabled")
        self.assertTrue(_is_opposite(a, b))

    def test_negation_opposites(self):
        a = _polarity_signature("foo is enabled")
        b = _polarity_signature("foo is not enabled")
        self.assertTrue(_is_opposite(a, b))

    def test_same_polarity_not_opposite(self):
        a = _polarity_signature("foo is enabled")
        b = _polarity_signature("bar is enabled")
        self.assertFalse(_is_opposite(a, b))

    def test_empty_signatures_not_opposite(self):
        self.assertFalse(_is_opposite(set(), set()))
        self.assertFalse(_is_opposite({"enabled"}, set()))


class FindContradictionsTests(unittest.TestCase):
    def _make_home(self, ws_files: dict) -> tempfile.TemporaryDirectory:
        tmp = tempfile.TemporaryDirectory()
        home = Path(tmp.name)
        ws_root = home / "workspaces" / "test" / "topic"
        ws_root.mkdir(parents=True, exist_ok=True)
        for name, body in ws_files.items():
            (ws_root / name).write_text(body)
        return tmp, home

    def test_finds_simple_contradiction(self):
        files = {
            "2026-05-15-fts.md": "- [ref] fts5 indexer is enabled in sqlite for memory search\n",
            "2026-05-17-fts.md": "- [ref] fts5 indexer is disabled in sqlite for memory search\n",
        }
        tmp, home = self._make_home(files)
        try:
            old = os.environ.get("GOWTH_MEM_HOME")
            os.environ["GOWTH_MEM_HOME"] = str(home)
            try:
                pairs = find_contradictions(ws="test", min_entity_overlap=3)
            finally:
                if old is None:
                    os.environ.pop("GOWTH_MEM_HOME", None)
                else:
                    os.environ["GOWTH_MEM_HOME"] = old
            self.assertEqual(len(pairs), 1)
            self.assertIn("fts5", pairs[0]["shared_entities"])
        finally:
            tmp.cleanup()

    def test_no_overlap_no_match(self):
        files = {
            "a.md": "- [ref] alpha beta gamma delta enabled here\n",
            "b.md": "- [ref] zulu yankee xray whiskey disabled there\n",
        }
        tmp, home = self._make_home(files)
        try:
            old = os.environ.get("GOWTH_MEM_HOME")
            os.environ["GOWTH_MEM_HOME"] = str(home)
            try:
                pairs = find_contradictions(ws="test", min_entity_overlap=3)
            finally:
                if old is None:
                    os.environ.pop("GOWTH_MEM_HOME", None)
                else:
                    os.environ["GOWTH_MEM_HOME"] = old
            self.assertEqual(pairs, [])
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
