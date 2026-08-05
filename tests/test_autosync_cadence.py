"""Tests for debounced Stop-hook auto-push (v4.3/4.4, sync coverage gap).

Observed on the live vault: 116 uncommitted changes with ahead=0/behind=0 — a whole
session's worth of hook writes (journal, forget deletions, _MAP, handoff) had never
reached the remote. Root cause read from hooks/hooks.json: the ONLY event wired to
auto-sync is PostCompact, so a session that never compacts never pushes. On a
two-machine setup that means machine 2 pulls stale memory indefinitely, and the
longer the drift the worse the eventual conflict.

Fix: the Stop hook (which already runs every turn) may trigger a push, but it must be
(a) debounced so it is not a per-turn network call, (b) detached so it never adds
latency to the hook, and (c) silent on failure — hooks must always exit 0.
"""
import importlib.util
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "hooks" / "scripts"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class AutoSyncCadenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_asc_")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        self.home = Path(self.tmp)
        (self.home / "workspaces" / "demo").mkdir(parents=True, exist_ok=True)
        (self.home / "workspaces" / "demo" / "workspace.json").write_text('{"n":"demo"}')
        # maybe_autosync deliberately refuses to touch a non-repo, so give it one
        (self.home / ".git").mkdir(exist_ok=True)
        self.sync = load_module("gowth_sync_cad", SCRIPTS / "_sync.py")

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _settings(self, **kw):
        (self.home / "settings.json").write_text(json.dumps({"sync": kw}))

    def test_disabled_by_settings_is_a_noop(self):
        self._settings(auto_sync_on_stop=False)
        self.assertFalse(self.sync.maybe_autosync(dry_run=True)["due"])

    def test_due_when_never_synced_before(self):
        self._settings(auto_sync_on_stop=True, min_interval_minutes=30)
        res = self.sync.maybe_autosync(dry_run=True)
        self.assertTrue(res["due"], res)

    def test_not_due_inside_debounce_window(self):
        self._settings(auto_sync_on_stop=True, min_interval_minutes=30)
        state = self.home / "state.json"
        state.write_text(json.dumps({"last_autosync": time.time() - 60}))
        self.assertFalse(self.sync.maybe_autosync(dry_run=True)["due"])

    def test_due_again_after_window_elapses(self):
        self._settings(auto_sync_on_stop=True, min_interval_minutes=30)
        state = self.home / "state.json"
        state.write_text(json.dumps({"last_autosync": time.time() - 31 * 60}))
        self.assertTrue(self.sync.maybe_autosync(dry_run=True)["due"])

    def test_default_is_enabled_with_a_sane_window(self):
        """No settings at all must still sync — the whole point is that users who
        never run /mem-sync stop losing work."""
        res = self.sync.maybe_autosync(dry_run=True)
        self.assertTrue(res["due"])
        self.assertGreaterEqual(res["interval_minutes"], 5)

    def test_records_timestamp_when_it_fires(self):
        self._settings(auto_sync_on_stop=True, min_interval_minutes=30)
        self.sync._record_autosync()
        self.assertFalse(self.sync.maybe_autosync(dry_run=True)["due"])

    def test_timestamp_write_preserves_other_state_keys(self):
        (self.home / "state.json").write_text(json.dumps({"session": {"a": {"turn": 3}}}))
        self.sync._record_autosync()
        d = json.loads((self.home / "state.json").read_text())
        self.assertEqual(d["session"]["a"]["turn"], 3, "must not clobber SRS/session state")
        self.assertIn("last_autosync", d)

    def test_never_raises_on_corrupt_state(self):
        (self.home / "state.json").write_text("{not json")
        self.sync.maybe_autosync(dry_run=True)   # must not raise

    def test_never_raises_without_vault(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.sync.maybe_autosync(dry_run=True)   # must not raise


class StopHookIntegrationTest(unittest.TestCase):
    """The Stop hook must call the debounced sync and still exit 0 with valid JSON."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_asc2_")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        Path(self.tmp, "workspaces", "demo").mkdir(parents=True, exist_ok=True)
        Path(self.tmp, "workspaces", "demo", "workspace.json").write_text('{"n":"demo"}')

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_stop_hook_wires_the_debounced_sync(self):
        src = (SCRIPTS / "auto-journal.py").read_text()
        self.assertIn("maybe_autosync", src,
                      "Stop hook must trigger the debounced push — PostCompact alone "
                      "leaves non-compacting sessions unsynced")

    def test_stop_hook_still_exits_zero_and_emits_json(self):
        import subprocess
        import sys
        r = subprocess.run(
            [sys.executable, str(SCRIPTS / "auto-journal.py")],
            input=json.dumps({"session_id": "s1", "hook_event_name": "Stop"}),
            capture_output=True, text=True,
            env={**os.environ, "GOWTH_MEM_HOME": self.tmp, "GOWTH_WORKSPACE": "demo"},
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.strip())
        json.loads(r.stdout)   # must be valid JSON


if __name__ == "__main__":
    unittest.main()
