"""v4.8 Task 8: the review directive dictates the judge prompt verbatim.

Holistic review H1: the journal directive gives the teammate prompt word for
word (628-char dispatches); the review directive gave none, so the main
session loaded the user's session-insights skill, wrote its own 2k-char judge
prompt and relayed a 12.5k-char report — ~32k main-context chars per fire
against a 4.5k contract. The judge now writes the full report into the
session log via `--append-review` and returns exactly 3 lines.
"""
from __future__ import annotations

import importlib.util
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS = REPO_ROOT / "hooks" / "scripts"
HOOK_SRC = (SCRIPTS / "auto-journal.py").read_text(encoding="utf-8")
JUDGE = (REPO_ROOT / "templates" / "self-review-instructions.md").read_text(encoding="utf-8")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text)


class JudgeDirectiveTest(unittest.TestCase):
    def setUp(self):
        self.aj = _load("gowth_auto_journal_judge", SCRIPTS / "auto-journal.py")

    def _reason(self, two_logs: bool = False) -> str:
        base = Path("/Users/someone/.gowth-mem/workspaces/personal/journal/sessions")
        log = base / "2026-09-28-1c16482b.md"
        prev = base / "2026-09-27-1c16482b.md" if two_logs else None
        return self.aj._build_review_reason("personal", 15, log, prev)

    def test_prompt_is_verbatim_with_sentinel(self):
        reason = self._reason()
        self.assertIn("You are the dispatched gowth-mem judge", reason)
        self.assertIn("never dispatch further subagents", reason)
        self.assertIn("self-review-instructions.md", reason)

    def test_report_goes_to_the_log_and_reply_is_three_lines(self):
        reason = self._reason()
        self.assertIn("--append-review", reason)
        self.assertIn("exactly 3 lines", reason)
        self.assertIn("do NOT load review skills in the main context", reason)

    def test_directive_stays_small_with_two_logs(self):
        self.assertLess(len(self._reason(two_logs=True)), 1500)
        self.assertLess(len(self._reason()), 1500)

    def test_template_carries_the_three_line_contract_top_and_bottom(self):
        head = "\n".join(JUDGE.splitlines()[:8])
        self.assertIn("exactly 3 lines", _norm(head))
        tail = JUDGE[JUDGE.index("## 6."):]
        self.assertIn("exactly 3 lines", _norm(tail))
        self.assertIn("--append-review", _norm(JUDGE))

    def test_template_quotes_the_verbatim_prompt(self):
        self.assertIn("You are the dispatched gowth-mem judge", _norm(JUDGE))


class StopOutputClampTest(unittest.TestCase):
    def test_stop_output_is_clamped_in_both_envelopes(self):
        aj = _load("gowth_auto_journal_clamp", SCRIPTS / "auto-journal.py")
        import os
        big = "x\n" * 12000
        for agent in ("claude-code_2-1-283_harness", "claude-code_2-1-100_harness"):
            os.environ["AI_AGENT"] = agent
            try:
                out = aj._stop_output(big)
            finally:
                os.environ.pop("AI_AGENT", None)
            payload = out.get("hookSpecificOutput", {}).get("additionalContext") or out.get("reason")
            self.assertIsNotNone(payload, out)
            self.assertLess(len(payload), 9000)
            self.assertTrue(payload.endswith("[gowth-mem: truncated to fit the host limit]"))


if __name__ == "__main__":
    unittest.main()
