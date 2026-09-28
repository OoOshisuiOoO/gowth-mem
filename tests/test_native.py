"""v4.8 Task 4: `_native` — point a project's Claude Code auto-memory at the vault.

`autoMemoryDirectory` is honoured from `.claude/settings.local.json` and names
the memory directory itself (E2E on 2.1.283). Wiring merges the key, never
clobbers a different value without --force, never touches a git-tracked or
invalid file, and never runs from a hook. Import copies the machine-local
`~/.claude/projects/<slug>/memory/*.md` into the mapped workspace through the
privacy sanitizer.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "hooks" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _native  # type: ignore  # noqa: E402

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@x"}


class _NativeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gowth_native_"))
        self.vault = self.tmp / "vault"
        (self.vault / "workspaces" / "demo" / "docs").mkdir(parents=True)
        (self.vault / "workspaces" / "demo" / "workspace.json").write_text('{"name": "demo"}')
        os.environ["GOWTH_MEM_HOME"] = str(self.vault)
        self.proj = self.tmp / "proj"
        self.proj.mkdir()
        self.env = {"HOME": str(self.tmp / "home"), "CLAUDE_CONFIG_DIR": str(self.tmp / "claude")}
        (self.tmp / "claude").mkdir()

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def local(self) -> Path:
        return self.proj / ".claude" / "settings.local.json"


class WireTest(_NativeCase):
    def test_wire_creates_settings_local_then_already(self):
        self.assertEqual(_native.wire(self.proj, "demo"), "wired")
        data = json.loads(self.local().read_text())
        self.assertEqual(data["autoMemoryDirectory"], _native.memory_dir_value("demo"))
        self.assertEqual(_native.wire(self.proj, "demo"), "already")

    def test_wire_preserves_other_keys_and_indent(self):
        self.local().parent.mkdir()
        self.local().write_text('{\n  "permissions": {"allow": ["Bash"]}\n}\n')
        self.assertEqual(_native.wire(self.proj, "demo"), "wired")
        text = self.local().read_text()
        data = json.loads(text)
        self.assertEqual(data["permissions"], {"allow": ["Bash"]})
        self.assertIn("autoMemoryDirectory", data)
        self.assertTrue(text.startswith("{\n  "))

    def test_wire_conflict_without_force(self):
        self.local().parent.mkdir()
        self.local().write_text('{"autoMemoryDirectory": "/elsewhere"}')
        before = self.local().read_bytes()
        self.assertEqual(_native.wire(self.proj, "demo"), "conflict")
        self.assertEqual(self.local().read_bytes(), before)
        self.assertEqual(_native.wire(self.proj, "demo", force=True), "wired")
        self.assertEqual(json.loads(self.local().read_text())["autoMemoryDirectory"],
                         _native.memory_dir_value("demo"))

    def test_tracked_file_is_left_alone(self):
        subprocess.run(["git", "init", "-q", str(self.proj)], check=True)
        self.local().parent.mkdir()
        self.local().write_text('{"permissions": {}}')
        subprocess.run(["git", "-C", str(self.proj), "add", ".claude/settings.local.json"], check=True)
        subprocess.run(["git", "-C", str(self.proj), "-c", "commit.gpgsign=false", "commit", "-q", "-m", "x"],
                       check=True, env={**os.environ, **GIT_ENV})
        before = self.local().read_bytes()
        self.assertEqual(_native.wire(self.proj, "demo"), "tracked")
        self.assertEqual(self.local().read_bytes(), before)

    def test_invalid_settings_local_is_left_alone(self):
        self.local().parent.mkdir()
        self.local().write_text("{bad json,}")
        self.assertEqual(_native.wire(self.proj, "demo"), "invalid")
        self.assertEqual(self.local().read_text(), "{bad json,}")

    def test_memory_dir_value_uses_tilde_under_home(self):
        home = self.tmp / "home"
        vault = home / ".gowth-mem"
        (vault / "workspaces" / "demo").mkdir(parents=True)
        os.environ["GOWTH_MEM_HOME"] = str(vault)
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(home)
        try:
            self.assertEqual(_native.memory_dir_value("demo"), "~/.gowth-mem/workspaces/demo/memory")
        finally:
            if old_home is not None:
                os.environ["HOME"] = old_home


class IsWiredTest(_NativeCase):
    def test_false_before_and_true_after_wire(self):
        self.assertFalse(_native.is_wired(self.proj, "demo", env=self.env))
        _native.wire(self.proj, "demo")
        self.assertTrue(_native.is_wired(self.proj, "demo", env=self.env))

    def test_tilde_value_resolves(self):
        home = self.tmp / "home"
        vault = home / ".gowth-mem"
        (vault / "workspaces" / "demo").mkdir(parents=True)
        os.environ["GOWTH_MEM_HOME"] = str(vault)
        self.local().parent.mkdir()
        self.local().write_text('{"autoMemoryDirectory": "~/.gowth-mem/workspaces/demo/memory"}')
        self.assertTrue(_native.is_wired(self.proj, "demo", env=self.env))

    def test_env_disable_wins(self):
        _native.wire(self.proj, "demo")
        env = {**self.env, "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
        self.assertFalse(_native.is_wired(self.proj, "demo", env=env))

    def test_host_setting_disable_wins(self):
        _native.wire(self.proj, "demo")
        (self.tmp / "claude" / "settings.json").write_text('{"autoMemoryEnabled": false}')
        self.assertFalse(_native.is_wired(self.proj, "demo", env=self.env))


class ProjectsTest(_NativeCase):
    def test_projects_for_workspaces_maps_existing_globs_and_cwd(self):
        p1 = self.tmp / "p1"
        p1.mkdir()
        cfg = {"workspace_map": {f"{p1}/**": "w1", f"{self.tmp}/missing/**": "w2"}}
        rows = _native.projects_for_workspaces(cfg, cwd=self.proj)
        self.assertIn((p1.resolve(), "w1"), rows)
        self.assertFalse(any(ws == "w2" for _, ws in rows))
        self.assertIn(self.proj.resolve(), [p for p, _ in rows])


class ImportTest(_NativeCase):
    def _native_memory(self, project: Path, files: dict) -> Path:
        slug = re.sub(r"[^A-Za-z0-9]", "-", str(project.resolve()))
        d = self.tmp / "claude" / "projects" / slug / "memory"
        d.mkdir(parents=True)
        for name, text in files.items():
            (d / name).write_text(text)
        return d

    def setUp(self):
        super().setUp()
        self.p1 = self.tmp / "p1"
        self.p1.mkdir()
        (self.vault / "config.json").write_text(json.dumps(
            {"workspace_map": {f"{self.p1.resolve()}/**": "demo"}}))
        self._native_memory(self.p1, {
            "MEMORY.md": "- [Postgres notes](postgres.md) — connection tips\n",
            "postgres.md": "# Postgres\nkey AKIAIOSFODNN7EXAMPLE must not sync\n",
        })
        self._native_memory(self.tmp / "unmapped", {"MEMORY.md": "- x\n"})

    def test_dry_run_writes_nothing(self):
        rep = _native.import_native(self.tmp / "claude")
        self.assertEqual(rep["imported"], ["postgres.md"])
        self.assertEqual(len(rep["skipped_unmapped"]), 1)
        self.assertFalse((self.vault / "workspaces" / "demo" / "memory").exists())

    def test_apply_copies_sanitizes_and_indexes_once(self):
        rep = _native.import_native(self.tmp / "claude", apply=True)
        mem = self.vault / "workspaces" / "demo" / "memory"
        self.assertTrue((mem / "postgres.md").is_file())
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", (mem / "postgres.md").read_text())
        text = (mem / "MEMORY.md").read_text()
        self.assertIn("- [Postgres notes](postgres.md) — connection tips", text)
        self.assertEqual(rep["index_lines_added"], 1)
        rep2 = _native.import_native(self.tmp / "claude", apply=True)
        self.assertEqual(rep2["index_lines_added"], 0)
        self.assertEqual((mem / "MEMORY.md").read_text().count("Postgres notes"), 1)

    def test_clash_keeps_vault_copy_and_renames_incoming(self):
        mem = self.vault / "workspaces" / "demo" / "memory"
        mem.mkdir(parents=True)
        (mem / "postgres.md").write_text("vault version\n")
        rep = _native.import_native(self.tmp / "claude", apply=True)
        self.assertEqual((mem / "postgres.md").read_text(), "vault version\n")
        renamed = [p.name for p in mem.glob("postgres.from-*.md")]
        self.assertEqual(len(renamed), 1)
        self.assertEqual(rep["renamed"], renamed)


class StatusTest(_NativeCase):
    def test_status_keys(self):
        st = _native.status(cwd=self.proj, env=self.env)
        for k in ("workspace", "wired", "memfile_exists", "host_disabled", "projects"):
            self.assertIn(k, st)


if __name__ == "__main__":
    unittest.main()
