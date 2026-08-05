"""Tests for bootstrap-load.py budget allocation (v4.3, Zero-Mem audit D8).

The defect: `_load_file(f, gh, room)` handed the FIRST file the entire remaining
budget, so on the live vault shared/AGENTS.md (13,281 B) plus a truncated
shared/secrets.md (13,520 B) consumed the whole 15,000-char cap and the loop broke
at `room <= 200`. Result: `[bootstrap: loaded 2/5 files]` — workspace AGENTS.md and
docs/handoff.md, the two files carrying CURRENT session state, never reached the
model at all. handoff.md is the file the entire handoff protocol exists to deliver.

The fix reserves budget for the small per-session deltas BEFORE the large static
files are read, and caps any single file. Ordering is deliberately NOT changed:
CLAUDE.md requires a stable prefix (AGENTS/secrets/tools) for Anthropic prompt-cache
hits, and handoff.md changes every session — moving it first would poison the cache
prefix for every stable file behind it.
"""
import importlib.util
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


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class BootstrapBudgetTest(unittest.TestCase):
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
        # Two oversized static files, mirroring the live vault's shape.
        (home / "shared" / "AGENTS.md").write_text("A" * 13_281)
        (home / "shared" / "secrets.md").write_text("S" * 13_520)
        (home / "shared" / "tools.md").write_text("T" * 3_750)
        # Two small per-session deltas that were being starved.
        (wsd / "AGENTS.md").write_text("W" * 1_206)
        (wsd / "docs" / "handoff.md").write_text("HANDOFF-MARKER " + "H" * 4_000)
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

    def test_handoff_is_loaded_despite_oversized_static_files(self):
        ctx = self._run()
        self.assertIn("HANDOFF-MARKER", ctx,
                      "docs/handoff.md must reach the model; it carries session state")

    def test_all_five_stable_files_load(self):
        ctx = self._run()
        for rel in ("shared/AGENTS.md", "shared/secrets.md", "shared/tools.md",
                    f"workspaces/{self.ws}/AGENTS.md",
                    f"workspaces/{self.ws}/docs/handoff.md"):
            self.assertIn(rel, ctx, f"{rel} missing from bootstrap")

    def test_total_stays_within_cap(self):
        boot = load_module("gowth_boot_cap", SCRIPTS / "bootstrap-load.py")
        ctx = self._run()
        self.assertLessEqual(len(ctx), boot.MAX_TOTAL + 2_000,
                             "context must respect MAX_TOTAL (plus header/summary)")

    def test_no_single_file_exceeds_per_file_cap(self):
        boot = load_module("gowth_boot_pf", SCRIPTS / "bootstrap-load.py")
        ctx = self._run()
        # Each block is "=== label ===\n<body>"; the A-run and S-run are the
        # oversized statics, so neither may appear more than MAX_PER_FILE times.
        self.assertLessEqual(ctx.count("A"), boot.MAX_PER_FILE + 200)
        self.assertLessEqual(ctx.count("S"), boot.MAX_PER_FILE + 200)

    def test_truncation_marker_present_for_oversized_file(self):
        ctx = self._run()
        self.assertIn("[truncated:", ctx)

    def test_stable_prefix_order_preserved_for_prompt_cache(self):
        ctx = self._run()
        i_shared = ctx.index("shared/AGENTS.md")
        i_handoff = ctx.index("docs/handoff.md")
        self.assertLess(i_shared, i_handoff,
                        "stable shared files must precede per-session deltas so the "
                        "prompt-cache prefix stays stable across sessions")

    def test_summary_reports_all_loaded(self):
        ctx = self._run()
        self.assertRegex(ctx, r"\[bootstrap: loaded 5/5 files")

    def test_missing_optional_file_does_not_consume_budget(self):
        (self.wsd / "docs" / "handoff.md").unlink()
        ctx = self._run()
        self.assertIn("shared/tools.md", ctx)
        self.assertNotIn("HANDOFF-MARKER", ctx)

    def test_today_journal_included_when_present(self):
        j = self.wsd / "journal" / f"{date.today().isoformat()}.md"
        j.write_text("JOURNAL-MARKER today notes")
        ctx = self._run()
        self.assertIn("JOURNAL-MARKER", ctx)

    def test_small_vault_is_unaffected(self):
        """Regression: when every file fits under the caps, nothing is truncated."""
        (self.home / "shared" / "AGENTS.md").write_text("a" * 100)
        (self.home / "shared" / "secrets.md").write_text("s" * 100)
        (self.home / "shared" / "tools.md").write_text("t" * 100)
        (self.wsd / "docs" / "handoff.md").write_text("HANDOFF-MARKER " + "H" * 100)
        ctx = self._run()
        self.assertNotIn("[truncated:", ctx)
        self.assertIn("HANDOFF-MARKER", ctx)

    def test_oversized_handoff_keeps_its_NEWEST_state(self):
        """handoff.md is newest-FIRST (verified on the live vault: top line is the
        most recent `host:` entry), so head-truncation must preserve current state.
        A tail-truncating implementation would inject stale state and defeat the fix."""
        newest = "host:Mini 2026-08-05 NEWEST-STATE current task\n"
        self.wsd.joinpath("docs", "handoff.md").write_text(newest + ("x" * 200_000))
        ctx = self._run()
        self.assertIn("NEWEST-STATE", ctx)


if __name__ == "__main__":
    unittest.main()
