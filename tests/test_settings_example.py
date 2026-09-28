"""v4.8 Task 11: the settings example documents exactly the keys the code reads.

Holistic review M2: 51 of 82 documented leaves were read by nothing (recall.*,
compression.*, contradictions.*, moc.*, slug.*, embedding.*, migration.*, …).
Both directions are pinned: every documented leaf is read somewhere under
hooks/scripts, and every key the code reads is documented (except the two
deliberate omissions).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "hooks" / "scripts"
EXAMPLE = ROOT / "templates" / "dot-gowth-mem" / "settings.example.v3.json"
sys.path.insert(0, str(SCRIPTS))

# Keys the code reads (holistic review Q2 + v4.8 additions).
CODE_KEYS = [
    "layout_version",
    "auto_journal.journal_every", "auto_journal.auto_journal_enabled",
    "journal.auto_forget_enabled", "journal.raw_ttl_days", "journal.max_bytes", "journal.salvage",
    "reflection.enabled", "reflection.turn_interval", "reflection.min_review_turns",
    "reflection.max_prompt_chars", "reflection.max_thinking_chars", "reflection.capture_thinking",
    "gate.enabled", "gate.strict", "gate.english_only",
    "tags.enabled", "tags.max_per_entry", "tags.max_frontmatter",
    "topic_routing.min_keyword_overlap", "topic_routing.default_topic",
    "topic_layout.archive_threshold_days", "topic_layout.auto_archive_enabled",
    "workspace.auto_detect_from_cwd", "workspace.default",
    "retrieval.index_archive",
    "sync.auto_sync_on_stop", "sync.min_interval_minutes",
    "doctor.auto_heal",
    "native.enabled",
    "recall.on_prompt_enabled", "recall.on_prompt_max_entries", "recall.on_prompt_max_chars",
    "recall.on_prompt_min_terms", "recall.on_prompt_score_threshold", "recall.on_prompt_prompt_cap",
    "memfile.max_lines", "memfile.max_chars",
]
# Deliberately undocumented: reflection.capture_enabled (an explicit true in the
# example would defeat the enabled:false privacy opt-out) and the legacy
# top-level journal_every / auto_journal_enabled.


def _leaves(obj, prefix=""):
    out = []
    for k, v in obj.items():
        if k.startswith("_"):
            continue
        path = f"{prefix}{k}"
        if isinstance(v, dict):
            out.extend(_leaves(v, path + "."))
        else:
            out.append(path)
    return out


class SettingsExampleTest(unittest.TestCase):
    def setUp(self):
        self.example = json.loads(EXAMPLE.read_text())
        self.sources = "\n".join(p.read_text(encoding="utf-8", errors="ignore")
                                 for p in SCRIPTS.glob("*") if p.suffix in (".py", ".sh"))

    def test_example_keys_are_all_read_by_code(self):
        unread = []
        for leaf in _leaves(self.example):
            last = leaf.split(".")[-1]
            if not re.search(r"['\"]" + re.escape(last) + r"['\"]|\." + re.escape(last) + r"\b", self.sources):
                unread.append(leaf)
        self.assertEqual(unread, [], f"documented but read by nothing: {unread}")

    def test_code_keys_are_all_documented(self):
        leaves = set(_leaves(self.example))
        missing = [k for k in CODE_KEYS if k not in leaves]
        self.assertEqual(missing, [], f"read by code but absent from the example: {missing}")

    def test_no_dead_sections(self):
        for dead in ("compression", "contradictions", "moc", "slug", "embedding", "migration",
                     "conflict_resolution", "context_budget"):
            self.assertNotIn(dead, self.example, f"dead section {dead} still documented")

    def test_archive_threshold_matches_code_default(self):
        self.assertEqual(self.example["topic_layout"]["archive_threshold_days"], 90)

    def test_example_does_not_ship_capture_enabled(self):
        self.assertNotIn("capture_enabled", self.example.get("reflection", {}))


class MemfileSettingsTest(unittest.TestCase):
    def test_memfile_budget_keys_are_honoured(self):
        tmp = tempfile.mkdtemp(prefix="gowth_mfset_")
        try:
            os.environ["GOWTH_MEM_HOME"] = tmp
            wsd = Path(tmp) / "workspaces" / "demo"
            (wsd / "docs").mkdir(parents=True)
            (wsd / "workspace.json").write_text('{"name": "demo"}')
            (wsd / "docs" / "handoff.md").write_text(
                "## 2026-09-13 s\n" + "".join(f"- host:mac 2026-09-13 [done] item {i}\n" for i in range(80)))
            (Path(tmp) / "settings.json").write_text(json.dumps({"memfile": {"max_lines": 40, "max_chars": 3000}}))
            import _memfile  # type: ignore
            self.assertTrue(_memfile.write("demo"))
            block, _ = _memfile.split((wsd / "memory" / "MEMORY.md").read_text())
            self.assertLessEqual(block.count("\n"), 40)
            self.assertLessEqual(len(block), 3000)
        finally:
            os.environ.pop("GOWTH_MEM_HOME", None)
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
