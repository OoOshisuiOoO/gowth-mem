"""v4.8 Task 1 (step 5): every boolean knob reads through `_home.setting`.

Holistic review M1 listed 13 boolean reads that used bare truthiness, so the
JSON string "false" turned features ON. Each test below writes the knob as the
string "false" into a scratch vault and asserts the feature is OFF through the
public function that consumes it — never through the accessor alone.
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "hooks" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _home  # type: ignore  # noqa: E402


class _VaultCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_knob_")
        self.home = Path(self.tmp)
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        os.environ.pop("GOWTH_WORKSPACE", None)
        self.ws = "w"
        self.wsd = self.home / "workspaces" / self.ws
        (self.wsd / "docs").mkdir(parents=True)
        (self.wsd / "journal").mkdir(parents=True)
        (self.wsd / "workspace.json").write_text('{"name": "w"}')
        (self.home / "shared").mkdir()

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def settings(self, obj: dict) -> None:
        obj = {"layout_version": 3, **obj}
        (self.home / "settings.json").write_text(json.dumps(obj))

    def run_script(self, name: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPTS / name), *args],
            capture_output=True, text=True, timeout=120,
            env={**os.environ, "GOWTH_MEM_HOME": self.tmp},
        )


class SyncKnobTest(_VaultCase):
    def test_auto_sync_on_stop_string_false_is_off(self):
        import _sync  # type: ignore
        (self.home / ".git").mkdir()
        self.settings({"sync": {"auto_sync_on_stop": "false"}})
        self.assertFalse(_sync.maybe_autosync(dry_run=True)["due"])


class ForgetKnobTest(_VaultCase):
    def test_auto_archive_enabled_string_false_keeps_old_aspects(self):
        topic = self.wsd / "t"
        topic.mkdir()
        (topic / "00-README.md").write_text("---\nslug: t\ntitle: T\n---\n# T\n")
        names = [f"2025-01-0{i}-note.md" for i in range(1, 6)]
        for n in names:
            (topic / n).write_text(f"---\nslug: t-note\n---\n- [exp] old aspect body {n} kept for the test\n")
        self.settings({"topic_layout": {"auto_archive_enabled": "false"},
                       "journal": {"raw_ttl_days": 7}})
        r = self.run_script("_forget.py", "--all-workspaces")
        self.assertEqual(r.returncode, 0, r.stderr)
        for n in names:
            self.assertTrue((topic / n).is_file(), f"{n} was archived although the knob is off")

    def _old_journal(self) -> Path:
        # journal TTL is judged by mtime (v3.6), so age the file 30 days
        p = self.wsd / "journal" / "2020-01-01.md"
        p.write_text("# 2020-01-01\n\n- [exp] Some lesson worth keeping for later reference here\n")
        old = 1_600_000_000
        os.utime(p, (old, old))
        return p

    def test_salvage_true_creates_salvage_file(self):
        # positive control: the fixture really exercises the salvage path
        self._old_journal()
        self.settings({"journal": {"salvage": True, "raw_ttl_days": 7}})
        r = self.run_script("_forget.py", "--all-workspaces")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.wsd / "journal" / "_salvage.md").exists(), r.stdout)

    def test_salvage_string_false_skips_salvage(self):
        self._old_journal()
        self.settings({"journal": {"salvage": "false", "raw_ttl_days": 7}})
        r = self.run_script("_forget.py", "--all-workspaces")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.wsd / "journal" / "2020-01-01.md").exists(), "journal not archived")
        self.assertFalse((self.wsd / "journal" / "_salvage.md").exists(),
                         "salvage ran although journal.salvage is the string false")


class CaptureKnobTest(_VaultCase):
    def test_capture_thinking_string_false_omits_thinking(self):
        import _capture  # type: ignore
        tp = self.home / "transcript.jsonl"
        recs = [
            {"type": "user", "uuid": "u1", "timestamp": "2026-09-28T01:00:00Z",
             "origin": {"kind": "human"},
             "message": {"role": "user", "content": "please explain the cache design"}},
            {"type": "assistant", "uuid": "a1", "timestamp": "2026-09-28T01:00:05Z",
             "message": {"role": "assistant", "content": [
                 {"type": "thinking", "thinking": "SECRET-THOUGHT planning the answer"},
                 {"type": "text", "text": "The cache is keyed by path."}]}},
        ]
        tp.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        settings = {"reflection": {"capture_thinking": "false"}}
        ok = _capture.capture_turn(str(tp), self.ws, "sid12345678", 1, settings)
        self.assertTrue(ok)
        logs = list((self.wsd / "journal" / "sessions").glob("*.md"))
        self.assertEqual(len(logs), 1)
        text = logs[0].read_text()
        self.assertIn("cache", text)
        self.assertNotIn("SECRET-THOUGHT", text)


class GateKnobTest(_VaultCase):
    def test_english_only_string_false_accepts_vietnamese(self):
        import _gate  # type: ignore
        self.settings({"gate": {"english_only": "false"}})
        r = _gate.evaluate("[exp] Lỗi kết nối cơ sở dữ liệu vì thiếu biến môi trường PGHOST trong container")
        self.assertTrue(r.ok, r.reason)

    def test_gate_enabled_string_false_skips_gate_on_append_entry(self):
        import _topic  # type: ignore
        self.settings({"gate": {"enabled": "false"}})
        (self.wsd / "misc").mkdir()
        (self.wsd / "misc" / "00-README.md").write_text("---\nslug: misc\ntitle: Misc\n---\n# Misc\n")
        path, status = _topic.append_entry_status("[exp] tiny", ws=self.ws,
                                                  settings={"gate": {"enabled": "false"}})
        self.assertEqual(status, "written", "the gate ran although gate.enabled is the string false")

    def test_gate_enabled_string_false_skips_gate_on_append_lesson(self):
        import _lesson  # type: ignore
        self.settings({"gate": {"enabled": "false"}})
        (self.wsd / "misc").mkdir()
        (self.wsd / "misc" / "00-README.md").write_text("---\nslug: misc\ntitle: Misc\n---\n# Misc\n")
        path, status = _lesson.append_lesson_status("x", "y", "z", "w", ws=self.ws)
        self.assertEqual(status, "written", "the lesson gate ran although gate.enabled is the string false")


class TagsKnobTest(unittest.TestCase):
    def test_tags_enabled_string_false(self):
        import _tags  # type: ignore
        self.assertFalse(_tags.tags_enabled({"tags": {"enabled": "false"}}))


class IndexKnobTest(_VaultCase):
    def test_index_archive_string_false_skips_archive_rows(self):
        topic = self.wsd / "t"
        topic.mkdir()
        (topic / "00-README.md").write_text("---\nslug: t\ntitle: T\n---\n# T\n\n- [ref] live entry about sqlite indexing. Source: test\n")
        arch = self.home / ".archive" / "journal" / self.ws
        arch.mkdir(parents=True)
        with gzip.open(arch / "2020-01-01-1234567890.md.gz", "wt") as f:
            f.write("# old journal\n\narchived text about sqlite indexing\n")
        self.settings({"retrieval": {"index_archive": "false"}})
        r = self.run_script("_index.py")
        self.assertEqual(r.returncode, 0, r.stderr)
        db = sqlite3.connect(str(self.home / "index.db"))
        n = db.execute("SELECT count(*) FROM chunks WHERE path LIKE '.archive/%'").fetchone()[0]
        db.close()
        self.assertEqual(n, 0, "archive rows indexed although retrieval.index_archive is the string false")


class WorkspaceKnobTest(_VaultCase):
    def _map(self) -> Path:
        proj = (self.home / "proj").resolve()
        (proj / "x").mkdir(parents=True)
        (self.home / "config.json").write_text(json.dumps(
            {"workspace_map": {f"{proj}/**": "mapped"}, "active_workspace": "fallback"}))
        return proj / "x"

    def test_auto_detect_default_uses_workspace_map(self):
        cwd = self._map()
        self.settings({})
        self.assertEqual(_home.active_workspace(cwd), "mapped")

    def test_auto_detect_string_false_ignores_workspace_map(self):
        cwd = self._map()
        self.settings({"workspace": {"auto_detect_from_cwd": "false"}})
        self.assertEqual(_home.active_workspace(cwd), "fallback")


if __name__ == "__main__":
    unittest.main()
