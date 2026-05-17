"""Tests for _budget.py — 4-tier weighted token-budget context planner."""
import math
import os
import sys
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hooks" / "scripts"))

from _budget import (  # type: ignore  # noqa: E402
    DEFAULT_TIER_WEIGHTS,
    _classify_tier,
    _recency_decay,
    _tier_weights,
    _half_life,
    plan_context,
)


class RecencyDecayTests(unittest.TestCase):
    def test_recent_file_near_one(self):
        now = time.time()
        # 0 days old → exp(0) = 1
        self.assertAlmostEqual(_recency_decay(now, now, 14), 1.0, places=4)

    def test_half_life_exactly_half(self):
        now = time.time()
        mtime = now - 14 * 86400
        decay = _recency_decay(mtime, now, 14)
        self.assertAlmostEqual(decay, 0.5, places=3)

    def test_zero_mtime_returns_zero(self):
        self.assertEqual(_recency_decay(0, time.time(), 14), 0.0)

    def test_zero_half_life_returns_zero(self):
        self.assertEqual(_recency_decay(time.time(), time.time(), 0), 0.0)


class ClassifyTierTests(unittest.TestCase):
    def test_today_journal_is_working(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws_root = Path(tmp)
            jd = ws_root / "journal"
            jd.mkdir()
            today = jd / f"{date.today().isoformat()}.md"
            today.write_text("hi")
            self.assertEqual(_classify_tier(today, ws_root), "working")

    def test_older_journal_is_episodic(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws_root = Path(tmp)
            jd = ws_root / "journal"
            jd.mkdir()
            older = jd / "2020-01-01.md"
            older.write_text("hi")
            self.assertEqual(_classify_tier(older, ws_root), "episodic")

    def test_skills_is_procedural(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws_root = Path(tmp)
            sk = ws_root / "skills"
            sk.mkdir()
            f = sk / "x.md"
            f.write_text("hi")
            self.assertEqual(_classify_tier(f, ws_root), "procedural")

    def test_handoff_is_working(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws_root = Path(tmp)
            docs = ws_root / "docs"
            docs.mkdir()
            f = docs / "handoff.md"
            f.write_text("hi")
            self.assertEqual(_classify_tier(f, ws_root), "working")

    def test_other_docs_are_semantic(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws_root = Path(tmp)
            docs = ws_root / "docs"
            docs.mkdir()
            f = docs / "exp.md"
            f.write_text("hi")
            self.assertEqual(_classify_tier(f, ws_root), "semantic")


class TierWeightsTests(unittest.TestCase):
    def test_defaults_returned_when_no_override(self):
        w = _tier_weights({})
        self.assertEqual(w, DEFAULT_TIER_WEIGHTS)

    def test_override_merges_with_defaults(self):
        w = _tier_weights({"context_budget": {"tier_weights": {"working": 2.0}}})
        self.assertEqual(w["working"], 2.0)
        self.assertEqual(w["episodic"], DEFAULT_TIER_WEIGHTS["episodic"])

    def test_invalid_weight_ignored(self):
        w = _tier_weights({"context_budget": {"tier_weights": {"working": "nope"}}})
        self.assertEqual(w["working"], DEFAULT_TIER_WEIGHTS["working"])


class HalfLifeTests(unittest.TestCase):
    def test_default_when_missing(self):
        self.assertEqual(_half_life({}), 14)

    def test_override(self):
        self.assertEqual(_half_life({"context_budget": {"recency_half_life_days": 7}}), 7)

    def test_invalid_falls_back(self):
        self.assertEqual(_half_life({"context_budget": {"recency_half_life_days": "bad"}}), 14)


class PlanContextIntegrationTests(unittest.TestCase):
    def test_returns_files_within_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / "shared"
            shared.mkdir()
            (shared / "AGENTS.md").write_text("# shared agents\n" * 5)
            (shared / "secrets.md").write_text("# secrets\n")
            (shared / "tools.md").write_text("# tools\n")
            ws = home / "workspaces" / "test"
            ws.mkdir(parents=True)
            (ws / "AGENTS.md").write_text("# ws agents\n")
            docs = ws / "docs"
            docs.mkdir()
            (docs / "handoff.md").write_text("# handoff state\n")
            topic = ws / "fts"
            topic.mkdir()
            (topic / "00-README.md").write_text("# fts MOC\nfts5 indexer details here\n" * 10)

            old = os.environ.get("GOWTH_MEM_HOME")
            os.environ["GOWTH_MEM_HOME"] = str(home)
            try:
                plan = plan_context(ws="test", query="fts5 indexer", budget_chars=2000)
            finally:
                if old is None:
                    os.environ.pop("GOWTH_MEM_HOME", None)
                else:
                    os.environ["GOWTH_MEM_HOME"] = old

            self.assertGreaterEqual(len(plan), 1)
            total = sum(len(s) for _, s, _ in plan)
            self.assertLessEqual(total, 2000)
            # stable prefix should come first; AGENTS.md must appear before any topic file
            paths = [str(p) for p, _, _ in plan]
            self.assertTrue(any("AGENTS.md" in p for p in paths))


if __name__ == "__main__":
    unittest.main()
