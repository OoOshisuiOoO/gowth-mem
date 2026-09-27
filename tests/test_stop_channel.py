#!/usr/bin/env python3
"""v4.7.3 — Stop-hook directives travel on Claude Code's NON-ERROR channel.

THE DEFECT THIS PINS
--------------------
Users kept reporting this as an error, every 10/15 turns:

    Ran 9 stop hooks
      ⎿  Stop hook error: [gowth-mem:self-review ws=trade] 15 turns logged. DISPATCH …

Nothing failed. auto-journal.py printed `{"decision": "block", "reason": …}` and
exited 0. Claude Code (2.1.283 binary, verified) files a block reason under
`hookErrors`: the stop-hook summary row renders it red as "Stop hook error: …"
and raises a "Stop hook error occurred · ctrl+o to see" notification.

CHANGELOG 2.1.163: "Stop and SubagentStop hooks can now return
`hookSpecificOutput.additionalContext` to give Claude feedback and keep the turn
going without being labeled a hook error." Same continuation (the attachment
joins the same `blockingErrors` array that re-invokes the model with
`stopHookActive: true`), rendered gold as "Stop hook feedback: …", no error
notification; the model reads it as "Stop hook additional context: …".

Claude Code exports its own version to every subprocess (hooks included) via
`AI_AGENT=claude-code_<M>-<m>-<p>_<surface>` (since 2.1.120). ≥ 2.1.163 → the
feedback channel. Older, unknown or unparseable → `decision: block`, the one
shape every version acts on: a silently dropped directive is worse than a
mislabeled one.
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

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "hooks" / "scripts"
HOOK = SCRIPTS_DIR / "auto-journal.py"


def _load(name: str):
    sys.path.insert(0, str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestClaudeCodeVersion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v = _load("_version")

    def ver(self, value):
        return self.v.claude_code_version({} if value is None else {"AI_AGENT": value})

    def test_parses_current_and_older_formats(self):
        self.assertEqual(self.ver("claude-code_2-1-283_harness"), (2, 1, 283))
        self.assertEqual(self.ver("claude-code_2-1-283_agent"), (2, 1, 283))
        self.assertEqual(self.ver("claude-code/2.1.200"), (2, 1, 200))
        self.assertEqual(self.ver("  claude-code_3-0-1_harness "), (3, 0, 1))

    def test_unknown_is_none(self):
        for value in (None, "", "my-custom-agent", "claude-code_", "claude-code_x-y-z",
                      "cursor_1-2-3", "claude-code_2-1"):
            self.assertIsNone(self.ver(value), value)

    def test_never_raises(self):
        self.assertIsNone(self.v.claude_code_version({"AI_AGENT": 42}))
        self.assertIsNone(self.v.claude_code_version({"AI_AGENT": None}))
        # env=None reads the real os.environ: whatever the runner is, the
        # result is a 3-tuple or None — never an exception.
        got = self.v.claude_code_version(None)
        self.assertTrue(got is None or (isinstance(got, tuple) and len(got) == 3), got)

    def test_support_boundary_is_2_1_163_numeric_not_lexical(self):
        s = lambda value: self.v.supports_stop_context({"AI_AGENT": value})  # noqa: E731
        self.assertFalse(s("claude-code_2-1-162_harness"))
        self.assertTrue(s("claude-code_2-1-163_harness"))
        self.assertTrue(s("claude-code_2-1-283_agent"))
        self.assertFalse(s("claude-code_2-1-99_harness"), '"99" > "163" lexically')
        self.assertTrue(s("claude-code_2-10-0_harness"))
        self.assertTrue(s("claude-code_3-0-0_harness"))
        self.assertFalse(s("claude-code_1-9-999_harness"))
        self.assertFalse(self.v.supports_stop_context({}))


class ChannelHarness(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        ws = self.home / "workspaces" / "default"
        (ws / "journal").mkdir(parents=True)
        (ws / "workspace.json").write_text(json.dumps({"name": "default"}))
        (self.home / "config.json").write_text(json.dumps({"active_workspace": "default"}))
        (self.home / "settings.json").write_text(json.dumps({
            "auto_journal": {"journal_every": 99, "auto_journal_enabled": True},
            "reflection": {"enabled": True, "turn_interval": 1, "min_review_turns": 1},
            "journal": {"auto_forget_enabled": False},
        }))
        self.tx = self.home / "transcript.jsonl"
        # A Stop always follows the model's reply — an unanswered origin-less
        # record is not an ask (e.g. /model), so the fixture carries the reply.
        self.tx.write_text("\n".join(json.dumps(r) for r in (
            {"type": "user", "message": {"content": "do it"}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}},
        )) + "\n")

    def stop(self, sid: str, ai_agent: str | None, transcript: bool = True) -> dict:
        payload = {"session_id": sid, "hook_event_name": "Stop", "stop_hook_active": False}
        if transcript:
            payload["transcript_path"] = str(self.tx)
        env = {**os.environ, "GOWTH_MEM_HOME": str(self.home),
               # isolate the backlog scan from the real ~/.claude/projects
               "CLAUDE_CONFIG_DIR": str(self.home / "claude-config")}
        for k in ("AI_AGENT", "GOWTH_WORKSPACE", "CLAUDE_SUBAGENT"):  # no runner-env leaks
            env.pop(k, None)
        if ai_agent is not None:
            env["AI_AGENT"] = ai_agent
        r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip())


class TestDirectiveChannel(ChannelHarness):
    def test_new_claude_code_gets_the_non_error_channel(self):
        d = self.stop("newchan12345", "claude-code_2-1-283_harness")
        self.assertEqual(set(d), {"hookSpecificOutput"},
                         "no decision/reason keys — those are what Claude Code labels an error")
        hso = d["hookSpecificOutput"]
        self.assertEqual(hso.get("hookEventName"), "Stop")
        self.assertIn("[gowth-mem:self-review ws=default]", hso.get("additionalContext", ""))

    def test_boundary_version_gets_the_non_error_channel(self):
        d = self.stop("boundary1234", "claude-code_2-1-163_agent")
        self.assertIn("hookSpecificOutput", d)
        self.assertNotIn("decision", d)

    def test_old_claude_code_keeps_decision_block(self):
        d = self.stop("oldchan12345", "claude-code_2-1-150_harness")
        self.assertEqual(d.get("decision"), "block")
        self.assertIn("[gowth-mem:self-review ws=default]", d.get("reason", ""))
        self.assertNotIn("hookSpecificOutput", d)

    def test_unknown_harness_keeps_decision_block(self):
        for sid, agent in (("noagent12345", None), ("custom123456", "my-wrapper")):
            d = self.stop(sid, agent)
            self.assertEqual(d.get("decision"), "block", agent)

    def test_both_channels_carry_the_same_directive(self):
        new = self.stop("samedir11111", "claude-code_2-1-283_harness")
        old = self.stop("samedir22222", "claude-code_2-1-100_harness")
        a = new["hookSpecificOutput"]["additionalContext"].replace("samedir1", "<sid>")
        b = old["reason"].replace("samedir2", "<sid>")
        self.assertEqual(a, b, "the channel changes the envelope, never the directive")

    def test_paused_notice_uses_the_channel_too(self):
        d = self.stop("paused123456", "claude-code_2-1-283_harness", transcript=False)
        ctx = d.get("hookSpecificOutput", {}).get("additionalContext", "")
        self.assertIn("[gowth-mem:review-paused ws=default]", ctx)
        self.assertNotIn("decision", d)

    def test_no_directive_output_is_unchanged(self):
        (self.home / "settings.json").write_text(json.dumps({
            "auto_journal": {"journal_every": 99, "auto_journal_enabled": True},
            "reflection": {"enabled": True, "turn_interval": 99},
            "journal": {"auto_forget_enabled": False},
        }))
        d = self.stop("quiet1234567", "claude-code_2-1-283_harness")
        self.assertEqual(d, {"continue": True, "suppressOutput": True})


if __name__ == "__main__":
    unittest.main()
