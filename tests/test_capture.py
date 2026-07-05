#!/usr/bin/env python3
"""Unit tests for v4.0 _capture.py — session turn capture (prompt + actions trace).

Real Claude Code transcripts carry signature-only (empty) thinking blocks, so the
primary captured signal is the visible Claude-text summary + the tool-use actions
trace. An opportunistic thinking extractor is kept for future transcripts that
populate the `thinking` text; tests cover BOTH cases.

All tests use tempfile isolation via GOWTH_MEM_HOME; never touch real ~/.gowth-mem.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "hooks" / "scripts"
MODULE = SCRIPTS_DIR / "_capture.py"


def _load_module():
    sys.path.insert(0, str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location("_capture", MODULE)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _user(content) -> dict:
    return {"type": "user", "message": {"content": content}}


def _assistant(content) -> dict:
    return {"type": "assistant", "message": {"content": content}}


def _tool_use(name: str, **inp) -> dict:
    return {"type": "tool_use", "name": name, "input": inp}


def _field(content: str, label: str) -> str:
    """Return the value on the `**label:** ...` line (up to newline), or ''."""
    marker = f"**{label}:** "
    for line in content.splitlines():
        if line.startswith(marker):
            return line[len(marker):]
    return ""


class CaptureBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        os.environ["GOWTH_MEM_HOME"] = str(self.home)
        self.mod = _load_module()
        self.settings = {
            "reflection": {
                "enabled": True,
                "capture_thinking": True,
                "max_prompt_chars": 2000,
                "max_thinking_chars": 1500,
            }
        }

    def tearDown(self) -> None:
        os.environ.pop("GOWTH_MEM_HOME", None)
        self._tmp.cleanup()

    def _session_file(self, ws: str = "default", sid: str = "sess1234abcd") -> Path:
        today = datetime.now().strftime("%Y-%m-%d")
        return self.home / "workspaces" / ws / "journal" / "sessions" / f"{today}-{sid[:8]}.md"

    def _tx(self, records: list[dict]) -> str:
        tx = self.home / "transcript.jsonl"
        _write_jsonl(tx, records)
        return str(tx)


class TestActionsTrace(CaptureBase):
    def test_tool_use_sequence_captured(self):
        tx = self._tx([
            _user("run the tests"),
            _assistant([
                {"type": "text", "text": "Running the unittest suite."},
                _tool_use("Read", file_path="/a/b/_topic.py"),
                _tool_use("Edit", file_path="/x/_index.py"),
                _tool_use("Bash", command="python3 -m unittest discover -s tests"),
            ]),
        ])
        ok = self.mod.capture_turn(tx, "default", "sess1234abcd", 1, self.settings)
        self.assertTrue(ok)
        content = self._session_file().read_text()
        self.assertEqual(
            _field(content, "Actions"),
            "Read(_topic.py) → Edit(_index.py) → Bash(python3 -m unittest discover -s tests)",
        )
        self.assertEqual(_field(content, "Claude"), "Running the unittest suite.")

    def test_grep_pattern_and_missing_arg(self):
        tx = self._tx([
            _user("search"),
            _assistant([
                _tool_use("Grep", pattern="def capture_turn"),
                _tool_use("SomeTool"),  # no input → bare name
            ]),
        ])
        self.mod.capture_turn(tx, "default", "sess1234abcd", 2, self.settings)
        content = self._session_file().read_text()
        self.assertEqual(_field(content, "Actions"), "Grep(def capture_turn) → SomeTool")

    def test_actions_cap_enforced(self):
        parts = [_tool_use("Read", file_path=f"/d/file{i}.py") for i in range(100)]
        tx = self._tx([_user("many reads"), _assistant(parts)])
        self.mod.capture_turn(tx, "default", "sess1234abcd", 3, self.settings)
        content = self._session_file().read_text()
        self.assertLessEqual(len(_field(content, "Actions")), 500)
        self.assertIn("file0.py", content)
        self.assertNotIn("file99.py", content)


class TestThinkingOpportunistic(CaptureBase):
    def test_empty_thinking_is_primary_realworld_case(self):
        # Signature-only thinking block (empty text) → NO Thinking line.
        tx = self._tx([
            _user("fix the lock bug"),
            _assistant([
                {"type": "thinking", "thinking": "", "signature": "sig-abc"},
                {"type": "text", "text": "Patched the lock acquisition."},
                _tool_use("Edit", file_path="/p/_lock.py"),
            ]),
        ])
        ok = self.mod.capture_turn(tx, "default", "sess1234abcd", 1, self.settings)
        self.assertTrue(ok)
        content = self._session_file().read_text()
        self.assertNotIn("**Thinking:**", content)
        self.assertEqual(_field(content, "Claude"), "Patched the lock acquisition.")
        self.assertEqual(_field(content, "Actions"), "Edit(_lock.py)")

    def test_populated_thinking_key_captured(self):
        tx = self._tx([
            _user("prompt"),
            _assistant([
                {"type": "thinking", "thinking": "reasoning direction here"},
                {"type": "text", "text": "answer"},
            ]),
        ])
        self.mod.capture_turn(tx, "default", "sess1234abcd", 2, self.settings)
        content = self._session_file().read_text()
        self.assertIn("**Thinking:** reasoning direction here", content)

    def test_thinking_text_key_fallback(self):
        tx = self._tx([
            _user("prompt"),
            _assistant([{"type": "thinking", "text": "fallback thinking body"}]),
        ])
        self.mod.capture_turn(tx, "default", "sess1234abcd", 3, self.settings)
        content = self._session_file().read_text()
        self.assertIn("**Thinking:** fallback thinking body", content)

    def test_capture_thinking_disabled_omits_line(self):
        s = {"reflection": {"capture_thinking": False, "max_prompt_chars": 2000, "max_thinking_chars": 1500}}
        tx = self._tx([
            _user("prompt"),
            _assistant([
                {"type": "thinking", "thinking": "SECRET_THINKING_MARKER"},
                {"type": "text", "text": "did the thing"},
            ]),
        ])
        self.mod.capture_turn(tx, "default", "sess1234abcd", 4, s)
        content = self._session_file().read_text()
        self.assertNotIn("**Thinking:**", content)
        self.assertNotIn("SECRET_THINKING_MARKER", content)
        self.assertEqual(_field(content, "Claude"), "did the thing")

    def test_thinking_total_and_per_block_caps(self):
        s = {"reflection": {"capture_thinking": True, "max_prompt_chars": 2000, "max_thinking_chars": 20}}
        tx = self._tx([_user("p"), _assistant([{"type": "thinking", "thinking": "z" * 400}])])
        self.mod.capture_turn(tx, "default", "sess1234abcd", 5, s)
        content = self._session_file().read_text()
        self.assertIn("z" * 20, content)
        self.assertNotIn("z" * 21, content)

        s2 = {"reflection": {"capture_thinking": True, "max_prompt_chars": 2000, "max_thinking_chars": 1500}}
        tx2 = self._tx([_user("p"), _assistant([{"type": "thinking", "thinking": "w" * 600}])])
        self.mod.capture_turn(tx2, "default", "sess1234abcd", 6, s2)
        content2 = self._session_file().read_text()
        self.assertIn("w" * 400, content2)
        self.assertNotIn("w" * 401, content2)


class TestCaptureSelection(CaptureBase):
    def test_str_user_and_list_user_both_captured(self):
        tx = self._tx([_user("str-form prompt"), _assistant([{"type": "text", "text": "ok"}])])
        self.mod.capture_turn(tx, "default", "aaaa1111", 1, self.settings)
        self.assertEqual(_field(self._session_file(sid="aaaa1111").read_text(), "User"), "str-form prompt")

        tx2 = self._tx([
            _user([{"type": "text", "text": "list-form prompt"}]),
            _assistant([{"type": "text", "text": "ok"}]),
        ])
        self.mod.capture_turn(tx2, "default", "bbbb2222", 1, self.settings)
        self.assertEqual(_field(self._session_file(sid="bbbb2222").read_text(), "User"), "list-form prompt")

    def test_tool_result_user_records_excluded(self):
        tx = self._tx([
            _user("real question about fcntl locks"),
            _assistant([{"type": "text", "text": "answer"}, _tool_use("Read", file_path="/x/_lock.py")]),
            _user([{"type": "tool_result", "content": "TOOL_SIDE_EFFECT_DATA"}]),
        ])
        self.mod.capture_turn(tx, "default", "sess1234abcd", 3, self.settings)
        content = self._session_file().read_text()
        self.assertEqual(_field(content, "User"), "real question about fcntl locks")
        self.assertNotIn("TOOL_SIDE_EFFECT_DATA", content)

    def test_claude_head_is_first_300_chars(self):
        tx = self._tx([_user("prompt"), _assistant([{"type": "text", "text": "a" * 500}])])
        self.mod.capture_turn(tx, "default", "sess1234abcd", 4, self.settings)
        content = self._session_file().read_text()
        self.assertIn("a" * 300, content)
        self.assertNotIn("a" * 301, content)

    def test_prompt_cap_enforced(self):
        s = {"reflection": {"capture_thinking": True, "max_prompt_chars": 10, "max_thinking_chars": 1500}}
        tx = self._tx([_user("y" * 50), _assistant([{"type": "text", "text": "ok"}])])
        self.mod.capture_turn(tx, "default", "sess1234abcd", 5, s)
        content = self._session_file().read_text()
        self.assertIn("y" * 10, content)
        self.assertNotIn("y" * 11, content)


class TestCaptureRobustness(CaptureBase):
    def test_missing_transcript_returns_false(self):
        self.assertFalse(self.mod.capture_turn("", "default", "s", 1, self.settings))
        self.assertFalse(self.mod.capture_turn(str(self.home / "nope.jsonl"), "default", "s", 1, self.settings))
        self.assertFalse(self._session_file(sid="s").exists())

    def test_corrupt_transcript_returns_false_no_write(self):
        tx = self.home / "corrupt.jsonl"
        tx.write_text("not json at all\n{also not\nrandom bytes\n")
        ok = self.mod.capture_turn(str(tx), "default", "sess1234abcd", 1, self.settings)
        self.assertFalse(ok)
        self.assertFalse(self._session_file().exists())

    def test_no_user_text_returns_false(self):
        tx = self._tx([_user([{"type": "tool_result", "content": "x"}])])
        self.assertFalse(self.mod.capture_turn(tx, "default", "sess1234abcd", 1, self.settings))

    def test_idempotence_same_turn_twice(self):
        tx = self._tx([_user("dedupe me"), _assistant([{"type": "text", "text": "ok"}])])
        self.assertTrue(self.mod.capture_turn(tx, "default", "sess1234abcd", 5, self.settings))
        self.assertTrue(self.mod.capture_turn(tx, "default", "sess1234abcd", 5, self.settings))
        self.assertEqual(self._session_file().read_text().count("## turn 5"), 1)

    def test_distinct_turns_append_header_once(self):
        tx = self._tx([_user("q one"), _assistant([{"type": "text", "text": "a one"}])])
        self.mod.capture_turn(tx, "default", "sess1234abcd", 1, self.settings)
        self.mod.capture_turn(tx, "default", "sess1234abcd", 2, self.settings)
        content = self._session_file().read_text()
        self.assertIn("## turn 1", content)
        self.assertIn("## turn 2", content)
        self.assertEqual(content.count("# Session log —"), 1)
        self.assertTrue(content.startswith("# Session log —"))


if __name__ == "__main__":
    unittest.main()
