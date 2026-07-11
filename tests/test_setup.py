#!/usr/bin/env python3
"""Tests for v4.1 _setup.py — Claude setup portability (backup → shared/setup/).

The backup must let a new machine restore the full Claude Code environment
(plugins, marketplaces, global MCP servers, personal skills, settings, global
CLAUDE.md) with one script run + one /plugin paste block — WITHOUT ever
writing a real secret value into the synced vault.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "hooks" / "scripts"


def _load():
    sys.path.insert(0, str(SCRIPTS_DIR))
    import _setup
    return _setup


SETUP = _load()

FAKE_GH_TOKEN = "ghp_" + "a" * 40  # matches _privacy github-pat pattern


def _scaffold_claude(tmp: Path) -> tuple[Path, Path]:
    """Build a fake ~/.claude + ~/.claude.json pair."""
    claude = tmp / "dot-claude"
    (claude / "plugins").mkdir(parents=True)
    (claude / "skills" / "my-skill").mkdir(parents=True)

    # marketplace clone with a real git remote
    mkt = claude / "plugins" / "marketplaces" / "my-market"
    mkt.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(mkt)], check=True)
    subprocess.run(["git", "-C", str(mkt), "remote", "add", "origin",
                    "https://github.com/example/my-market.git"], check=True)
    # builtin marketplace (no git remote)
    (claude / "plugins" / "marketplaces" / "claude-plugins-official").mkdir()

    (claude / "plugins" / "installed_plugins.json").write_text(json.dumps({
        "version": 2,
        "plugins": {
            "my-plugin@my-market": [{"scope": "user", "version": "1.2.3",
                                     "installPath": "/x", "installedAt": "t"}],
            "official-thing@claude-plugins-official": [{"scope": "user", "version": "0.1.0"}],
        },
    }))

    (claude / "skills" / "my-skill" / "SKILL.md").write_text(
        "---\nname: my-skill\n---\nDo the thing. token=" + FAKE_GH_TOKEN + "\n")
    (claude / "CLAUDE.md").write_text("# global rules\nbe excellent\n")
    (claude / "settings.json").write_text(json.dumps({
        "model": "opus", "env": {"MY_FLAG": "1"},
        "enabledPlugins": {"my-plugin@my-market": True},
    }))

    claude_json = tmp / "dot-claude.json"
    claude_json.write_text(json.dumps({
        "mcpServers": {
            "my-mcp": {"command": "npx", "args": ["-y", "my-mcp"],
                       "env": {"MY_MCP_TOKEN": FAKE_GH_TOKEN}},
        },
        "someOtherTopLevelState": {"huge": "ignore-me"},
    }))
    return claude, claude_json


class TestBackup(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.home = tmp / "gowth-home"
        self.home.mkdir()
        os.environ["GOWTH_MEM_HOME"] = str(self.home)
        self.claude, self.claude_json = _scaffold_claude(tmp)
        self.out = self.home / "shared" / "setup"

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        self._tmp.cleanup()

    def _backup(self, **kw):
        return SETUP.backup(claude_dir=self.claude, claude_json=self.claude_json, **kw)

    def test_plugins_and_marketplaces_collected(self):
        self._backup()
        data = json.loads((self.out / "plugins.json").read_text())
        self.assertEqual(data["marketplaces"]["my-market"],
                         "https://github.com/example/my-market.git")
        self.assertEqual(data["marketplaces"]["claude-plugins-official"], "builtin")
        self.assertIn("my-plugin@my-market", data["plugins"])
        self.assertEqual(data["plugins"]["my-plugin@my-market"]["version"], "1.2.3")

    def test_mcp_env_values_redacted_to_pointers(self):
        self._backup()
        mcp = json.loads((self.out / "mcp.global.json").read_text())
        self.assertEqual(mcp["mcpServers"]["my-mcp"]["env"]["MY_MCP_TOKEN"],
                         "<env:MY_MCP_TOKEN>")
        self.assertIn("MY_MCP_TOKEN", mcp["required_env"])
        self.assertNotIn(FAKE_GH_TOKEN, (self.out / "mcp.global.json").read_text())
        # non-secret fields survive verbatim
        self.assertEqual(mcp["mcpServers"]["my-mcp"]["command"], "npx")

    def test_skills_copied_and_sanitized(self):
        r = self._backup()
        copied = self.out / "skills" / "my-skill" / "SKILL.md"
        self.assertTrue(copied.is_file())
        text = copied.read_text()
        self.assertIn("Do the thing.", text)
        self.assertNotIn(FAKE_GH_TOKEN, text, "real token must never enter the vault")
        self.assertGreater(r["redactions"], 0)

    def test_settings_and_global_claude_md_copied(self):
        self._backup()
        self.assertIn("be excellent", (self.out / "CLAUDE.global.md").read_text())
        s = json.loads((self.out / "settings.json").read_text())
        self.assertEqual(s["model"], "opus")

    def test_restore_artifacts_generated(self):
        self._backup()
        md = (self.out / "RESTORE.md").read_text()
        sh = (self.out / "restore.sh").read_text()
        self.assertIn("/plugin marketplace add https://github.com/example/my-market.git", md)
        self.assertIn("/plugin install my-plugin@my-market", md)
        # builtin marketplace needs no `marketplace add` line
        self.assertNotIn("marketplace add builtin", md)
        self.assertIn("mcp.global.json", sh)
        self.assertTrue(os.access(self.out / "restore.sh", os.X_OK), "restore.sh must be executable")

    def test_manifest_written_with_counts(self):
        r = self._backup()
        m = json.loads((self.out / "manifest.json").read_text())
        self.assertEqual(m["plugins"], 2)
        self.assertEqual(m["marketplaces"], 2)
        self.assertEqual(m["mcp_servers"], 1)
        self.assertGreaterEqual(m["skills"], 1)
        self.assertEqual(r["plugins"], 2)

    def test_dry_run_writes_nothing(self):
        r = self._backup(dry_run=True)
        self.assertFalse(self.out.exists())
        self.assertEqual(r["plugins"], 2)  # still reports what it WOULD do

    def test_missing_claude_dir_graceful(self):
        r = SETUP.backup(claude_dir=Path(self._tmp.name) / "nope",
                         claude_json=Path(self._tmp.name) / "nope.json")
        self.assertIn("skipped", r)

    def test_idempotent_second_run(self):
        self._backup()
        r2 = self._backup()
        self.assertEqual(r2["plugins"], 2)
        data = json.loads((self.out / "plugins.json").read_text())
        self.assertEqual(len(data["plugins"]), 2)


if __name__ == "__main__":
    unittest.main()
