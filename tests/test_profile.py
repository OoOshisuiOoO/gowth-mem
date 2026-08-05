"""Tests for _profile.py — the deterministic query profile phi(q) (v4.3).

Adopts Zero-Mem eq (6): phi(q) = {subject, keywords, answer-type, temporal-cues, boundary}.
Purpose in gowth-mem: build a SAFE FTS5 MATCH expression from a parsed profile instead of
passing raw user text (which returns 0 hits for natural language because FTS5 ANDs bare
terms, and raises OperationalError on hyphens/colons).

No LLM, no embeddings, no pip deps.
"""
import importlib.util
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


prof_mod = load_module("gowth_profile", SCRIPTS / "_profile.py")


class TestProfileFields(unittest.TestCase):
    def test_returns_all_five_fields(self):
        p = prof_mod.profile("why did we drop vector recall")
        for key in ("subject", "keywords", "answer_type", "temporal", "boundary"):
            self.assertIn(key, p)

    def test_keywords_exclude_stopwords(self):
        p = prof_mod.profile("why did we drop the vector recall from the plugin")
        for stop in ("why", "did", "we", "the", "from"):
            self.assertNotIn(stop, p["keywords"])

    def test_keywords_keep_identifiers_intact(self):
        p = prof_mod.profile("what does _forget.py do with raw_ttl_days")
        self.assertIn("_forget.py", p["keywords"])
        self.assertIn("raw_ttl_days", p["keywords"])

    def test_subject_is_the_most_specific_keyword(self):
        p = prof_mod.profile("how does prop-firm-funding sizing work")
        self.assertEqual(p["subject"], "prop-firm-funding")

    def test_answer_type_decision(self):
        self.assertEqual(prof_mod.profile("why did we choose FTS5")["answer_type"],
                         "decision")

    def test_answer_type_exp_on_failure_words(self):
        self.assertEqual(prof_mod.profile("what broke in the release")["answer_type"],
                         "exp")

    def test_answer_type_empty_when_no_cue(self):
        self.assertEqual(prof_mod.profile("prop firm funding")["answer_type"], "")

    def test_temporal_cues_detected(self):
        for q in ("what did I do yesterday", "changes since 2026-07-01",
                  "the latest handoff", "when did we ship v4.1"):
            self.assertTrue(prof_mod.profile(q)["temporal"], q)

    def test_temporal_false_without_cue(self):
        self.assertFalse(prof_mod.profile("prop firm funding sizing")["temporal"])

    def test_boundary_none_by_default(self):
        self.assertIsNone(prof_mod.profile("prop firm")["boundary"])

    def test_boundary_from_days_argument(self):
        p = prof_mod.profile("recent work", days=7)
        self.assertEqual(p["boundary"], 7)


class TestFtsMatch(unittest.TestCase):
    """fts_match() must produce an expression FTS5 can always parse."""

    def test_natural_language_becomes_or_joined(self):
        expr = prof_mod.fts_match(prof_mod.profile("why did we drop vector recall"))
        self.assertIn(" OR ", expr)
        self.assertIn('"vector"', expr)
        self.assertIn('"recall"', expr)

    def test_hyphenated_term_is_quoted_not_bare(self):
        expr = prof_mod.fts_match(prof_mod.profile("vector-recall drop"))
        self.assertNotRegex(expr, r'(?<!")\bvector-recall\b(?!")')
        self.assertIn('"vector-recall"', expr)

    def test_colon_is_neutralised(self):
        expr = prof_mod.fts_match(prof_mod.profile("forget: ttl"))
        self.assertNotIn(":", expr)

    def test_double_quotes_in_query_do_not_break_expression(self):
        expr = prof_mod.fts_match(prof_mod.profile('the "funding" path'))
        self.assertEqual(expr.count('"') % 2, 0)

    def test_fts5_operators_are_not_emitted_bare(self):
        expr = prof_mod.fts_match(prof_mod.profile("a AND b OR NOT c NEAR d"))
        # every remaining token must be quoted; no bare operator keywords
        for op in (" AND ", " NOT ", " NEAR "):
            self.assertNotIn(op, expr)

    def test_empty_query_returns_empty_expression(self):
        self.assertEqual(prof_mod.fts_match(prof_mod.profile("   ")), "")

    def test_stopword_only_query_returns_empty_expression(self):
        self.assertEqual(prof_mod.fts_match(prof_mod.profile("the and of")), "")

    def test_expression_is_valid_fts5_against_a_real_table(self):
        import sqlite3
        db = sqlite3.connect(":memory:")
        db.execute("CREATE VIRTUAL TABLE t USING fts5(body)")
        db.execute("INSERT INTO t(body) VALUES ('vector recall dropped in v3.2')")
        hostile = ["why did we drop vector-recall",
                   "forget: ttl",
                   'a AND "b',
                   "NEAR( x",
                   "*",
                   "^caret",
                   "stop-loss OOM (fatal)",
                   "tại sao bỏ vector recall"]
        for q in hostile:
            expr = prof_mod.fts_match(prof_mod.profile(q))
            if not expr:
                continue
            # must not raise
            db.execute("SELECT count(*) FROM t WHERE t MATCH ?", (expr,)).fetchone()


if __name__ == "__main__":
    unittest.main()
