#!/usr/bin/env python3
"""v4.7.1: pin the delegation dispatch contract across its prose copies.

The contract ships in three places — the hook's reason builders
(hooks/scripts/auto-journal.py), the teammate template
(templates/auto-journal-instructions.md), and the judge rubric
(templates/self-review-instructions.md). They drifted silently in v4.7
(missing sentinel in the template quote, dangling "given in the hook reason"
for a fresh subagent, identity-keyed SKIP guard). These tests make any future
drift a test failure instead of a live incident.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
HOOK_SRC = (REPO_ROOT / "hooks" / "scripts" / "auto-journal.py").read_text(encoding="utf-8")
TEAMMATE = (REPO_ROOT / "templates" / "auto-journal-instructions.md").read_text(encoding="utf-8")
JUDGE = (REPO_ROOT / "templates" / "self-review-instructions.md").read_text(encoding="utf-8")

SENTINEL = "never dispatch further subagents"


def _norm(text: str) -> str:
    """Whitespace-normalize so line-wrapped prose still matches."""
    return re.sub(r"\s+", " ", text)


class TestAntiRecursionSentinel(unittest.TestCase):
    def test_sentinel_in_hook_reason_builder(self):
        self.assertIn(SENTINEL, HOOK_SRC,
                      "the teammate prompt built by _build_reason must carry the sentinel")

    def test_sentinel_in_teammate_template_quote(self):
        self.assertIn(SENTINEL, _norm(TEAMMATE),
                      "the template's quoted dispatch prompt must carry the sentinel "
                      "(a main session composing from the template instead of the hook "
                      "reason must not drop the recursion guard)")

    def test_sentinel_in_judge_rubric(self):
        self.assertIn(SENTINEL, _norm(JUDGE))


class TestTeammateTemplateContract(unittest.TestCase):
    def test_names_the_write_interface(self):
        """v4.7 switched the executor to a fresh subagent with no bootstrap —
        the template must NAME the gate/tags/dedup/reindex write path or the
        teammate hand-writes topic files and bypasses all of it."""
        self.assertIn("_topic.py --append", TEAMMATE)
        self.assertIn("GOWTH_MEM_HOME=", TEAMMATE,
                      "vault-root env prefix required on env-asymmetric machines")

    def test_moc_step_carries_env_prefix(self):
        norm = _norm(TEAMMATE)
        self.assertIn("GOWTH_MEM_HOME=<vault-root> python3 <plugin-scripts>/_moc.py", norm,
                      "MOC rebuild without the env prefix lands in the wrong vault")

    def test_dispatch_quote_has_no_dangling_reference(self):
        self.assertNotIn("given in the\n   hook reason.", TEAMMATE)
        self.assertIn("absolute session-log path", _norm(TEAMMATE),
                      "the quoted prompt must demand ABSOLUTE paths — a fresh "
                      "subagent cannot resolve 'given in the hook reason'")


class TestJudgeRubricContract(unittest.TestCase):
    def test_skip_guard_keys_on_payload_not_identity(self):
        norm = _norm(JUDGE)
        self.assertIn("You ARE the dispatched judge if your prompt hands you", norm,
                      "the SKIP guard must be decidable from the payload — no prompt "
                      "is required to state 'you are the judge'")

    def test_pass_list_includes_scores_path(self):
        self.assertIn("_scores.md", _norm(JUDGE).split("## 0b.")[0],
                      "§0's pass-list must forward the absolute scores path")

    def test_has_anchors_section(self):
        self.assertIn("0c. Anchors", JUDGE)
        self.assertIn("never assume `~/.gowth-mem/`", _norm(JUDGE))

    def test_review_append_goes_through_locked_cli(self):
        self.assertIn("--append-review", JUDGE,
                      "a raw Write/Edit of the session log races capture's "
                      "read-modify-write — the rubric must route through the locked CLI")

    def test_backlog_jsonl_mode_documented(self):
        self.assertIn("Backlog mode", JUDGE,
                      "/mem-review-backlog judges receive raw JSONL with zero "
                      "'## turn' blocks — without this mode they floor-skip everything")


class TestSettingsExamplePrivacy(unittest.TestCase):
    def test_example_does_not_ship_capture_enabled(self):
        """F1 pin: an explicit capture_enabled in the example is copied verbatim
        by /mem-install and permanently defeats the reflection.enabled: false
        privacy opt-out on every vault scaffolded from it."""
        example = json.loads(
            (REPO_ROOT / "templates" / "dot-gowth-mem" / "settings.example.v3.json")
            .read_text(encoding="utf-8"))
        self.assertNotIn("capture_enabled", example.get("reflection", {}),
                         "capture_enabled must stay ABSENT from the example so the "
                         "default chain (reflection.enabled) governs")


if __name__ == "__main__":
    unittest.main()
