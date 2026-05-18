#!/usr/bin/env python3
"""Tests for v3.4 hook wrapper shell scripts and auto-journal.py changes.

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

# Repo root (two levels up from tests/)
REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "hooks" / "scripts"
TEMPLATES_DIR = REPO_ROOT / "templates"


class TestShellScriptsExist(unittest.TestCase):
    """Each new .sh wrapper must exist and be executable."""

    def _assert_executable(self, rel_path: str) -> None:
        p = SCRIPTS_DIR / rel_path
        self.assertTrue(p.exists(), f"{rel_path} does not exist")
        self.assertTrue(os.access(p, os.X_OK), f"{rel_path} is not executable")

    def test_conflict_detect_sh_exists_and_executable(self) -> None:
        self._assert_executable("conflict-detect.sh")

    def test_session_start_sh_exists_and_executable(self) -> None:
        self._assert_executable("session-start.sh")

    def test_precompact_sh_exists_and_executable(self) -> None:
        self._assert_executable("precompact.sh")


class TestAutoJournalSubagentSkip(unittest.TestCase):
    """auto-journal.py must exit 0 silently in subagent context."""

    def _run_auto_journal(self, stdin_data: dict, extra_env: dict | None = None) -> subprocess.CompletedProcess:
        env = {**os.environ, "GOWTH_MEM_HOME": "/nonexistent-gowth-mem-test-isolation"}
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "auto-journal.py")],
            input=json.dumps(stdin_data),
            capture_output=True,
            text=True,
            env=env,
        )

    def test_subagent_via_stdin_field_exits_0(self) -> None:
        """stdin JSON with agent_type == "subagent" → exit 0."""
        result = self._run_auto_journal({"agent_type": "subagent", "session_id": "test-sub"})
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        # Should produce no blocking output
        stdout = result.stdout.strip()
        if stdout:
            parsed = json.loads(stdout)
            self.assertNotEqual(parsed.get("decision"), "block",
                                "subagent context must not produce a block decision")

    def test_subagent_via_env_var_exits_0(self) -> None:
        """CLAUDE_SUBAGENT env var → exit 0."""
        result = self._run_auto_journal(
            {"session_id": "test-env-sub"},
            extra_env={"CLAUDE_SUBAGENT": "1"},
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")


class TestAutoJournalDisabledSetting(unittest.TestCase):
    """auto-journal.py must exit 0 when auto_journal_enabled is false in settings."""

    def test_disabled_via_settings_exits_0(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = {"auto_journal": {"journal_every": 10, "auto_journal_enabled": False}}
            settings_path = Path(tmp) / "settings.json"
            settings_path.write_text(json.dumps(settings))

            env = {**os.environ, "GOWTH_MEM_HOME": tmp}
            result = subprocess.run(
                [sys.executable, str(SCRIPTS_DIR / "auto-journal.py")],
                input=json.dumps({"session_id": "test-disabled"}),
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
            stdout = result.stdout.strip()
            if stdout:
                parsed = json.loads(stdout)
                self.assertNotEqual(parsed.get("decision"), "block",
                                    "disabled auto_journal must not block")


class TestAutoJournalInstructionsTemplate(unittest.TestCase):
    """templates/auto-journal-instructions.md must exist and be non-empty."""

    def test_template_exists(self) -> None:
        p = TEMPLATES_DIR / "auto-journal-instructions.md"
        self.assertTrue(p.exists(), "templates/auto-journal-instructions.md does not exist")

    def test_template_non_empty(self) -> None:
        p = TEMPLATES_DIR / "auto-journal-instructions.md"
        content = p.read_text(errors="ignore").strip()
        self.assertGreater(len(content), 100,
                           "templates/auto-journal-instructions.md is too short (< 100 chars)")

    def test_template_contains_protocol_keywords(self) -> None:
        p = TEMPLATES_DIR / "auto-journal-instructions.md"
        content = p.read_text(errors="ignore")
        for keyword in ["[decision]", "[exp]", "[ref]", "quality gates"]:
            self.assertIn(keyword, content, f"Expected keyword '{keyword}' in instructions template")


class TestConflictDetectShNoConflict(unittest.TestCase):
    """conflict-detect.sh must exit 0 when SYNC-CONFLICT.md does not exist."""

    def test_exit_0_when_no_conflict_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # tmp has no SYNC-CONFLICT.md
            env = {**os.environ, "GOWTH_MEM_HOME": tmp}
            result = subprocess.run(
                ["bash", str(SCRIPTS_DIR / "conflict-detect.sh")],
                input="",
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(result.returncode, 0,
                             f"Expected exit 0 with no SYNC-CONFLICT.md, got {result.returncode}. "
                             f"stderr: {result.stderr}")


if __name__ == "__main__":
    unittest.main()
