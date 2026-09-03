#!/usr/bin/env python3
"""v4.7.2 — plugin version self-report + upgrade-drift detection.

THE DEFECT THIS PINS
--------------------
A machine reported this every 10 turns, months after v4.7.1 shipped:

    Stop hook error: [gowth-mem:auto-journal ws=trade] 10 turns elapsed.
    Read /Users/<user>/.claude/plugins/cache/gowth-mem/gowth-mem/3.9.0/
    templates/auto-journal-instructions.md ...

That path is `Path(__file__).parents[2]` inside `_build_reason()` — i.e. the
plugin root ACTUALLY EXECUTING. `git show v3.9.0:hooks/scripts/auto-journal.py`
emits that exact string, character for character, and contains zero occurrences
of DELEGATE/_capture. So the machine was still running v3.9.0: the upgrade never
reached `installed_plugins.json`, and every hook kept executing pre-v4.7 code
against the SHARED vault (no v4.0 auto-tagging, no v4.1 `fix_aspect` on new
aspects, no v4.3 index repair, no `english_only` gate).

Three things let that stay invisible for months:
  1. gowth-mem never printed its own version anywhere. The bootstrap header was
     `[gowth-mem:bootstrap workspace=<ws>]`. Drift was undetectable except by
     noticing a version number buried in an absolute path inside a block reason.
  2. `bin/doctor.sh` detects and heals exactly this — but only ran when a human
     typed `/mem-doctor`.
  3. `bin/auto-upgrade.sh` existed, was complete, and was referenced by NOTHING
     (grep across the whole repo: zero hits outside its own header). A fix
     script nobody calls is not a fix.

These tests pin the version report, the drift comparison (including the
string-compare trap 3.10.0 vs 3.9.0), the SessionStart auto-heal wiring, and a
general guard against another never-invoked ops script.
"""
from __future__ import annotations

import importlib.util
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
BIN = ROOT / "bin"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


V = load_module("gowth_version_mod", SCRIPTS / "_version.py")


class ParseVersionTest(unittest.TestCase):
    def test_dotted_numeric(self):
        self.assertEqual(V.parse_version("4.7.1"), (4, 7, 1))
        self.assertEqual(V.parse_version("3.9.0"), (3, 9, 0))

    def test_two_and_four_segments(self):
        self.assertEqual(V.parse_version("4.7"), (4, 7))
        self.assertEqual(V.parse_version("1.2.3.4"), (1, 2, 3, 4))

    def test_non_semver_is_none(self):
        # Real example from the user's registry: frontend-design pins a git sha
        # as its "version". Comparing that numerically would be nonsense.
        self.assertIsNone(V.parse_version("0120fb83da5d"))
        self.assertIsNone(V.parse_version("v4.7.1"))
        self.assertIsNone(V.parse_version(""))
        self.assertIsNone(V.parse_version(None))


class IsOutdatedTest(unittest.TestCase):
    def test_the_reported_case(self):
        self.assertTrue(V.is_outdated("3.9.0", "4.7.1"))

    def test_string_compare_trap(self):
        # "3.10.0" < "3.9.0" lexically. Numeric compare must not regress here.
        self.assertFalse(V.is_outdated("3.10.0", "3.9.0"))
        self.assertTrue(V.is_outdated("3.9.0", "3.10.0"))

    def test_equal_is_not_outdated(self):
        self.assertFalse(V.is_outdated("4.7.1", "4.7.1"))

    def test_running_ahead_is_not_outdated(self):
        # Dev machine whose marketplace clone has not been pulled yet.
        self.assertFalse(V.is_outdated("4.8.0", "4.7.1"))

    def test_unknown_never_claims_drift(self):
        self.assertFalse(V.is_outdated(None, "4.7.1"))
        self.assertFalse(V.is_outdated("3.9.0", None))
        self.assertFalse(V.is_outdated("3.9.0", "0120fb83da5d"))


class ReadPluginVersionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gowth_ver_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, payload: str):
        (self.tmp / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        (self.tmp / ".claude-plugin" / "plugin.json").write_text(payload)

    def test_reads_version(self):
        self._write(json.dumps({"name": "gowth-mem", "version": "1.2.3"}))
        self.assertEqual(V.read_plugin_version(self.tmp), "1.2.3")

    def test_missing_file_is_none(self):
        self.assertIsNone(V.read_plugin_version(self.tmp))

    def test_malformed_json_is_none(self):
        self._write("{not json")
        self.assertIsNone(V.read_plugin_version(self.tmp))

    def test_running_version_matches_repo_manifest(self):
        declared = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())["version"]
        self.assertEqual(V.running_version(), declared,
                         "running_version() must report the manifest of the tree it executes from")


class MarketplaceVersionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gowth_mkt_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_reads_clone_version(self):
        clone = self.tmp / "plugins" / "marketplaces" / "gowth-mem" / ".claude-plugin"
        clone.mkdir(parents=True)
        (clone / "plugin.json").write_text(json.dumps({"version": "9.9.9"}))
        self.assertEqual(V.marketplace_version(claude_dir=self.tmp), "9.9.9")

    def test_missing_clone_is_none(self):
        self.assertIsNone(V.marketplace_version(claude_dir=self.tmp))


class VersionTagTest(unittest.TestCase):
    def test_tag_is_appendable_to_header(self):
        tag = V.version_tag()
        self.assertTrue(tag.startswith(" v"), tag)
        self.assertIn(V.running_version() or "", tag)

    def test_tag_empty_when_unknown(self):
        self.assertEqual(V.version_tag(running="  "), "")


