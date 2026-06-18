#!/usr/bin/env python3
"""Tests for v3.6 _gate.py — deterministic hard write-rules (canon §1)."""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "hooks" / "scripts"
MODULE = SCRIPTS_DIR / "_gate.py"


def _load():
    sys.path.insert(0, str(SCRIPTS_DIR))
    import _gate  # importable (no hyphen); avoids @dataclass + importlib sys.modules quirk
    return _gate


GATE = _load()


class TestAccept(unittest.TestCase):
    def test_good_entries_accepted(self):
        good = [
            "[ref] sqlite WAL allows concurrent readers. Source: https://sqlite.org/wal.html",
            "[decision] use fcntl locks because os.replace alone is not multi-session safe",
            "[tool] ripgrep v14.1 — `rg -n pattern path` beats grep -r for code search",
            "[exp] build failed due to pydantic v2; fixed by moving the BaseSettings import",
            "[reflection] every bloat incident traced back to capture-without-consolidation",
        ]
        for e in good:
            self.assertTrue(GATE.evaluate(e).ok, f"should accept: {e!r}")


class TestReject(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(GATE.evaluate("   ").reason, "empty")

    def test_placeholder(self):
        for p in ("todo", "TBD", "note to self", "misc", "..."):
            self.assertEqual(GATE.evaluate(p).reason, "placeholder", p)

    def test_too_short(self):
        self.assertEqual(GATE.evaluate("[exp] nope").reason, "too_short")

    def test_hedged_no_evidence(self):
        v = GATE.evaluate("[exp] maybe it works i think probably")
        self.assertFalse(v.ok)
        self.assertIn(v.reason, ("hedged_no_evidence", "too_short"))

    def test_ref_without_source(self):
        self.assertEqual(GATE.evaluate("[ref] the feature is enabled in production now").reason,
                         "ref_without_source")

    def test_decision_without_rationale(self):
        self.assertEqual(GATE.evaluate("[decision] migrate the whole service over to graphql api").reason,
                         "decision_without_rationale")

    def test_tool_without_version_or_syntax(self):
        self.assertEqual(GATE.evaluate("[tool] use the cli tool to deploy the stack to prod").reason,
                         "tool_without_version_or_syntax")

    def test_secret_leak(self):
        for s in (
            "[secret-ref] aws key AKIAIOSFODNN7EXAMPLE in the env",
            "[ref] token sk-abcdefghijklmnopqrstuvwxyz0123456789 works",
            "[tool] ghp_abcdefghijklmnopqrstuvwxyz0123456789 for gh auth",
        ):
            self.assertEqual(GATE.evaluate(s).reason, "secret_leak", s)


class TestStrictToggle(unittest.TestCase):
    def test_non_strict_skips_schema_gates(self):
        # ref-without-source is a strict (schema) rule; non-strict should accept.
        v = GATE.evaluate("[ref] the feature is enabled in production environment now", strict=False)
        self.assertTrue(v.ok)

    def test_base_rules_apply_even_non_strict(self):
        # secret leak + placeholder are base rules — always enforced.
        self.assertFalse(GATE.evaluate("todo", strict=False).ok)
        self.assertEqual(GATE.evaluate("[ref] AKIAIOSFODNN7EXAMPLE key", strict=False).reason, "secret_leak")


class TestLessonGate(unittest.TestCase):
    def test_good_lesson_accepted(self):
        v = GATE.evaluate_lesson(
            symptom="pytest hangs on import of config module",
            tried="cleared __pycache__; pinned pydantic",
            root_cause="pydantic v2 moved BaseSettings to pydantic-settings",
            fix="pip install pydantic-settings; update import",
            source="commit abc1234",
        )
        self.assertTrue(v.ok)

    def test_short_symptom_rejected(self):
        self.assertEqual(GATE.evaluate_lesson("bug", "x", "y", "z").reason, "too_short")

    def test_secret_in_lesson_rejected(self):
        v = GATE.evaluate_lesson("deploy fails with auth error every time",
                                 "checked token", "bad creds",
                                 "rotate AKIAIOSFODNN7EXAMPLE", "")
        self.assertEqual(v.reason, "secret_leak")


class TestScan(unittest.TestCase):
    def test_scan_finds_junk(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["GOWTH_MEM_HOME"] = tmp
            try:
                # reload so _home picks up the env (module caches nothing, but be safe)
                ws = Path(tmp) / "workspaces" / "default"
                topic = ws / "demo"
                topic.mkdir(parents=True)
                (ws / "workspace.json").write_text("{}")
                (topic / "00-README.md").write_text("# Demo\n")
                (topic / "2026-06-18-x.md").write_text(
                    "# X\n\n"
                    "- [ref] sqlite WAL is great. Source: https://sqlite.org\n"   # good
                    "- [ref] this thing is enabled now in prod environment\n"      # junk: no source
                    "- [decision] switched everything to graphql for the api\n"    # junk: no rationale
                )
                findings = GATE.scan_workspace("default")
                reasons = sorted(f["reason"] for f in findings)
                self.assertIn("ref_without_source", reasons)
                self.assertIn("decision_without_rationale", reasons)
                self.assertEqual(len(findings), 2, f"expected 2 junk, got {findings}")
            finally:
                os.environ.pop("GOWTH_MEM_HOME", None)


class TestBlockFormat(unittest.TestCase):
    """v3.8: gate must recognize `## [type] <title>` titled blocks, not only bullets."""

    def test_block_decision_without_rationale_rejected(self):
        self.assertEqual(GATE.evaluate("## [decision] switched everything to graphql").reason,
                         "decision_without_rationale")

    def test_block_decision_with_rationale_accepted(self):
        self.assertTrue(GATE.evaluate(
            "## [decision] use blocks\nuse titled blocks because they read better").ok)

    def test_block_ref_without_source_rejected(self):
        self.assertEqual(GATE.evaluate("## [ref] the feature is enabled in prod now").reason,
                         "ref_without_source")

    def test_block_ref_with_source_accepted(self):
        self.assertTrue(GATE.evaluate(
            "## [ref] WAL allows concurrent readers\nbody.\nSource: https://sqlite.org/wal.html").ok)

    def test_block_secret_leak_rejected(self):
        self.assertEqual(GATE.evaluate("## [secret-ref] key AKIAIOSFODNN7EXAMPLE in env").reason,
                         "secret_leak")

    def test_scan_finds_block_junk(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["GOWTH_MEM_HOME"] = tmp
            try:
                ws = Path(tmp) / "workspaces" / "default"
                topic = ws / "demo"
                topic.mkdir(parents=True)
                (ws / "workspace.json").write_text("{}")
                (topic / "2026-06-18-x.md").write_text(
                    "# X\n\n"
                    "## [ref] sqlite WAL\nconcurrent readers. Source: https://sqlite.org\n\n"  # good
                    "## [decision] switched everything to graphql for the api\nno reason given\n\n"  # junk
                )
                findings = GATE.scan_workspace("default")
                reasons = [f["reason"] for f in findings]
                self.assertIn("decision_without_rationale", reasons)
                self.assertEqual(len(findings), 1, f"expected 1 junk block, got {findings}")
            finally:
                os.environ.pop("GOWTH_MEM_HOME", None)


if __name__ == "__main__":
    unittest.main()
