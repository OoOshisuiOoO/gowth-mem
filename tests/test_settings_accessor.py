"""v4.8 Task 1: one typed settings accessor, and workspace listing without workspace.json.

Holistic review M1: 13 boolean reads used bare truthiness, so the JSON string
"false" enabled features, and one malformed sibling key reset a whole section
to defaults (re-enabling privacy opt-outs). `_home.setting()` is the single
accessor every knob goes through: per-key fallback, coerced booleans, ints that
fall back on ValueError.

Audit ANOMALY: idol-ai had no workspace.json, so `list_workspaces()` skipped it —
0 index rows, never archived. A workspace is any non-underscore directory that
holds docs/, journal/, a topic folder, or workspace.json.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "hooks" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _home  # type: ignore  # noqa: E402


class SettingAccessorTest(unittest.TestCase):
    def test_string_false_is_false(self):
        s = {"reflection": {"enabled": "false"}}
        self.assertFalse(_home.setting("reflection.enabled", bool, True, settings=s))

    def test_string_true_is_true(self):
        s = {"native": {"enabled": "TRUE"}}
        self.assertTrue(_home.setting("native.enabled", bool, False, settings=s))

    def test_malformed_sibling_keeps_other_keys(self):
        s = {"reflection": {"enabled": False, "turn_interval": "15m"}}
        self.assertFalse(_home.setting("reflection.enabled", bool, True, settings=s))
        self.assertEqual(_home.setting("reflection.turn_interval", int, 15, settings=s), 15)

    def test_int_from_string_digits(self):
        s = {"reflection": {"turn_interval": "20"}}
        self.assertEqual(_home.setting("reflection.turn_interval", int, 15, settings=s), 20)

    def test_missing_path_returns_default(self):
        self.assertEqual(_home.setting("recall.on_prompt_max_chars", int, 2000, settings={}), 2000)

    def test_null_means_default(self):
        s = {"native": {"enabled": None}}
        self.assertTrue(_home.setting("native.enabled", bool, True, settings=s))

    def test_non_dict_section_returns_default(self):
        s = {"reflection": "yes"}
        self.assertTrue(_home.setting("reflection.enabled", bool, True, settings=s))

    def test_unknown_bool_string_returns_default(self):
        self.assertFalse(_home.coerce_bool("maybe", False))
        self.assertTrue(_home.coerce_bool("maybe", True))

    def test_reads_vault_settings_when_no_snapshot(self):
        tmp = tempfile.mkdtemp(prefix="gowth_setting_")
        try:
            os.environ["GOWTH_MEM_HOME"] = tmp
            (Path(tmp) / "settings.json").write_text('{"gate": {"english_only": "false"}}')
            self.assertFalse(_home.setting("gate.english_only", bool, True))
        finally:
            os.environ.pop("GOWTH_MEM_HOME", None)
            shutil.rmtree(tmp, ignore_errors=True)


class ListWorkspacesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_ws_")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        root = Path(self.tmp) / "workspaces"
        (root / "a").mkdir(parents=True)
        (root / "a" / "workspace.json").write_text('{"name": "a"}')
        (root / "b" / "docs").mkdir(parents=True)                 # no workspace.json
        (root / "_archive" / "docs").mkdir(parents=True)          # underscore: excluded
        (root / "c").mkdir(parents=True)                          # empty: excluded
        (root / "d" / "topic").mkdir(parents=True)
        (root / "d" / "topic" / "00-README.md").write_text("# t\n")
        (root / "e" / "journal").mkdir(parents=True)

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dirs_without_workspace_json_are_listed_when_they_hold_memory(self):
        self.assertEqual(_home.list_workspaces(), ["a", "b", "d", "e"])

    def test_memory_is_reserved(self):
        self.assertTrue(_home.is_reserved("memory"))
        self.assertIn("memory", _home.RESERVED_SUBDIRS)


if __name__ == "__main__":
    unittest.main()
