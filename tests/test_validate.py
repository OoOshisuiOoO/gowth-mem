#!/usr/bin/env python3
"""Tests for v3.7 _validate.py — file-level schema validator (supremor-learned)."""
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
    import _validate
    return _validate


V = _load()


def _topic(home: Path, ws: str, slug: str) -> Path:
    d = home / "workspaces" / ws / slug
    d.mkdir(parents=True, exist_ok=True)
    (home / "workspaces" / ws / "workspace.json").write_text("{}")
    return d


class TestValidate(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        os.environ["GOWTH_MEM_HOME"] = str(self.home)
        self.root = self.home / "workspaces" / "trade"

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        self._tmp.cleanup()

    def test_conformant_aspect_no_issues(self):
        t = _topic(self.home, "trade", "ema-cross")
        p = t / "2026-06-18-backtest.md"
        p.write_text("---\nslug: ema-cross-backtest\ntitle: Backtest\ntype: aspect\n"
                     "date: 2026-06-18\ntopic: ema-cross\nstatus: active\n---\n\n# Backtest\n")
        self.assertEqual(V.validate_file(p, self.root), [])

    def test_missing_frontmatter_flagged(self):
        t = _topic(self.home, "trade", "ema-cross")
        p = t / "2026-06-18-rules.md"
        p.write_text("# Rules\n\n- [decision] enter on cross because momentum confirms\n")
        self.assertIn("missing-frontmatter", V.validate_file(p, self.root))

    def test_partial_frontmatter_missing_field(self):
        t = _topic(self.home, "trade", "ema-cross")
        p = t / "2026-06-18-x.md"
        p.write_text("---\nslug: ema-cross-x\ntitle: X\ntype: aspect\nstatus: active\n---\n\n# X\n")
        issues = V.validate_file(p, self.root)
        self.assertIn("missing-field:date", issues)
        self.assertIn("missing-field:topic", issues)

    def test_wrong_type_flagged(self):
        t = _topic(self.home, "trade", "ema-cross")
        p = t / "2026-06-18-y.md"
        p.write_text("---\nslug: s\ntitle: Y\ntype: topic\ndate: 2026-06-18\ntopic: ema-cross\nstatus: active\n---\n# Y\n")
        self.assertTrue(any(i.startswith("wrong-type") for i in V.validate_file(p, self.root)))

    def test_fix_adds_full_frontmatter(self):
        t = _topic(self.home, "trade", "vol-target")
        p = t / "2026-06-11-ea-port.md"
        p.write_text("# EA port\n\n- [decision] lock acct 109680411 because Real6 is canonical\n")
        self.assertTrue(V.fix_aspect(p))
        self.assertEqual(V.validate_file(p, self.root), [], V.validate_file(p, self.root))
        txt = p.read_text()
        self.assertIn("type: aspect", txt)
        self.assertIn("date: 2026-06-11", txt)
        self.assertIn("topic: vol-target", txt)
        self.assertIn("109680411", txt)  # content preserved
        # idempotent
        self.assertFalse(V.fix_aspect(p))

    def test_fix_repairs_partial_and_wrong_type(self):
        t = _topic(self.home, "trade", "ema-cross")
        p = t / "2026-06-18-z.md"
        p.write_text("---\nslug: keep-me\ntitle: Z\ntype: note\nstatus: active\n---\n\n# Z\nbody\n")
        self.assertTrue(V.fix_aspect(p))
        txt = p.read_text()
        self.assertIn("slug: keep-me", txt)        # existing preserved
        self.assertIn("type: aspect", txt)         # wrong type corrected
        self.assertIn("date: 2026-06-18", txt)     # missing added
        self.assertIn("topic: ema-cross", txt)
        self.assertIn("body", txt)                 # body preserved
        self.assertEqual(V.validate_file(p, self.root), [])

    def test_scan_workspace(self):
        t = _topic(self.home, "trade", "ema-cross")
        (t / "2026-06-18-a.md").write_text("# A\nno frontmatter\n")
        (t / "00-README.md").write_text("---\nslug: ema-cross\ntitle: EMA\ntype: topic\nstatus: active\n---\n# EMA\n")
        findings = V.scan_workspace("trade")
        files = [f["file"] for f in findings]
        self.assertTrue(any("2026-06-18-a.md" in f for f in files))
        self.assertFalse(any("00-README" in f for f in files), "conformant MOC should not be flagged")


if __name__ == "__main__":
    unittest.main()


class TestFixAspectSlugClamp(unittest.TestCase):
    """v4.1.2: fix_aspect derived `slug: {topic}-{aspect}` unclamped — a long
    topic+aspect pair produced a 71-char slug that route() later passed to
    ensure_topic_folder → ValueError (live crash while routing a reflection)."""

    def test_derived_slug_clamped_to_60_and_valid(self):
        import re as _re
        with tempfile.TemporaryDirectory() as tmp:
            topic = Path(tmp) / "gowth-mem"
            topic.mkdir()
            p = topic / "2026-07-11-askuserquestion-counterfactual-unconfirmed-interrupted-refle.md"
            p.write_text("body only, no frontmatter\n")
            self.assertTrue(V.fix_aspect(p))
            text = p.read_text()
            m = _re.search(r"^slug: (.+)$", text, _re.MULTILINE)
            self.assertIsNotNone(m)
            slug = m.group(1).strip()
            self.assertLessEqual(len(slug), 60, slug)
            self.assertRegex(slug, r"^[a-z0-9][a-z0-9-]{0,59}$")
            self.assertFalse(slug.endswith("-"), slug)
