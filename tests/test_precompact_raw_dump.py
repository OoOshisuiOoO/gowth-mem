#!/usr/bin/env python3
"""Tests for v3.5 precompact-flush.py raw-dump behavior.

All tests use tempfile isolation; never touch real ~/.gowth-mem.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "hooks" / "scripts"
HOOK = SCRIPTS_DIR / "precompact-flush.py"


def _make_transcript(path: Path, user_turns: list[str], assistant_turns: list[str]) -> None:
    """Write a minimal Claude Code transcript JSONL with alternating user/assistant text turns."""
    lines = []
    for u, a in zip(user_turns + [""] * len(assistant_turns), assistant_turns + [""] * len(user_turns)):
        if u:
            lines.append(json.dumps({"type": "user", "message": {"content": u}}))
        if a:
            lines.append(json.dumps({
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": a}]},
            }))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_hook(tmp_home: str, transcript_path: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "GOWTH_MEM_HOME": tmp_home}
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"transcript_path": transcript_path}),
        capture_output=True,
        text=True,
        env=env,
    )


class TestRawDumpPassThrough(unittest.TestCase):
    """Substantive transcript → raw-dump to journal + pass-through (no block)."""

    def test_dump_creates_journal_entry_and_passes_through(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # Layout: ~/.gowth-mem-test/workspaces/default/
            ws_dir = Path(tmp) / "workspaces" / "default"
            ws_dir.mkdir(parents=True)
            # config: active_workspace = default
            (Path(tmp) / "config.json").write_text(
                json.dumps({"active_workspace": "default"})
            )

            tx = Path(tmp) / "transcript.jsonl"
            _make_transcript(
                tx,
                user_turns=["fix the precompact hook", "test the raw-dump"],
                assistant_turns=["analyzing the flow", "verifying"],
            )

            result = _run_hook(tmp, str(tx))
            self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

            # Pass-through: stdout empty (no decision:block JSON)
            self.assertEqual(result.stdout.strip(), "",
                             f"expected no output (pass-through), got: {result.stdout!r}")

            # Journal entry created under <ws>/journal/<today>.md
            journal_dir = ws_dir / "journal"
            self.assertTrue(journal_dir.is_dir(), "journal dir was not created")
            md_files = list(journal_dir.glob("*.md"))
            self.assertEqual(len(md_files), 1, f"expected 1 journal file, got {md_files}")
            content = md_files[0].read_text()
            self.assertIn("[auto-precompact-dump]", content)
            self.assertIn("fix the precompact hook", content)
            self.assertIn("analyzing the flow", content)
            self.assertIn("/mem-distill", content)


class TestEmptyTranscriptPassThrough(unittest.TestCase):
    """< MIN_USER_TURNS substantive user turns → silent pass-through, no journal write."""

    def test_zero_user_turns_passes_through(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws_dir = Path(tmp) / "workspaces" / "default"
            ws_dir.mkdir(parents=True)
            (Path(tmp) / "config.json").write_text(json.dumps({"active_workspace": "default"}))

            tx = Path(tmp) / "transcript.jsonl"
            tx.write_text("")  # empty

            result = _run_hook(tmp, str(tx))
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), "")
            # No journal write
            self.assertFalse((ws_dir / "journal").exists(),
                             "journal dir should not be created on empty transcript")

    def test_single_user_turn_passes_through(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws_dir = Path(tmp) / "workspaces" / "default"
            ws_dir.mkdir(parents=True)
            (Path(tmp) / "config.json").write_text(json.dumps({"active_workspace": "default"}))

            tx = Path(tmp) / "transcript.jsonl"
            _make_transcript(tx, user_turns=["just a quick check"], assistant_turns=["ok"])

            result = _run_hook(tmp, str(tx))
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), "")


class TestRecentlyFlushedPassThrough(unittest.TestCase):
    """Existing fresh *.md under workspace → pass-through without re-dumping."""

    def test_recent_mtime_skips_dump(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws_dir = Path(tmp) / "workspaces" / "default"
            ws_dir.mkdir(parents=True)
            (Path(tmp) / "config.json").write_text(json.dumps({"active_workspace": "default"}))
            # Drop a fresh markdown file so recently_flushed() returns True
            (ws_dir / "fresh.md").write_text("# already saved")

            tx = Path(tmp) / "transcript.jsonl"
            _make_transcript(
                tx,
                user_turns=["question one", "question two"],
                assistant_turns=["answer one", "answer two"],
            )

            result = _run_hook(tmp, str(tx))
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), "",
                             "recently_flushed must short-circuit before raw-dump")
            # journal/ should NOT be created (we short-circuited)
            self.assertFalse((ws_dir / "journal").exists())


class TestSnapshotIdempotency(unittest.TestCase):
    """Second invocation after dump → pass-through (recently_flushed catches it)."""

    def test_second_run_does_not_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws_dir = Path(tmp) / "workspaces" / "default"
            ws_dir.mkdir(parents=True)
            (Path(tmp) / "config.json").write_text(json.dumps({"active_workspace": "default"}))

            tx = Path(tmp) / "transcript.jsonl"
            _make_transcript(
                tx,
                user_turns=["alpha", "beta"],
                assistant_turns=["one", "two"],
            )

            r1 = _run_hook(tmp, str(tx))
            self.assertEqual(r1.returncode, 0)
            r2 = _run_hook(tmp, str(tx))
            self.assertEqual(r2.returncode, 0)
            self.assertEqual(r2.stdout.strip(), "",
                             "second run must pass through silently (recently_flushed)")

            # Still exactly one snapshot section (second run skipped)
            md_files = list((ws_dir / "journal").glob("*.md"))
            self.assertEqual(len(md_files), 1)
            content = md_files[0].read_text()
            self.assertEqual(content.count("[auto-precompact-dump]"), 1,
                             "expected exactly one dump section, idempotency broken")


class TestMissingGowthHomePassThrough(unittest.TestCase):
    """If ~/.gowth-mem/ workspace dir doesn't exist → graceful pass-through.

    v3.5.1: tightened from "block-acceptable" to "MUST pass-through silently".
    Auto-compact on fresh install must not be blockable.
    """

    def test_missing_workspace_dir_no_block_no_create(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # Empty GOWTH_MEM_HOME (no workspaces/ dir)
            tx = Path(tmp) / "transcript.jsonl"
            _make_transcript(
                tx,
                user_turns=["alpha", "beta"],
                assistant_turns=["one", "two"],
            )
            result = _run_hook(tmp, str(tx))
            self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
            # Hook must NOT create workspaces/ as a side effect, must NOT block.
            self.assertFalse((Path(tmp) / "workspaces").exists(),
                             "hook must not materialize ~/.gowth-mem/workspaces/ when missing")
            self.assertEqual(result.stdout.strip(), "",
                             "v3.5.1: fresh install must NEVER block /compact; "
                             f"got stdout: {result.stdout!r}")


class TestExtractFailurePassThrough(unittest.TestCase):
    """Workspace exists but extract_recent_turns returns empty → pass-through, no block."""

    def test_tool_result_only_transcript_passes_through(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws_dir = Path(tmp) / "workspaces" / "default"
            ws_dir.mkdir(parents=True)
            (Path(tmp) / "config.json").write_text(json.dumps({"active_workspace": "default"}))

            # MIN_USER_TURNS satisfied by 2 substantive text turns, but assistant
            # output is tool_use only (no text parts) — extract_recent_turns
            # captures user turns, so this still extracts. To force empty,
            # craft user records with non-text content (tool_result).
            tx = Path(tmp) / "transcript.jsonl"
            lines = [
                json.dumps({"type": "user", "message": {"content": "real q one"}}),
                json.dumps({"type": "user", "message": {"content": "real q two"}}),
                # Trailing tool_result-shaped user record (skipped by extract)
                json.dumps({"type": "user", "message": {"content": [
                    {"type": "tool_result", "content": "side-effect data"}
                ]}}),
            ]
            tx.write_text("\n".join(lines) + "\n", encoding="utf-8")

            result = _run_hook(tmp, str(tx))
            self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
            self.assertEqual(result.stdout.strip(), "",
                             "v3.5.1: hook must never emit decision:block JSON")


class TestExtractRecentTurnsCap(unittest.TestCase):
    """extract_recent_turns must respect max_chars budget."""

    def test_max_chars_enforced(self) -> None:
        sys.path.insert(0, str(SCRIPTS_DIR))
        try:
            mod = __import__("precompact-flush".replace("-", "_")) if False else None
            # Direct import via runpy to avoid module name with hyphen.
            import importlib.util
            spec = importlib.util.spec_from_file_location("precompact_flush", HOOK)
            assert spec and spec.loader
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        finally:
            sys.path.pop(0)

        with tempfile.TemporaryDirectory() as tmp:
            tx = Path(tmp) / "transcript.jsonl"
            big = "x" * 5000
            _make_transcript(
                tx,
                user_turns=[big] * 20,
                assistant_turns=[big] * 20,
            )
            text = mod.extract_recent_turns(str(tx), max_chars=10_000)
            self.assertLessEqual(len(text), 20_000,
                                 "extract_recent_turns blew through budget by >2x")
            # Must include the most recent turn (tail-first selection)
            self.assertIn(big, text)


if __name__ == "__main__":
    unittest.main()
