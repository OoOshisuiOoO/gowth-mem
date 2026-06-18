#!/usr/bin/env python3
"""Tests for v3.6 _forget.py — journal raw-memory TTL (active forgetting).

All tests use tempfile isolation via GOWTH_MEM_HOME; never touch real ~/.gowth-mem.
"""
from __future__ import annotations

import gzip
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "hooks" / "scripts"
MODULE = SCRIPTS_DIR / "_forget.py"


def _load_module():
    sys.path.insert(0, str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location("_forget", MODULE)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _scaffold_ws(home: Path, ws: str = "default") -> Path:
    jd = home / "workspaces" / ws / "journal"
    jd.mkdir(parents=True, exist_ok=True)
    (home / "workspaces" / ws / "workspace.json").write_text(json.dumps({"name": ws}))
    (home / "config.json").write_text(json.dumps({"active_workspace": ws}))
    return jd


def _backdate(path: Path, days: float) -> None:
    t = time.time() - days * 86400
    os.utime(path, (t, t))


class TestForgetCore(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        os.environ["GOWTH_MEM_HOME"] = str(self.home)
        self.mod = _load_module()

    def tearDown(self) -> None:
        os.environ.pop("GOWTH_MEM_HOME", None)
        self._tmp.cleanup()

    def _forget(self, **kw):
        return self.mod.forget_workspace(
            "default", kw.get("ttl_days", 7), kw.get("max_bytes", 75_000),
            kw.get("salvage", True), kw.get("dry_run", False), self.home,
        )

    def test_old_journal_archived_and_removed(self):
        jd = _scaffold_ws(self.home)
        old = jd / "2026-01-01.md"
        old.write_text("## [auto-precompact-dump]\n\n### [assistant]\n\nraw transcript noise\n")
        _backdate(old, 30)

        r = self._forget()
        self.assertEqual(r["archived"], 1)
        self.assertFalse(old.exists(), "old journal must be removed after archive")
        gzs = list((self.home / ".archive" / "journal" / "default").glob("*.md.gz"))
        self.assertEqual(len(gzs), 1, "expected one gz archive")
        # archive must be a faithful copy
        with gzip.open(gzs[0], "rb") as f:
            self.assertIn(b"raw transcript noise", f.read())

    def test_today_journal_protected(self):
        jd = _scaffold_ws(self.home)
        today = jd / (datetime.now().strftime("%Y-%m-%d") + ".md")
        today.write_text("x" * 200_000)  # huge AND today → must still be kept
        r = self._forget()
        self.assertEqual(r["archived"], 0, "today's journal must never be archived")
        self.assertTrue(today.exists())

    def test_recent_journal_within_ttl_kept(self):
        jd = _scaffold_ws(self.home)
        recent = jd / "2026-06-16.md"
        recent.write_text("recent working memory\n")
        _backdate(recent, 2)  # within 7d TTL, small
        r = self._forget()
        self.assertEqual(r["archived"], 0)
        self.assertTrue(recent.exists())

    def test_oversized_old_journal_archived_by_size(self):
        jd = _scaffold_ws(self.home)
        big = jd / "2026-06-14.md"
        big.write_text("y" * 100_000)  # over max_bytes
        _backdate(big, 2)  # within TTL by age, but >1 day old AND oversized
        r = self._forget(max_bytes=75_000)
        self.assertEqual(r["archived"], 1, "oversized >1d-old journal should archive by size")

    def test_salvage_lifts_curated_entry(self):
        jd = _scaffold_ws(self.home)
        old = jd / "2026-01-02.md"
        old.write_text(
            "## [auto-precompact-dump]\n\n"
            "### [assistant]\n\nlong raw transcript prose with no bullet entries here\n\n"
            "- [decision] use fcntl locks for multi-session safety because os.replace is atomic\n"
            "- [ref] sqlite WAL allows concurrent readers. Source: sqlite.org/wal.html\n"
        )
        _backdate(old, 30)
        r = self._forget()
        self.assertEqual(r["salvaged"], 2)
        salvage = (jd / "_salvage.md").read_text()
        self.assertIn("fcntl locks", salvage)
        self.assertIn("sqlite WAL", salvage)
        # raw prose must NOT be salvaged (no bullet)
        self.assertNotIn("long raw transcript prose", salvage)

    def test_salvage_dedup_across_runs(self):
        jd = _scaffold_ws(self.home)
        for name in ("2026-01-03.md", "2026-01-04.md"):
            f = jd / name
            f.write_text("- [decision] identical fact kept once because dedup by sha1 hash\n")
            _backdate(f, 30)
        r = self._forget()
        salvage = (jd / "_salvage.md").read_text()
        self.assertEqual(salvage.count("identical fact kept once"), 1,
                         "duplicate curated entries must dedupe to one")

    def test_short_entry_below_min_chars_not_salvaged(self):
        jd = _scaffold_ws(self.home)
        old = jd / "2026-01-05.md"
        old.write_text("- [exp] nope\n")  # body < 20 chars → drop
        _backdate(old, 30)
        r = self._forget()
        self.assertEqual(r["salvaged"], 0)

    def test_dry_run_changes_nothing(self):
        jd = _scaffold_ws(self.home)
        old = jd / "2026-01-06.md"
        old.write_text("- [decision] something durable and long enough to pass the gate here\n")
        _backdate(old, 30)
        r = self._forget(dry_run=True)
        self.assertTrue(old.exists(), "dry-run must not remove files")
        self.assertFalse((self.home / ".archive").exists(), "dry-run must not write archives")
        self.assertFalse((jd / "_salvage.md").exists(), "dry-run must not write salvage")


class TestForgetCLI(unittest.TestCase):
    def test_missing_home_graceful(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope"
            env = {**os.environ, "GOWTH_MEM_HOME": str(missing)}
            r = subprocess.run([sys.executable, str(MODULE), "--all-workspaces"],
                               capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")

    def test_cli_all_workspaces_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            jd = _scaffold_ws(home, "trade")
            old = jd / "2026-01-07.md"
            old.write_text("## [auto-precompact-dump]\n\nraw\n")
            _backdate(old, 40)
            env = {**os.environ, "GOWTH_MEM_HOME": str(home)}
            r = subprocess.run([sys.executable, str(MODULE), "--all-workspaces", "--dry-run"],
                               capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")
            self.assertIn("dry-run", r.stdout)
            self.assertTrue(old.exists(), "dry-run CLI must not remove files")


if __name__ == "__main__":
    unittest.main()
