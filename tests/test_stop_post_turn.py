"""v4.8 Task 8: what the Stop hook does after every real turn.

- MEMORY.md's managed block is regenerated when its sources changed (hash-gated,
  so an unchanged block never rewrites the file);
- `memory/*.md` written by Claude Code's auto memory are privacy-sanitized before
  they can sync;
- the index is refreshed incrementally;
- settings.json is parsed once per Stop;
- the daily slot starts one detached full reindex per day.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "hooks" / "scripts"
sys.path.insert(0, str(SCRIPTS))

NEW_CC = "claude-code_2-1-283_harness"
HANDOFF = "## 2026-09-13 s\n- host:mac 2026-09-13 [doing] first state line\n"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class _StopCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gowth_stop_"))
        os.environ["GOWTH_MEM_HOME"] = str(self.tmp)
        os.environ.pop("GOWTH_WORKSPACE", None)
        self.ws = "default"
        self.wsd = self.tmp / "workspaces" / self.ws
        (self.wsd / "journal").mkdir(parents=True)
        (self.wsd / "docs").mkdir()
        (self.wsd / "memory").mkdir()
        (self.wsd / "t").mkdir()
        (self.wsd / "workspace.json").write_text(json.dumps({"name": "default"}))
        (self.wsd / "docs" / "handoff.md").write_text(HANDOFF)
        (self.wsd / "t" / "00-README.md").write_text("---\nslug: t\ntitle: T\nlast_touched: 2026-09-10\n---\n# T\n\nAbout T.\n")
        (self.wsd / "t" / "2026-09-10-a.md").write_text("---\nslug: t-a\n---\n- [exp] seeded entry about wombats here\n")
        (self.tmp / "shared").mkdir()
        (self.tmp / "config.json").write_text(json.dumps({"active_workspace": "default"}))
        (self.tmp / "settings.json").write_text(json.dumps({
            "layout_version": 3,
            "auto_journal": {"journal_every": 100, "auto_journal_enabled": True},
            "reflection": {"enabled": True, "turn_interval": 100, "min_review_turns": 100},
        }))
        self.tx = self.tmp / "transcript.jsonl"
        self.tx.write_text("\n".join([
            json.dumps({"type": "user", "message": {"content": "implement the capture module"}}),
            json.dumps({"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Done — wrote _capture.py"}]}}),
        ]) + "\n")
        self.env = {**os.environ, "GOWTH_MEM_HOME": str(self.tmp), "AI_AGENT": NEW_CC}
        r = subprocess.run([sys.executable, str(SCRIPTS / "_index.py")], capture_output=True, text=True, env=self.env)
        assert r.returncode == 0, r.stderr

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def stop(self, sid: str = "stopsess1") -> subprocess.CompletedProcess:
        r = subprocess.run([sys.executable, str(SCRIPTS / "auto-journal.py")],
                           input=json.dumps({"session_id": sid, "transcript_path": str(self.tx),
                                             "hook_event_name": "Stop", "stop_hook_active": False}),
                           capture_output=True, text=True, env=self.env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        return r

    def memfile(self) -> Path:
        return self.wsd / "memory" / "MEMORY.md"


class PostTurnTest(_StopCase):
    def test_memfile_refreshed_when_handoff_changed(self):
        import _memfile  # type: ignore
        self.assertTrue(_memfile.write(self.ws))
        h = self.wsd / "docs" / "handoff.md"
        h.write_text(HANDOFF + "- host:mac 2026-09-14 [next] brand-new state line\n")
        fut = time.time() + 5
        os.utime(h, (fut, fut))
        self.stop()
        self.assertIn("brand-new state line", self.memfile().read_text())

    def test_memfile_untouched_when_block_unchanged(self):
        import _memfile  # type: ignore
        _memfile.write(self.ws)
        old = 1_600_000_000
        os.utime(self.memfile(), (old, old))
        self.stop()
        self.assertEqual(int(self.memfile().stat().st_mtime), old)

    def test_memory_note_is_sanitized_before_sync(self):
        note = self.wsd / "memory" / "note.md"
        note.write_text("# note\n\ntoken glpat-AbCdEfGhIjKlMnOpQrSt must not sync\n")
        self.stop()
        text = note.read_text()
        self.assertNotIn("glpat-AbCdEfGhIjKlMnOpQrSt", text)
        self.assertIn("[REDACTED", text)
        self.assertIn("must not sync", text)

    def test_new_aspect_is_indexed_at_stop(self):
        (self.wsd / "t" / "2026-09-11-b.md").write_text("---\nslug: t-b\n---\n- [exp] new entry about numbats here\n")
        self.stop()
        db = sqlite3.connect(str(self.tmp / "index.db"))
        try:
            n = db.execute("SELECT count(*) FROM chunks WHERE path LIKE '%2026-09-11-b.md'").fetchone()[0]
        finally:
            db.close()
        self.assertGreaterEqual(n, 1)


class InProcessTest(_StopCase):
    def _module(self):
        return _load("gowth_auto_journal_post", SCRIPTS / "auto-journal.py")

    def test_settings_parsed_once_per_stop(self):
        mod = self._module()
        os.environ["AI_AGENT"] = NEW_CC
        calls = []
        real = mod.read_settings

        def counting():
            calls.append(1)
            return real()

        mod.read_settings = counting
        old_stdin, old_stdout = sys.stdin, sys.stdout
        sys.stdin = io.StringIO(json.dumps({"session_id": "inproc01", "transcript_path": str(self.tx),
                                            "hook_event_name": "Stop", "stop_hook_active": False}))
        sys.stdout = io.StringIO()   # the hook prints its envelope; keep the runner's output clean
        try:
            rc = mod.main()
        finally:
            sys.stdin, sys.stdout = old_stdin, old_stdout
            os.environ.pop("AI_AGENT", None)
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 1, "settings.json must be parsed once per Stop")

    def test_daily_slot_spawns_one_full_reindex(self):
        mod = self._module()
        spawned = []

        class FakePopen:
            def __init__(self, args, **kw):
                spawned.append(list(args))

        import types
        real_sp = mod.subprocess
        # a private namespace so the patch never leaks into the shared subprocess module
        mod.subprocess = types.SimpleNamespace(Popen=FakePopen, run=real_sp.run,
                                               DEVNULL=real_sp.DEVNULL,
                                               TimeoutExpired=real_sp.TimeoutExpired)
        try:
            settings = json.loads((self.tmp / "settings.json").read_text())
            mod._run_forget_daily(settings)
            mod._run_forget_daily(settings)
        finally:
            mod.subprocess = real_sp
        full = [a for a in spawned if "--full" in a]
        self.assertEqual(len(full), 1, spawned)
        state = json.loads((self.tmp / "state.json").read_text())
        self.assertEqual(state.get("index_last_full"), datetime.now().strftime("%Y-%m-%d"))


if __name__ == "__main__":
    unittest.main()
