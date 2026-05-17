"""Tests for _compress.py — rtk-style pre-storage compression."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hooks" / "scripts"))

from _compress import collapse_repeats, group_by_prefix, compress_block  # type: ignore  # noqa: E402


class CollapseRepeatsTests(unittest.TestCase):
    def test_collapses_runs_above_threshold(self):
        text = "foo\nfoo\nfoo\nfoo\nbar\n"
        out = collapse_repeats(text, min_repeat=3)
        self.assertEqual(out, "foo  (×4)\nbar\n")

    def test_leaves_short_runs_alone(self):
        text = "foo\nfoo\nbar\n"
        out = collapse_repeats(text, min_repeat=3)
        self.assertEqual(out, text)

    def test_empty_lines_never_collapse(self):
        text = "\n\n\n\n"
        out = collapse_repeats(text, min_repeat=3)
        self.assertEqual(out, text)

    def test_preserves_trailing_newline(self):
        self.assertEqual(collapse_repeats("a\nb\n"), "a\nb\n")
        self.assertEqual(collapse_repeats("a\nb"), "a\nb")

    def test_idempotent(self):
        text = "x\nx\nx\nx\ny\n"
        once = collapse_repeats(text)
        twice = collapse_repeats(once)
        self.assertEqual(once, twice)

    def test_min_repeat_must_be_at_least_two(self):
        with self.assertRaises(ValueError):
            collapse_repeats("hi", min_repeat=1)


class GroupByPrefixTests(unittest.TestCase):
    def test_merges_run_above_threshold(self):
        text = "k: a\nk: b\nk: c\nk: d\nk: e\nk: f\n"
        out = group_by_prefix(text, max_per_group=5)
        self.assertIn("k: [6 items:", out)
        self.assertIn("+1 more", out)

    def test_keeps_short_runs_verbatim(self):
        text = "k: a\nk: b\n"
        out = group_by_prefix(text, max_per_group=5)
        self.assertEqual(out, text)

    def test_unrelated_lines_unchanged(self):
        text = "just a line\nanother line\n"
        out = group_by_prefix(text, max_per_group=2)
        self.assertEqual(out, text)

    def test_different_keys_dont_merge(self):
        text = "a: 1\nb: 2\na: 3\nb: 4\na: 5\nb: 6\n"
        out = group_by_prefix(text, max_per_group=2)
        # Each adjacent run is length 1; no merge possible
        self.assertEqual(out, text)


class CompressBlockTests(unittest.TestCase):
    def test_both_passes_apply(self):
        text = "foo\nfoo\nfoo\nfoo\nk: 1\nk: 2\nk: 3\nk: 4\nk: 5\n"
        out = compress_block(text)
        self.assertIn("foo  (×4)", out)
        self.assertIn("k: [5 items:", out)

    def test_idempotent(self):
        text = "x\nx\nx\nx\nk: 1\nk: 2\nk: 3\nk: 4\nk: 5\nk: 6\n"
        once = compress_block(text)
        twice = compress_block(once)
        self.assertEqual(once, twice)


if __name__ == "__main__":
    unittest.main()
