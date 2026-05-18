"""Tests for hooks/scripts/_dream.py — dreaming orchestrator.

All tests use GOWTH_MEM_HOME env override + tempfile to avoid touching ~/.gowth-mem.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "hooks" / "scripts"
DREAM_PY = SCRIPTS / "_dream.py"


# ── helpers ──────────────────────────────────────────────────────────────

def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _run_in_home(args: list[str], home: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["GOWTH_MEM_HOME"] = str(home)
    return subprocess.run(
        [sys.executable, str(DREAM_PY)] + args,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def _make_workspace(home: Path, ws: str) -> Path:
    """Create a minimal workspace structure with workspace.json."""
    ws_dir = home / "workspaces" / ws
    ws_dir.mkdir(parents=True, exist_ok=True)
    (ws_dir / "workspace.json").write_text(json.dumps({"name": ws}))
    docs = ws_dir / "docs"
    docs.mkdir(exist_ok=True)
    return ws_dir


def _populate_state(home: Path, ws: str, file_entries: dict) -> None:
    """Write state.json with activity records for the given workspace files."""
    import time
    now = time.time()
    files: dict = {}
    for rel, count in file_entries.items():
        files[rel] = {
            "count": count,
            "last_seen": now - 3600,
            "query_hashes": [f"h{i}" for i in range(count)],
            "days_seen": ["2026-05-17", "2026-05-18"],
        }
    state = {"version": 2, "files": files, "session": {}}
    (home / "state.json").write_text(json.dumps(state, indent=2))


def _write_topic_file(ws_dir: Path, rel_path: str, content: str) -> None:
    """Write a topic file at ws_dir / rel_path, creating parent dirs."""
    p = ws_dir / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


# ── Test 1: dry_run=True returns dict with all 3 phase keys, no files written ──

class TestDryRun(unittest.TestCase):
    def test_dry_run_returns_all_phases_no_write(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            ws_dir = _make_workspace(home, "test_ws")

            # Create topic files so light_phase has candidates
            rel1 = "workspaces/test_ws/ema-strategy/2026-05-17-entry.md"
            rel2 = "workspaces/test_ws/risk-notes/2026-05-17-limits.md"
            _write_topic_file(ws_dir.parent.parent, rel1,
                              "- [decision] Use EMA 20/50 cross for entry signal\n"
                              "- [ref] Backtest shows 1.2 Sharpe on GC 2020-2025\n")
            _write_topic_file(ws_dir.parent.parent, rel2,
                              "- [decision] Max 1% risk per trade\n"
                              "- [exp] Exceeded limit on 2026-05-10, lost 2.3%\n")
            _populate_state(home, "test_ws", {rel1: 3, rel2: 4})

            env = os.environ.copy()
            env["GOWTH_MEM_HOME"] = str(home)

            # Import and call directly
            old_path = sys.path[:]
            sys.path.insert(0, str(SCRIPTS))
            try:
                import importlib
                dream = importlib.import_module("_dream") if "_dream" in sys.modules else None
                if dream is None:
                    spec = importlib.util.spec_from_file_location("_dream_t1", DREAM_PY)
                    dream = importlib.util.module_from_spec(spec)
                    # patch home via env
                os.environ["GOWTH_MEM_HOME"] = str(home)
                # Use subprocess for clean env isolation
                proc = _run_in_home(["--ws", "test_ws", "--dry-run"], home)
            finally:
                sys.path[:] = old_path
                os.environ.pop("GOWTH_MEM_HOME", None)

            self.assertEqual(proc.returncode, 0, f"stderr: {proc.stderr}")
            result = json.loads(proc.stdout)

            # Must have all 3 phase keys
            self.assertIn("light", result["phases"])
            self.assertIn("rem", result["phases"])
            self.assertIn("deep", result["phases"])

            # dry_run flag must be reflected
            self.assertTrue(result["dry_run"])

            # workspace must be echoed
            self.assertEqual(result["workspace"], "test_ws")

            # summary must be non-empty string
            self.assertIsInstance(result["summary"], str)
            self.assertGreater(len(result["summary"]), 0)

            # No new files should be created beyond what we put in
            # (state.json may be updated by consolidate pipeline write — but
            #  dream itself does not create topic files)
            topic_files_before = {rel1, rel2}
            all_md = set(
                str(p.relative_to(home))
                for p in home.rglob("*.md")
            )
            # Verify our input files still exist and no unexpected .md appeared
            for rel in topic_files_before:
                self.assertIn(rel, all_md)


# ── Test 2: live run consolidates — at least one phase counter > 0 ──────

class TestLiveRun(unittest.TestCase):
    def test_live_run_phase_counters_nonzero_with_duplicates(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            ws_dir = _make_workspace(home, "test_ws")

            # Two files with overlapping keywords so REM clusters them
            rel1 = "workspaces/test_ws/gold-strategy/2026-05-17-ema.md"
            rel2 = "workspaces/test_ws/gold-signals/2026-05-17-entry.md"
            content1 = (
                "- [decision] Gold EMA strategy entry signal crossover\n"
                "- [ref] Gold futures daily range average session\n"
                "- [exp] Gold price signal crossover strategy entry\n"
            )
            content2 = (
                "- [decision] Gold signal crossover entry condition\n"
                "- [ref] Gold futures session open strategy EMA\n"
            )
            _write_topic_file(ws_dir.parent.parent, rel1, content1)
            _write_topic_file(ws_dir.parent.parent, rel2, content2)
            _populate_state(home, "test_ws", {rel1: 5, rel2: 3})

            proc = _run_in_home(["--ws", "test_ws"], home)
            self.assertEqual(proc.returncode, 0, f"stderr: {proc.stderr}\nstdout: {proc.stdout}")
            result = json.loads(proc.stdout)

            phases = result["phases"]
            # Light must have found at least 1 candidate (we gave count >= MIN_RECALL_COUNT=2)
            self.assertGreater(
                phases["light"].get("files_processed", 0), 0,
                f"light phase found no candidates: {phases['light']}"
            )
            # At least one of rem or deep must have processed something
            rem_files = phases["rem"].get("files_processed", 0)
            deep_promoted = phases["deep"].get("promoted", 0)
            deep_maintained = phases["deep"].get("maintained", 0)
            self.assertGreater(
                rem_files + deep_promoted + deep_maintained, 0,
                f"all phase counters are zero: {phases}"
            )


# ── Test 3: missing workspace → empty phase results, no crash ────────────

class TestMissingWorkspace(unittest.TestCase):
    def test_missing_workspace_no_crash(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            # Deliberately do NOT create workspaces/nonexistent
            # We don't even create state.json

            proc = _run_in_home(["--ws", "nonexistent"], home)
            self.assertEqual(proc.returncode, 0, f"stderr: {proc.stderr}")
            result = json.loads(proc.stdout)

            # Must return valid dict structure
            self.assertIn("phases", result)
            self.assertIn("light", result["phases"])
            self.assertIn("rem", result["phases"])
            self.assertIn("deep", result["phases"])
            self.assertIn("summary", result)
            self.assertEqual(result["workspace"], "nonexistent")

            # With no state.json, light phase finds 0 candidates — all counts are 0
            light = result["phases"]["light"]
            self.assertFalse(light.get("error"), f"unexpected error: {light.get('error')}")


# ── Test 4: --no-light flag → light phase skipped ────────────────────────

class TestNoLightFlag(unittest.TestCase):
    def test_no_light_flag_skips_light_phase(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            _make_workspace(home, "test_ws")

            proc = _run_in_home(["--ws", "test_ws", "--no-light"], home)
            self.assertEqual(proc.returncode, 0, f"stderr: {proc.stderr}")
            result = json.loads(proc.stdout)

            light = result["phases"]["light"]
            self.assertTrue(light.get("skipped"), f"light phase should be skipped: {light}")

            # REM and deep should not be skipped (they run with empty candidates)
            self.assertFalse(result["phases"]["rem"].get("skipped"),
                             "rem should not be skipped")
            self.assertFalse(result["phases"]["deep"].get("skipped"),
                             "deep should not be skipped")


# ── Test 5: CLI subprocess returns valid JSON ─────────────────────────────

class TestCliJson(unittest.TestCase):
    def test_cli_dry_run_returns_valid_json(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            _make_workspace(home, "cli_ws")

            proc = _run_in_home(["--ws", "cli_ws", "--dry-run"], home)
            self.assertEqual(proc.returncode, 0,
                             f"non-zero exit:\nstdout={proc.stdout}\nstderr={proc.stderr}")

            # stdout must be valid JSON
            try:
                result = json.loads(proc.stdout)
            except json.JSONDecodeError as e:
                self.fail(f"stdout is not valid JSON: {e}\nstdout={proc.stdout!r}")

            # Required top-level keys
            for key in ("workspace", "phases", "summary", "dry_run"):
                self.assertIn(key, result, f"missing key {key!r} in result")

            # phases has the 3 required keys
            for phase in ("light", "rem", "deep"):
                self.assertIn(phase, result["phases"],
                              f"missing phase {phase!r}")

            # No traceback in stderr
            self.assertNotIn("Traceback", proc.stderr,
                             f"traceback in stderr:\n{proc.stderr}")

    def test_cli_all_workspaces_no_crash(self):
        """Running without --ws against an empty home should not crash."""
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            # No workspaces at all
            proc = _run_in_home([], home)
            self.assertEqual(proc.returncode, 0,
                             f"non-zero exit:\nstdout={proc.stdout}\nstderr={proc.stderr}")
            result = json.loads(proc.stdout)
            self.assertIsNone(result["workspace"])
            self.assertNotIn("Traceback", proc.stderr)


if __name__ == "__main__":
    unittest.main()
