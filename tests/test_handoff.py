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


if __name__ == "__main__":
    unittest.main()
