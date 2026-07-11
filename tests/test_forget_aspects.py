#!/usr/bin/env python3
"""Tests for v4.1 aspect archiving — retention policy: topic aspects older
than `topic_layout.archive_threshold_days` (default 90) are salvaged (curated
`- [type]` blocks → lessons.md, verbatim + provenance) then gzip-archived to
.archive/topics/. A topic ALWAYS keeps its newest `keep_newest` aspects
regardless of age; 00-README.md and lessons.md are never touched.

Age comes from the FILENAME date (the knowledge date), not mtime — mtime is
perturbed by maintenance (validate --fix, retag) and would mis-age files.
"""
from __future__ import annotations

import gzip
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "hooks" / "scripts"


def _load():
    sys.path.insert(0, str(SCRIPTS_DIR))
    import _forget
    return _forget


FORGET = _load()

TODAY = "2026-07-11"


def _aspect(topic: Path, name: str, body: str = "") -> Path:
    p = topic / name
    p.write_text(f"---\ntype: aspect\ndate: {name[:10]}\n---\n\n# {name}\n\n{body}\n")
    return p


class TestAspectForget(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        os.environ["GOWTH_MEM_HOME"] = str(self.home)
        ws = self.home / "workspaces" / "trade"
        ws.mkdir(parents=True)
        (ws / "workspace.json").write_text("{}")
        self.topic = ws / "ema-cross"
        self.topic.mkdir()
        (self.topic / "00-README.md").write_text("---\nslug: ema-cross\n---\n# EMA\n")
        (self.topic / "lessons.md").write_text("# Lessons\n")

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        self._tmp.cleanup()

    def _run(self, threshold_days=90, keep_newest=3, dry_run=False):
        return FORGET.forget_aspects("trade", threshold_days=threshold_days,
                                     keep_newest=keep_newest, dry_run=dry_run,
                                     gh=self.home, today=TODAY)

    def test_old_aspect_archived_newest_kept(self):
        _aspect(self.topic, "2026-01-05-ancient-one.md")   # >90d → archive
        _aspect(self.topic, "2026-02-10-old-two.md")       # >90d but inside keep-3
        _aspect(self.topic, "2026-06-01-recent-a.md")
        _aspect(self.topic, "2026-07-01-recent-b.md")
        r = self._run()
        self.assertEqual(r["archived"], 1)
        self.assertFalse((self.topic / "2026-01-05-ancient-one.md").exists())
        self.assertTrue((self.topic / "2026-02-10-old-two.md").exists())   # keep-newest guard
        gz = list((self.home / ".archive" / "topics" / "trade" / "ema-cross").glob("*.md.gz"))
        self.assertEqual(len(gz), 1)
        with gzip.open(gz[0], "rt") as fh:
            self.assertIn("ancient-one", fh.read())

    def test_keep_newest_guard_regardless_of_age(self):
        _aspect(self.topic, "2026-01-01-a.md")
        _aspect(self.topic, "2026-01-02-b.md")
        _aspect(self.topic, "2026-01-03-c.md")
        r = self._run()   # all 3 ancient, but keep_newest=3 → nothing archived
        self.assertEqual(r["archived"], 0)

    def test_readme_and_lessons_never_touched(self):
        _aspect(self.topic, "2026-01-05-x.md")
        _aspect(self.topic, "2026-01-06-y.md")
        _aspect(self.topic, "2026-01-07-z.md")
        _aspect(self.topic, "2026-01-08-w.md")
        self._run()
        self.assertTrue((self.topic / "00-README.md").exists())
        self.assertTrue((self.topic / "lessons.md").exists())

    def test_salvage_curated_blocks_into_lessons_with_provenance(self):
        _aspect(self.topic, "2026-01-05-x.md",
                "- [decision] archive by knowledge date because mtime is perturbed by maintenance\n"
                "raw prose that is NOT salvaged\n")
        _aspect(self.topic, "2026-05-01-a.md")
        _aspect(self.topic, "2026-05-02-b.md")
        _aspect(self.topic, "2026-05-03-c.md")
        r = self._run()
        self.assertEqual(r["archived"], 1)
        self.assertEqual(r["salvaged"], 1)
        lessons = (self.topic / "lessons.md").read_text()
        self.assertIn("[decision] archive by knowledge date", lessons)
        self.assertIn("2026-01-05-x.md", lessons)          # provenance
        self.assertNotIn("raw prose that is NOT salvaged", lessons)

    def test_dry_run_reports_but_writes_nothing(self):
        _aspect(self.topic, "2026-01-05-x.md", "- [exp] would be salvaged on a real run, kept on dry\n")
        _aspect(self.topic, "2026-05-01-a.md")
        _aspect(self.topic, "2026-05-02-b.md")
        _aspect(self.topic, "2026-05-03-c.md")
        r = self._run(dry_run=True)
        self.assertEqual(r["archived"], 1)  # would-archive count
        self.assertTrue((self.topic / "2026-01-05-x.md").exists())
        self.assertFalse((self.home / ".archive").exists())

    def test_threshold_from_settings_when_none(self):
        (self.home / "settings.json").write_text(json.dumps(
            {"topic_layout": {"archive_threshold_days": 30}}))
        _aspect(self.topic, "2026-05-20-x.md")   # 52 days old → archived at 30d threshold
        _aspect(self.topic, "2026-07-01-a.md")
        _aspect(self.topic, "2026-07-02-b.md")
        _aspect(self.topic, "2026-07-03-c.md")
        r = FORGET.forget_aspects("trade", threshold_days=None, keep_newest=3,
                                  dry_run=False, gh=self.home, today=TODAY)
        self.assertEqual(r["archived"], 1)

    def test_reserved_dirs_untouched(self):
        docs = self.home / "workspaces" / "trade" / "docs"
        docs.mkdir()
        old_doc = docs / "2026-01-05-note.md"
        old_doc.write_text("# not a topic aspect\n")
        _aspect(self.topic, "2026-05-01-a.md")
        self._run()
        self.assertTrue(old_doc.exists())


if __name__ == "__main__":
    unittest.main()