class DriftNudgeTest(unittest.TestCase):
    def test_silent_when_healthy(self):
        self.assertEqual(V.drift_nudge("4.7.1", "4.7.1"), "")
        self.assertEqual(V.drift_nudge(None, None), "")
        self.assertEqual(V.drift_nudge("4.8.0", "4.7.1"), "")

    def test_names_both_versions_and_the_exact_fix(self):
        n = V.drift_nudge("3.9.0", "4.7.1")
        self.assertTrue(n, "a stale machine must be told")
        self.assertIn("3.9.0", n)
        self.assertIn("4.7.1", n)
        # Official CLI first, doctor as the fallback for a stuck registry.
        self.assertIn("claude plugin update gowth-mem", n)
        self.assertIn("/mem-doctor", n)
        self.assertIn("restart", n.lower())

    def test_says_it_in_one_line(self):
        # Mirrors the review-paused notice contract: a nudge must not derail the
        # session into an upgrade project.
        self.assertIn("ONE line", V.drift_nudge("3.9.0", "4.7.1"))


class BootstrapHeaderTest(unittest.TestCase):
    """The SessionStart header must carry the running version."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_boot_ver_")
        self.ws = "demo"
        home = Path(self.tmp)
        (home / "shared").mkdir(parents=True, exist_ok=True)
        wsd = home / "workspaces" / self.ws
        (wsd / "docs").mkdir(parents=True, exist_ok=True)
        (wsd / "journal").mkdir(parents=True, exist_ok=True)
        (wsd / "workspace.json").write_text('{"name": "demo"}')
        (home / "settings.json").write_text('{"layout_version": 3}')
        (home / "shared" / "AGENTS.md").write_text("A" * 500)
        (wsd / "docs" / "handoff.md").write_text("H" * 500)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _ctx(self) -> str:
        r = subprocess.run(
            [sys.executable, str(SCRIPTS / "bootstrap-load.py")],
            input=json.dumps({"source": "startup"}),
            capture_output=True, text=True,
            env={**os.environ, "GOWTH_MEM_HOME": self.tmp, "GOWTH_WORKSPACE": self.ws},
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]

    def test_header_reports_running_version(self):
        ctx = self._ctx()
        ver = V.running_version()
        self.assertIn(f"[gowth-mem:bootstrap workspace={self.ws} v{ver}]", ctx,
                      "the running plugin version must be visible in every session")


class SessionStartAutoHealContractTest(unittest.TestCase):
    """The self-heal must actually be invoked — that was the whole failure."""

    def setUp(self):
        self.src = (SCRIPTS / "session-start.sh").read_text()

    def test_invokes_doctor(self):
        self.assertIn("doctor.sh", self.src,
                      "SessionStart must run the registry self-heal; a manual-only "
                      "doctor is why a machine sat on v3.9.0 for months")

    def test_heal_is_backgrounded(self):
        # Startup latency budget: the heal must never be on the blocking path.
        # The invocation line (however the path is spelled) must be backgrounded.
        invocations = [ln for ln in self.src.splitlines()
                       if "bash" in ln and ("doctor.sh" in ln or "DOCTOR" in ln)
                       and not ln.strip().startswith("#")]
        self.assertTrue(invocations, "no doctor invocation found")
        self.assertTrue(all("&" in ln for ln in invocations),
                        f"doctor must run detached, got: {invocations}")

    def test_no_network_on_the_startup_path(self):
        # --pull does git fetch. Never on SessionStart.
        self.assertNotRegex(self.src, r"doctor\.sh[^\n]*--pull",
                            "SessionStart must not do network I/O")

    def test_env_opt_out(self):
        self.assertIn("GOWTH_MEM_NO_AUTOHEAL", self.src)

    def test_settings_opt_out_is_a_pure_bash_precheck(self):
        self.assertIn("auto_heal", self.src)
        self.assertIn("grep", self.src,
                      "settings gate must be bash grep, not a python startup")

    def test_still_exits_zero_with_no_vault(self):
        r = subprocess.run(
            ["bash", str(SCRIPTS / "session-start.sh")],
            input=json.dumps({"source": "startup"}),
            capture_output=True, text=True,
            env={**os.environ, "GOWTH_MEM_HOME": "/nonexistent-gowth-autoheal-test"},
        )
        self.assertEqual(r.returncode, 0, r.stderr)


class NoDeadOpsScriptTest(unittest.TestCase):
    """Every bin/*.sh must be reachable from somewhere outside bin/.

    `bin/auto-upgrade.sh` was a complete, working upgrade script that no command,
    skill, hook, doc or test ever referenced. It would have prevented the v3.9.0
    drift entirely. This guard fails the build on the next such script.
    """

    def test_every_bin_script_is_referenced(self):
        # tests/ is deliberately EXCLUDED: a script referenced only by the test
        # that guards it is still a script nothing runs.
        searched = [ROOT / d for d in ("commands", "skills", "hooks", "docs", ".github")]
        searched += [ROOT / f for f in ("CLAUDE.md", "README.md", "RESEARCH.md")]
        blob = ""
        for p in searched:
            if p.is_file():
                blob += p.read_text(encoding="utf-8", errors="replace")
            elif p.is_dir():
                for f in p.rglob("*"):
                    if f.is_file() and f.suffix in (".md", ".py", ".sh", ".json", ".yml", ".yaml"):
                        blob += f.read_text(encoding="utf-8", errors="replace")
        orphans = [s.name for s in sorted(BIN.glob("*.sh")) if s.name not in blob]
        self.assertEqual(orphans, [],
                         f"ops script(s) referenced by nothing: {orphans}. "
                         f"Wire them up or delete them — a fix nobody calls is not a fix.")


if __name__ == "__main__":
    unittest.main()
