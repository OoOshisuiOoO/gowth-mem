"""v4.9 recovery: `_forget.py --resalvage-archive` lifts the curated entries of
ALREADY-archived aspects back into the synced layer.

Before v4.9, aspect archival salvaged only `- [type]` bullets: `## [type]`
blocks and bare `[type]` lines of every archived aspect survived only in this
machine's gitignored `.archive/topics/` (live vault: 2,014 entries from 336
aspects). The recovery reads each `.archive/topics/<ws>/<topic>/*.md.gz` and
salvages every shape into the live topic's lessons.md (same parser, dedup and
provenance as `forget_aspects`), under `entries-<ws>`. It never deletes, moves
or rewrites an archive file.
"""
from __future__ import annotations

import gzip
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_forget_salvage import (BARE, BLOCK, BULLET, FORGET, H1_BLOCK, ORDERED, SCRUB,  # noqa: E402
                                 WS, _aspect_text, _state, _Vault, _w, _forget)


class ResalvageTest(_Vault):
    def gz(self, topic: str, name: str, body: str) -> Path:
        d = self.home / ".archive" / "topics" / WS / topic
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{name[:-3]}-1783773446.md.gz"
        with gzip.open(p, "wb") as f:
            f.write(_aspect_text(name, body).encode("utf-8"))
        return p

    def seed_archive(self) -> list:
        return [self.gz("t", "2020-01-01-bullet.md", BULLET), self.gz("t", "2020-01-02-block.md", BLOCK),
                self.gz("t", "2020-01-03-bare.md", BARE), self.gz("t", "2020-01-04-h1.md", H1_BLOCK),
                self.gz("t", "2020-01-05-ordered.md", ORDERED)]

    def resalvage_cli(self, *extra: str) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k not in SCRUB}
        env.update({"GOWTH_MEM_HOME": str(self.home), "HOME": str(self.tmp / "h"),
                    "CLAUDE_CONFIG_DIR": str(self.tmp / "c"), "GOWTH_MEM_NO_AUTOHEAL": "1"})
        r = subprocess.run([sys.executable, str(FORGET), "--resalvage-archive", "--all-workspaces", *extra],
                           capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        return r

    def test_every_shape_comes_back_with_provenance_and_the_archive_is_untouched(self):
        arcs = self.seed_archive()
        before = {p: _state(p) for p in arcs}
        r = self.resalvage_cli()
        self.assertIn("[demo] 5 archived aspect(s) in 1 topic(s) → 5 entr(y/ies)", r.stdout)
        text = self.lessons.read_text(encoding="utf-8")
        for needle in ("[tool] `rg -n pattern` needs ripgrep 14.1", "## [decision] Use fcntl locks",
                       "flock -n /tmp/x.lock true", "[ref] Heron cache eviction is LRU",
                       "UPDATE 2026-11-05: the TTL became configurable", "# [exp] Rebuilding the index",
                       "`[reflection]` Compare the healthy twin node"):
            self.assertIn(needle, text)
        self.assertEqual(text.count("(salvaged from archived aspect 2020-01-0"), 5, "one provenance line each")
        got = {_forget._norm_hash(b) for b in _forget.entry_blocks(_forget._clean(text))}
        for p in arcs:
            for b in _forget.entry_blocks(_forget._clean(_forget._read_gz(p))):
                self.assertIn(_forget._norm_hash(b), got, f"an entry of {p.name} did not land")
        self.assertEqual({p: _state(p) for p in arcs}, before, "archive files are never rewritten")
        self.assertEqual(len(list((self.home / ".archive").rglob("*.gz"))), 5, "nothing deleted or added")

    def test_dry_run_writes_nothing(self):
        self.seed_archive()
        before = _state(self.lessons)
        r = self.resalvage_cli("--dry-run")
        self.assertIn("[dry-run] resalvage: [demo] 5 archived aspect(s)", r.stdout)
        self.assertEqual(_state(self.lessons), before)

    def test_three_passes_are_byte_identical_after_the_first(self):
        self.seed_archive()
        self.resalvage_cli()
        first = self.lessons.read_bytes()
        for _ in range(2):
            r = self.resalvage_cli()
            self.assertIn("→ 0 entr(y/ies)", r.stdout)
            self.assertEqual(self.lessons.read_bytes(), first)

    def test_entries_already_in_lessons_are_not_added_twice(self):
        self.seed_archive()
        _w(self.lessons, "# Lessons\n\n" + BLOCK + "\n")
        r = self.resalvage_cli()
        self.assertIn("→ 4 entr(y/ies)", r.stdout)
        self.assertEqual(self.lessons.read_text(encoding="utf-8").count("## [decision] Use fcntl locks"), 1)

    def test_a_topic_without_a_live_folder_is_reported_and_left_alone(self):
        gone = self.gz("gone-topic", "2020-01-01-x.md", BLOCK)
        before = _state(gone)
        r = self.resalvage_cli()
        self.assertIn("no live topic folder for: gone-topic", r.stdout)
        self.assertFalse((self.wsd / "gone-topic").exists(), "no folder is created")
        self.assertEqual(_state(gone), before)

    def test_a_symlinked_or_escaping_topic_folder_is_refused(self):
        outside = self.tmp / "outside"
        _w(outside / "00-README.md", "---\nslug: esc\n---\n# esc\n")
        (self.wsd / "esc").symlink_to(outside, target_is_directory=True)
        self.gz("esc", "2020-01-01-x.md", BLOCK)
        r = self.resalvage_cli()
        self.assertIn("no live topic folder for: esc", r.stdout)
        self.assertFalse((outside / "lessons.md").exists(), "never written through a symlink")

    def test_a_held_entries_lock_writes_nothing(self):
        self.seed_archive()
        before = _state(self.lessons)
        from _topic import entries_lock  # type: ignore
        with entries_lock(WS):
            r = _forget.resalvage_archive(WS, False, self.home, lock_timeout=0.2)
        self.assertEqual(r["busy"], 1)
        self.assertEqual(r["salvaged"], 0)
        self.assertEqual(_state(self.lessons), before)

    def test_a_corrupt_archive_member_is_skipped_not_fatal(self):
        self.seed_archive()
        bad = self.home / ".archive" / "topics" / WS / "t" / "2020-02-01-bad-1.md.gz"
        bad.write_bytes(b"not gzip at all")
        r = self.resalvage_cli()
        self.assertIn("5 entr(y/ies)", r.stdout)
        self.assertIn("1 not landed", r.stdout)


if __name__ == "__main__":
    unittest.main()
