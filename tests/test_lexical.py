"""Tests for _lexical.py — deterministic char-ngram Jaccard fuzzy match."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hooks" / "scripts"))

from _lexical import char_ngrams, jaccard, fuzzy_search  # type: ignore  # noqa: E402


class CharNgramsTests(unittest.TestCase):
    def test_basic_trigrams(self):
        ng = char_ngrams("hello", n=3)
        self.assertEqual(ng, {"hel", "ell", "llo"})

    def test_normalises_case_and_whitespace(self):
        a = char_ngrams("Hello   World", n=3)
        b = char_ngrams("hello world", n=3)
        self.assertEqual(a, b)

    def test_short_input_returns_empty(self):
        self.assertEqual(char_ngrams("ab", n=3), set())
        self.assertEqual(char_ngrams("", n=3), set())

    def test_n_must_be_positive(self):
        with self.assertRaises(ValueError):
            char_ngrams("hello", n=0)


class JaccardTests(unittest.TestCase):
    def test_identical_sets_are_one(self):
        a = char_ngrams("hello world")
        self.assertEqual(jaccard(a, a), 1.0)

    def test_disjoint_sets_are_zero(self):
        a = char_ngrams("abcd")
        b = char_ngrams("wxyz")
        self.assertEqual(jaccard(a, b), 0.0)

    def test_empty_both_returns_zero(self):
        self.assertEqual(jaccard(set(), set()), 0.0)

    def test_typo_tolerance_above_threshold(self):
        a = char_ngrams("hello world")
        b = char_ngrams("helo world")  # single-char drop
        self.assertGreater(jaccard(a, b), 0.3)


class FuzzySearchTests(unittest.TestCase):
    def test_returns_sorted_desc(self):
        candidates = [
            ("a", "the quick brown fox"),
            ("b", "the slow brown dog"),
            ("c", "completely unrelated"),
        ]
        results = fuzzy_search("quick brown fox", candidates, top_k=10, min_score=0.0)
        self.assertGreaterEqual(len(results), 1)
        keys = [k for k, _ in results]
        self.assertEqual(keys[0], "a")

    def test_filters_below_min_score(self):
        candidates = [("a", "completely different text")]
        results = fuzzy_search("xyz123", candidates, min_score=0.9)
        self.assertEqual(results, [])

    def test_top_k_caps_output(self):
        candidates = [(f"k{i}", "hello world") for i in range(5)]
        results = fuzzy_search("hello world", candidates, top_k=2, min_score=0.0)
        self.assertEqual(len(results), 2)

    def test_empty_query_returns_empty(self):
        self.assertEqual(fuzzy_search("", [("a", "anything")]), [])


if __name__ == "__main__":
    unittest.main()
