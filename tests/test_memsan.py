"""Review C3 (v4.8): Claude-written `<ws>/memory/*.md` must be privacy-sanitized
BEFORE any path that can sync them, in every workspace.

The v4.8 first cut kept ONE global stamp (`memory_sanitized_at`) but scanned
only the Stop's own workspace — a file written in workspace B was skipped
forever once any other workspace's Stop ran — and it ran AFTER the detached
push was spawned; PreCompact --commit-only, PostCompact and /mem-sync never
sanitized at all. `_memsan.sanitize_memory_files()` now scans every
workspace with a per-file content hash and runs from the Stop hook (before
autosync) and from every commit path (before `git add -A`).

Per CLAUDE.md the privacy tests go through the REAL entry points with the
real hostile input, assert the positive marker, and prove idempotence.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "hooks" / "scripts"
sys.path.insert(0, str(SCRIPTS))

NEW_CC = "claude-code_2-1-283_harness"
SECRET = "glpat-" + "A1b2C3d4E5f6G7h8I9j0"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@x", "GIT_CONFIG_NOSYSTEM": "1"}


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class _Vault(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gowth_memsan_"))
        os.environ["GOWTH_MEM_HOME"] = str(self.tmp)
        os.environ.pop("GOWTH_WORKSPACE", None)
        for ws in ("personal", "trade"):
            d = self.tmp / "workspaces" / ws
            (d / "memory").mkdir(parents=True)
            (d / "docs").mkdir()
            (d / "journal").mkdir()
            (d / "workspace.json").write_text(json.dumps({"name": ws}))
            (d / "docs" / "handoff.md").write_text(f"## 2026-09-13 s\n- host:mac 2026-09-13 [doing] {ws} work\n")
        (self.tmp / "shared").mkdir()
        (self.tmp / "config.json").write_text(json.dumps({"active_workspace": "personal"}))
        (self.tmp / "settings.json").write_text(json.dumps({
            "layout_version": 3,
            "auto_journal": {"journal_every": 100, "auto_journal_enabled": True},
            "reflection": {"enabled": True, "turn_interval": 100, "min_review_turns": 100},
            "retrieval": {"daily_full_reindex": False},
            "sync": {"auto_sync_on_stop": False},
        }))
        self.trade_note = self.tmp / "workspaces" / "trade" / "memory" / "project-ci.md"
        self.trade_note.write_text(f"# ci\n\nCI token is {SECRET} — rotate quarterly\n")
        self.env = {**os.environ, "GOWTH_MEM_HOME": str(self.tmp), "AI_AGENT": NEW_CC, **GIT_ENV}
        self.env.pop("GOWTH_WORKSPACE", None)

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-c", "init.defaultBranch=main", "-C", str(self.tmp), *args],
                              capture_output=True, text=True, env=self.env, check=check)

    def _run(self, script: str, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
        r = subprocess.run([sys.executable, str(SCRIPTS / script), *args], input=stdin,
                           capture_output=True, text=True, env=self.env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        return r

    def _assert_redacted(self, text: str):
        self.assertNotIn(SECRET, text)
        self.assertIn("[REDACTED", text)
        self.assertIn("rotate quarterly", text)


class StopHookTest(_Vault):
    def test_stop_in_another_workspace_sanitizes_every_memory_dir(self):
        tx = self.tmp / "transcript.jsonl"
        tx.write_text("\n".join([
            json.dumps({"type": "user", "message": {"content": "note the ci token somewhere"}}),
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}}),
        ]) + "\n")
        # the ACTIVE workspace is personal; the secret sits in trade's memory dir
        (self.tmp / "workspaces" / "personal" / "memory" / "note.md").write_text("harmless\n")
        self._run("auto-journal.py", stdin=json.dumps({
            "session_id": "memsan01", "transcript_path": str(tx),
            "hook_event_name": "Stop", "stop_hook_active": False}))
        self._assert_redacted(self.trade_note.read_text())

    def test_post_turn_runs_before_autosync(self):
        mod = _load("gowth_auto_journal_memsan", SCRIPTS / "auto-journal.py")
        order: list = []
        mod._autosync = lambda: order.append("autosync")
        real_post = mod._post_turn
        mod._post_turn = lambda ws, settings: (order.append("post_turn"), real_post(ws, settings))
        tx = self.tmp / "transcript.jsonl"
        tx.write_text("\n".join([
            json.dumps({"type": "user", "message": {"content": "implement the capture module"}}),
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}}),
        ]) + "\n")
        os.environ["AI_AGENT"] = NEW_CC
        old_stdin, old_stdout = sys.stdin, sys.stdout
        sys.stdin = io.StringIO(json.dumps({"session_id": "memsan02", "transcript_path": str(tx),
                                            "hook_event_name": "Stop", "stop_hook_active": False}))
        sys.stdout = io.StringIO()
        try:
            rc = mod.main()
        finally:
            sys.stdin, sys.stdout = old_stdin, old_stdout
            os.environ.pop("AI_AGENT", None)
        self.assertEqual(rc, 0)
        self.assertEqual(order, ["post_turn", "autosync"],
                         "the sanitizer must run before the detached push is spawned")


class CommitPathTest(_Vault):
    def setUp(self):
        super().setUp()
        self._git("init", "-q")
        # the vault's own ignores (state.json, index.db, .locks) as /mem-install writes them
        (self.tmp / ".gitignore").write_text("state.json\nindex.db\n.locks/\nconfig.json\n.archive/\n.backup/\n")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "base")
        # a NEW secret written by Claude after the base commit
        self.trade_note.write_text(f"# ci\n\nCI token is {SECRET} — rotate quarterly\nsecond line\n")

    def test_commit_only_sanitizes_before_staging(self):
        self._run("auto-sync.py", "--commit-only", "--quiet")
        shown = self._git("show", "HEAD:workspaces/trade/memory/project-ci.md").stdout
        self._assert_redacted(shown)
        self._assert_redacted(self.trade_note.read_text())
        self.assertIn("second line", shown)

    def test_mem_sync_push_only_sanitizes_before_staging(self):
        bare = self.tmp.parent / (self.tmp.name + "-remote.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True, env=self.env)
        self.addCleanup(shutil.rmtree, bare, True)
        (self.tmp / "config.json").write_text(json.dumps({"active_workspace": "personal",
                                                          "remote": str(bare), "branch": "main"}))
        self._git("remote", "add", "origin", str(bare))
        self._run("_sync.py", "--push-only")
        shown = self._git("show", "HEAD:workspaces/trade/memory/project-ci.md").stdout
        self._assert_redacted(shown)
        pushed = subprocess.run(["git", "-C", str(bare), "show", "main:workspaces/trade/memory/project-ci.md"],
                                capture_output=True, text=True, env=self.env, check=True).stdout
        self._assert_redacted(pushed)


class SanitizerTest(_Vault):
    def test_three_passes_are_idempotent_and_hash_gated(self):
        import _memsan  # type: ignore
        r1 = _memsan.sanitize_memory_files()
        self.assertIn("workspaces/trade/memory/project-ci.md", r1["sanitized"])
        self._assert_redacted(self.trade_note.read_text())
        after1 = self.trade_note.read_bytes()
        m1 = self.trade_note.stat().st_mtime_ns
        r2 = _memsan.sanitize_memory_files()
        r3 = _memsan.sanitize_memory_files()
        self.assertEqual(r2["sanitized"], [])
        self.assertEqual(r3["sanitized"], [])
        self.assertEqual(self.trade_note.read_bytes(), after1)
        self.assertEqual(self.trade_note.stat().st_mtime_ns, m1, "an unchanged file must not be rewritten")
        state = json.loads((self.tmp / "state.json").read_text())
        self.assertIn("workspaces/trade/memory/project-ci.md", state.get("memory_sanitized", {}))
        # a file rewritten with a new secret is caught again (hash changed)
        self.trade_note.write_text(f"new token {SECRET}\n")
        r4 = _memsan.sanitize_memory_files()
        self.assertIn("workspaces/trade/memory/project-ci.md", r4["sanitized"])
        self.assertNotIn(SECRET, self.trade_note.read_text())

    def test_memfile_write_waits_for_the_memfile_lock(self):
        import _memsan  # type: ignore
        from _lock import file_lock  # type: ignore
        mf = self.tmp / "workspaces" / "trade" / "memory" / "MEMORY.md"
        mf.write_text(f"<!-- gowth-mem:begin ws=trade -->\nx\n<!-- gowth-mem:end -->\n- token {SECRET}\n")
        with file_lock("memfile-trade", timeout=1.0):
            r = _memsan.sanitize_memory_files(lock_timeout=0.2)
        self.assertNotIn("workspaces/trade/memory/MEMORY.md", r["sanitized"])
        self.assertIn(SECRET, mf.read_text(), "must not write MEMORY.md while another writer holds its lock")
        r = _memsan.sanitize_memory_files(lock_timeout=0.2)
        self.assertIn("workspaces/trade/memory/MEMORY.md", r["sanitized"])
        self.assertNotIn(SECRET, mf.read_text())


if __name__ == "__main__":
    unittest.main()
