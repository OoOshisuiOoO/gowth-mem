#!/usr/bin/env python3
"""Tests for v3.6 _handoff.py — handoff rotation (keep recent, archive old)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "hooks" / "scripts"


def _load():
    sys.path.insert(0, str(SCRIPTS_DIR))
    import _handoff
    return _handoff


HANDOFF = _load()


def _scaffold(home: Path, ws: str, body: str) -> Path:
    d = home / "workspaces" / ws / "docs"
    d.mkdir(parents=True, exist_ok=True)
    (home / "workspaces" / ws / "workspace.json").write_text("{}")
    hp = d / "handoff.md"
    hp.write_text(body)
    return hp


SAMPLE = """# Handoff — trade

## Entries
curated current state (structural, must stay).

## host:NDP 2026-06-01
old snapshot 1

## host:NDP 2026-06-02
old snapshot 2

## host:NDP 2026-06-10
recent snapshot a

## host:NDP 2026-06-11
recent snapshot b

## host:NDP 2026-06-12
recent snapshot c
"""


class TestRotate(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        os.environ["GOWTH_MEM_HOME"] = str(self.home)

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        self._tmp.cleanup()

    def test_keeps_structural_and_recent_archives_old(self):
        hp = _scaffold(self.home, "trade", SAMPLE)
        r = HANDOFF.rotate_handoff("trade", keep=3, dry_run=False)  # 5 dated → keep 3, archive 2
        self.assertEqual(r["archived"], 2)        # 2026-06-01 and -06-02
        new = hp.read_text()
        # structural section + 2 most-recent dated survive
        self.assertIn("## Entries", new)
        self.assertIn("2026-06-12", new)
        self.assertIn("2026-06-11", new)
        self.assertNotIn("2026-06-01", new)
        self.assertNotIn("2026-06-02", new)
        # archived content moved (not lost)
        arc = (hp.parent / "handoff-archive.md").read_text()
        self.assertIn("2026-06-01", arc)
        self.assertIn("2026-06-02", arc)
        self.assertIn("# Handoff archive", arc)

    def test_dry_run_changes_nothing(self):
        hp = _scaffold(self.home, "trade", SAMPLE)
        before = hp.read_text()
        r = HANDOFF.rotate_handoff("trade", keep=3, dry_run=True)
        self.assertEqual(r["archived"], 2)
        self.assertEqual(hp.read_text(), before)
        self.assertFalse((hp.parent / "handoff-archive.md").exists())

    def test_noop_when_under_keep(self):
        hp = _scaffold(self.home, "trade", SAMPLE)
        r = HANDOFF.rotate_handoff("trade", keep=10, dry_run=False)
        self.assertEqual(r.get("archived", 0), 0)
        self.assertIn("2026-06-01", hp.read_text())  # nothing moved

    def test_no_data_loss_total_sections_preserved(self):
        hp = _scaffold(self.home, "trade", SAMPLE)
        HANDOFF.rotate_handoff("trade", keep=2, dry_run=False)
        combined = hp.read_text() + (hp.parent / "handoff-archive.md").read_text()
        for d in ("2026-06-01", "2026-06-02", "2026-06-10", "2026-06-11", "2026-06-12"):
            self.assertIn(d, combined, f"{d} must survive somewhere")


# v4.1 — bullet-level rotation. Real-world handoffs are a FLAT list of
# `- host:<machine> <date> [status] ...` bullets under one structural
# `## Entries` section, which the section-based rotation never touches
# (observed live: trade 62 bullets / 57 KB, 43 stale — loaded every session).
BULLET_SAMPLE = """# Handoff — trade

Per-session state. Each line: `host:<machine> [doing|next|blocker|thread] <text>`.

## Entries
- host:Mini 2026-07-10 [done] recent work A, keep me.
- host:Mini 2026-07-09 [done] recent work B, keep me.
- host:NDP 2026-06-25 [doing] old but LIVE thread, keep me despite age.
- host:NDP 2026-06-24 [done+blocker] old but blocked on operator, keep me.
- host:NDP 2026-06-15 [done] old finished work, archive me.
- host:NDP 2026-06-12 [done] older finished work, archive me.
  continuation line that belongs to the 06-12 bullet.

## Notes
structural tail section, must stay.
"""


class TestBulletRotate(unittest.TestCase):
    TODAY = "2026-07-11"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        os.environ["GOWTH_MEM_HOME"] = str(self.home)

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        self._tmp.cleanup()

    def _rotate(self, max_age_days=14, dry_run=False):
        return HANDOFF.rotate_handoff(
            "trade", keep=10, dry_run=dry_run,
            max_age_days=max_age_days, today=self.TODAY)

    def test_archives_old_done_bullets_keeps_recent(self):
        hp = _scaffold(self.home, "trade", BULLET_SAMPLE)
        r = self._rotate()
        self.assertEqual(r["bullets_archived"], 2)  # 06-15 and 06-12
        new = hp.read_text()
        self.assertIn("2026-07-10", new)
        self.assertIn("2026-07-09", new)
        self.assertNotIn("2026-06-15", new)
        self.assertNotIn("2026-06-12", new)
        arc = (hp.parent / "handoff-archive.md").read_text()
        self.assertIn("2026-06-15", arc)
        self.assertIn("2026-06-12", arc)

    def test_live_status_bullets_survive_regardless_of_age(self):
        hp = _scaffold(self.home, "trade", BULLET_SAMPLE)
        self._rotate()
        new = hp.read_text()
        self.assertIn("2026-06-25 [doing]", new)          # live thread
        self.assertIn("2026-06-24 [done+blocker]", new)   # blocked on operator

    def test_continuation_lines_move_with_their_bullet(self):
        hp = _scaffold(self.home, "trade", BULLET_SAMPLE)
        self._rotate()
        arc = (hp.parent / "handoff-archive.md").read_text()
        self.assertIn("continuation line that belongs", arc)
        self.assertNotIn("continuation line that belongs", hp.read_text())

    def test_structural_sections_untouched(self):
        hp = _scaffold(self.home, "trade", BULLET_SAMPLE)
        self._rotate()
        new = hp.read_text()
        self.assertIn("## Entries", new)
        self.assertIn("## Notes", new)
        self.assertIn("structural tail section, must stay.", new)

    def test_no_data_loss_all_bullets_survive_somewhere(self):
        hp = _scaffold(self.home, "trade", BULLET_SAMPLE)
        self._rotate()
        combined = hp.read_text() + (hp.parent / "handoff-archive.md").read_text()
        for d in ("2026-07-10", "2026-07-09", "2026-06-25", "2026-06-24",
                  "2026-06-15", "2026-06-12"):
            self.assertIn(d, combined, f"{d} must survive somewhere")

    def test_dry_run_reports_but_does_not_write(self):
        hp = _scaffold(self.home, "trade", BULLET_SAMPLE)
        before = hp.read_text()
        r = self._rotate(dry_run=True)
        self.assertEqual(r["bullets_archived"], 2)
        self.assertEqual(hp.read_text(), before)
        self.assertFalse((hp.parent / "handoff-archive.md").exists())

    def test_disabled_when_max_age_days_zero(self):
        hp = _scaffold(self.home, "trade", BULLET_SAMPLE)
        r = HANDOFF.rotate_handoff("trade", keep=10, dry_run=False,
                                   max_age_days=0, today=self.TODAY)
        self.assertEqual(r.get("bullets_archived", 0), 0)
        self.assertIn("2026-06-12", hp.read_text())


if __name__ == "__main__":
    unittest.main()
