"""Fallback bootstrap (v4.8) — the SessionStart payload when native memory is
not wired for the project.

History: v4.3 fixed a budget bug that dropped docs/handoff.md; v4.8 found the
whole 15,589-char payload was being persisted by Claude Code to a 2,000-char
preview (any hook context >= 10,000 chars), so handoff.md still never reached
the model. The fallback now emits the memfile sections — handoff FIRST, a
rules digest instead of the 12 KB shared/AGENTS.md, no journal — under 8,500
chars, and is clamped at HOOK_CONTEXT_MAX (9,000) as a last guard.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "hooks" / "scripts"


class BootstrapFallbackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_boot_")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        self.ws = "demo"
        home = Path(self.tmp)
        (home / "shared").mkdir(parents=True, exist_ok=True)
        wsd = home / "workspaces" / self.ws
        (wsd / "docs").mkdir(parents=True, exist_ok=True)
        (wsd / "journal").mkdir(parents=True, exist_ok=True)
        (wsd / "workspace.json").write_text('{"name": "demo"}')
        (home / "settings.json").write_text('{"layout_version": 3}')
        # Two oversized shared files, mirroring the live vault's shape.
        (home / "shared" / "AGENTS.md").write_text("A" * 13_281)
        (home / "shared" / "secrets.md").write_text("- `FAKE_KEY`\n" + "S" * 13_520)
        (home / "shared" / "tools.md").write_text("T" * 3_750)
        (wsd / "AGENTS.md").write_text("W" * 1_206)
        (wsd / "docs" / "handoff.md").write_text("## 2026-09-13 s\n- host:mac 2026-09-13 [doing] HANDOFF-MARKER " + "H" * 4_000)
        self.home = home
        self.wsd = wsd

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self) -> str:
        r = subprocess.run(
            [sys.executable, str(SCRIPTS / "bootstrap-load.py")],
            input=json.dumps({"source": "startup"}),
            capture_output=True, text=True,
            env={**os.environ, "GOWTH_MEM_HOME": self.tmp,
                 "GOWTH_WORKSPACE": self.ws},
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        if not r.stdout.strip():
            return ""
        return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]

    def test_handoff_is_loaded_despite_oversized_shared_files(self):
        ctx = self._run()
        self.assertIn("HANDOFF-MARKER", ctx,
                      "docs/handoff.md must reach the model; it carries session state")

    def test_sections_present(self):
        ctx = self._run()
        for h in ("## Handoff", "## Rules", "## Using memory"):
            self.assertIn(h, ctx)

    def test_total_stays_under_the_host_persist_rule(self):
        ctx = self._run()
        self.assertLess(len(ctx), 9000)

    def test_shared_agents_file_is_replaced_by_the_rules_digest(self):
        ctx = self._run()
        self.assertLess(ctx.count("AAAA"), 5, "the 13 KB shared/AGENTS.md must not be inlined")

    def test_handoff_precedes_rules(self):
        ctx = self._run()
        self.assertLess(ctx.index("## Handoff"), ctx.index("## Rules"),
                        "handoff is the first thing a fresh session needs")

    def test_missing_handoff_still_emits_rules(self):
        (self.wsd / "docs" / "handoff.md").unlink()
        ctx = self._run()
        self.assertIn("## Rules", ctx)
        self.assertNotIn("HANDOFF-MARKER", ctx)

    def test_today_journal_is_not_loaded(self):
        j = self.wsd / "journal" / f"{date.today().isoformat()}.md"
        j.write_text("JOURNAL-MARKER today notes")
        ctx = self._run()
        self.assertNotIn("JOURNAL-MARKER", ctx)

    def test_small_vault_is_unaffected(self):
        (self.home / "shared" / "AGENTS.md").write_text("a" * 100)
        (self.home / "shared" / "secrets.md").write_text("s" * 100)
        (self.home / "shared" / "tools.md").write_text("t" * 100)
        (self.wsd / "docs" / "handoff.md").write_text("## 2026-09-13 s\n- host:mac 2026-09-13 [doing] HANDOFF-MARKER small\n")
        ctx = self._run()
        self.assertNotIn("[gowth-mem: truncated", ctx)
        self.assertIn("HANDOFF-MARKER small", ctx)

    def test_oversized_handoff_keeps_its_NEWEST_state(self):
        """handoff.md is newest-FIRST; the digest must keep current state."""
        newest = "## 2026-08-05 s\n- host:Mini 2026-08-05 [doing] NEWEST-STATE current task\n"
        self.wsd.joinpath("docs", "handoff.md").write_text(newest + ("x" * 200_000))
        ctx = self._run()
        self.assertIn("NEWEST-STATE", ctx)

    def test_secret_values_never_emitted(self):
        (self.home / "shared" / "secrets.md").write_text("- `FAKE_KEY`\nFAKE_TOKEN=abc123\n")
        ctx = self._run()
        self.assertIn("FAKE_KEY", ctx)
        self.assertNotIn("abc123", ctx)


if __name__ == "__main__":
    unittest.main()
