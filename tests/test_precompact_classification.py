"""v4.8 Task 9: the PreCompact raw dump keeps human turns only.

Holistic review H2: precompact-flush counted every `type:"user"` record with
text as a turn (77 on the lead transcript; `_capture._classify_record` says 7
human, 33 machine, 11 unconfirmed, 26 non-prompt) and dumped notification
relays and hook feedback as the user's words. The dump now reuses the v4.7.3
classification: human records always, unconfirmed ones only when answered,
machine and non-prompt records never; assistant chunks are capped.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "hooks" / "scripts"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class PrecompactClassificationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gowth_pcc_"))
        os.environ["GOWTH_MEM_HOME"] = str(self.tmp)
        self.tx = self.tmp / "t.jsonl"
        recs = [
            {"type": "user", "uuid": "u1", "origin": {"kind": "human"},
             "message": {"content": "please fix the login bug"}},
            {"type": "assistant", "uuid": "a1",
             "message": {"content": [{"type": "text", "text": "LONG-ANSWER " + "z" * 3000}]}},
            {"type": "user", "uuid": "u2",
             "message": {"content": "<task-notification>teammate done</task-notification>"}},
            {"type": "attachment", "attachment": {"type": "queued_command", "origin": {"kind": "human"},
                                                  "commandMode": "prompt", "prompt": "also update the docs"}},
            {"type": "assistant", "uuid": "a2", "message": {"content": [{"type": "text", "text": "ok"}]}},
            {"type": "user", "uuid": "u3", "message": {"content": "legacy typed prompt without origin"}},
            {"type": "assistant", "uuid": "a3", "message": {"content": [{"type": "text", "text": "sure"}]}},
            {"type": "user", "uuid": "u4", "message": {"content": "unanswered trailing prompt"}},
        ]
        self.tx.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        self.pf = _load("gowth_precompact_flush", SCRIPTS / "precompact-flush.py")

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_user_turn_count_counts_human_and_answered_prompts_only(self):
        self.assertEqual(self.pf.user_turn_count(str(self.tx)), 3)

    def test_dump_keeps_human_words_and_drops_machine_records(self):
        dump = self.pf.extract_recent_turns(str(self.tx))
        self.assertIn("please fix the login bug", dump)
        self.assertIn("also update the docs", dump)
        self.assertIn("legacy typed prompt without origin", dump)
        self.assertNotIn("<task-notification", dump)
        self.assertNotIn("unanswered trailing prompt", dump)

    def test_assistant_chunks_are_capped(self):
        dump = self.pf.extract_recent_turns(str(self.tx))
        block = dump.split("### [assistant]")[1].split("###")[0]
        self.assertLess(len(block), 560)
        self.assertIn("[+", block)
        self.assertIn("LONG-ANSWER", block)


if __name__ == "__main__":
    unittest.main()
