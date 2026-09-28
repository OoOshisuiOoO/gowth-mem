"""v4.8 Task 5: SessionStart — native header, fallback bootstrap, compact delta.

When the project is wired to the vault's MEMORY.md (auto memory), the hook
prints only a short header: the host attaches MEMORY.md itself, and anything
the hook prints at >= 10,000 chars would be persisted to a 2,000-char preview
(measured on 2.1.283). When not wired, the hook prints the same sections as a
fallback under 8,500 chars, handoff first. `compact` gets a small delta
(MEMORY.md is re-attached by the host after compaction). `clear` gets a
bootstrap again (it used to get nothing). `resume` stays silent.
"""
from __future__ import annotations

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

HANDOFF = "## 2026-09-13 snapshot\n- host:mac 2026-09-13 [blocker] waiting on token\n- host:mac 2026-09-13 [done] shipped v4\n"


def _readme(slug: str, touched: str) -> str:
    return f"---\nslug: {slug}\ntitle: {slug.title()}\ntype: topic\nlast_touched: {touched}\n---\n# {slug}\n\nAbout {slug}.\n"


class _BootCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gowth_bootn_"))
        self.vault = self.tmp / "vault"
        self.proj = self.tmp / "proj"
        self.proj.mkdir()
        self.ws = "demo"
        self.wsd = self.vault / "workspaces" / self.ws
        (self.wsd / "docs").mkdir(parents=True)
        (self.wsd / "journal").mkdir()
        (self.wsd / "workspace.json").write_text('{"name": "demo"}')
        (self.wsd / "docs" / "handoff.md").write_text(HANDOFF)
        for slug, touched in (("alpha", "2026-09-01"), ("beta", "2026-09-12")):
            (self.wsd / slug).mkdir()
            (self.wsd / slug / "00-README.md").write_text(_readme(slug, touched))
        (self.vault / "shared").mkdir()
        (self.vault / "shared" / "AGENTS.md").write_text("# rules\n" + "R" * 12_000)
        (self.vault / "shared" / "secrets.md").write_text("- `FAKE_KEY`\nFAKE_TOKEN=abc123\n")
        (self.vault / "settings.json").write_text('{"layout_version": 3}')
        (self.vault / "config.json").write_text(json.dumps(
            {"workspace_map": {f"{self.proj.resolve()}/**": "demo"}}))
        (self.tmp / "claude").mkdir()
        self.env = {**os.environ, "GOWTH_MEM_HOME": str(self.vault),
                    "CLAUDE_CONFIG_DIR": str(self.tmp / "claude"),
                    "GOWTH_MEM_NO_AUTOHEAL": "1"}
        self.env.pop("GOWTH_WORKSPACE", None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def wire(self) -> None:
        os.environ["GOWTH_MEM_HOME"] = str(self.vault)
        try:
            import _native  # type: ignore
            import _memfile  # type: ignore
            self.assertEqual(_native.wire(self.proj, self.ws), "wired")
            self.assertTrue(_memfile.write(self.ws))
        finally:
            os.environ.pop("GOWTH_MEM_HOME", None)

    def run_bootstrap(self, source: str, *args: str) -> str:
        r = subprocess.run([sys.executable, str(SCRIPTS / "bootstrap-load.py"), *args],
                           input=json.dumps({"source": source, "cwd": str(self.proj),
                                             "session_id": "boot0001"}),
                           capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        return r.stdout

    def context(self, source: str) -> str:
        out = self.run_bootstrap(source)
        if not out.strip():
            return ""
        return json.loads(out)["hookSpecificOutput"]["additionalContext"]

    def run_session_start(self, source: str) -> str:
        r = subprocess.run(["bash", str(SCRIPTS / "session-start.sh")],
                           input=json.dumps({"source": source, "cwd": str(self.proj),
                                             "session_id": "boot0001"}),
                           capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout


class NativeModeTest(_BootCase):
    def test_native_mode_prints_header_only(self):
        self.wire()
        ctx = self.context("startup")
        self.assertLess(len(ctx), 600, ctx)
        self.assertIn("[gowth-mem:bootstrap workspace=demo", ctx)
        self.assertIn("MEMORY.md", ctx)
        self.assertNotIn("## Handoff", ctx)

    def test_compact_delta_under_1500_with_handoff(self):
        self.wire()
        ctx = self.context("compact")
        self.assertLess(len(ctx), 1500)
        self.assertIn("## Handoff", ctx)
        self.assertIn("[blocker]", ctx)

    def test_native_mode_refreshes_memfile_when_handoff_changed(self):
        self.wire()
        import time
        h = self.wsd / "docs" / "handoff.md"
        h.write_text(HANDOFF + "- host:mac 2026-09-14 [next] brand-new item\n")
        fut = time.time() + 5
        os.utime(h, (fut, fut))
        self.context("startup")
        self.assertIn("brand-new item", (self.wsd / "memory" / "MEMORY.md").read_text())


class FallbackModeTest(_BootCase):
    def test_fallback_when_not_wired(self):
        ctx = self.context("startup")
        self.assertLess(len(ctx), 9000)
        self.assertLess(ctx.index("## Handoff"), ctx.index("## Rules"))
        self.assertIn("[blocker]", ctx)
        self.assertNotIn("abc123", ctx)
        self.assertIn("/mem-setup native", ctx)

    def test_oversized_vault_never_exceeds_9000(self):
        (self.wsd / "docs" / "handoff.md").write_text(
            "## 2026-09-13 s\n" + "".join(f"- host:mac 2026-09-13 [done] item {i} " + "x" * 150 + "\n"
                                          for i in range(1500)))
        for i in range(60):
            d = self.wsd / f"t{i:02d}"
            d.mkdir()
            (d / "00-README.md").write_text(_readme(f"t{i:02d}", "2026-09-05"))
        ctx = self.context("startup")
        self.assertLess(len(ctx), 9000)
        self.assertIn("## Handoff", ctx)

    def test_startup_creates_missing_workspace_json_and_memfile(self):
        (self.wsd / "workspace.json").unlink()
        self.context("startup")
        self.assertTrue((self.wsd / "workspace.json").is_file())
        self.assertTrue((self.wsd / "memory" / "MEMORY.md").is_file())

    def test_report_lists_memfile_line(self):
        out = self.run_bootstrap("startup", "--report")
        self.assertIn("memfile:", out)
        self.assertIn("mode=fallback", out)

    def test_report_lists_recall_telemetry(self):
        (self.vault / "state.json").write_text(json.dumps({"session": {
            "s1": {"turn_count": 3, "recall": {"injected": 2, "entries": 4, "chars": 1800, "ids": [1, 2, 3, 4]}},
            "s2": {"turn_count": 1, "recall": {"injected": 1, "entries": 1, "chars": 300, "ids": [9]}},
        }, "recall_daily": {"2026-09-27": {"prompts": 40, "injected": 3, "entries": 5},
                            "2026-09-28": {"prompts": 10, "injected": 1, "entries": 1}}}))
        out = self.run_bootstrap("startup", "--report")
        self.assertIn("recall:", out)
        self.assertIn("3 prompts", out)
        self.assertIn("5 entries", out)
        self.assertIn("2100 chars", out)
        # review M13: the injection RATE over every profiled prompt (14-day totals)
        self.assertIn("recall rate: 4/50 prompts injected (8%)", out)


class SessionStartScriptTest(_BootCase):
    def test_clear_emits_bootstrap(self):
        out = self.run_session_start("clear")
        self.assertIn("additionalContext", out)
        self.assertIn("[gowth-mem:bootstrap workspace=demo", out)

    def test_resume_emits_nothing(self):
        self.assertEqual(self.run_session_start("resume").strip(), "")

    def test_startup_output_is_valid_hook_json(self):
        out = self.run_session_start("startup")
        data = json.loads(out.strip().splitlines()[-1])
        self.assertIn("hookSpecificOutput", data)


if __name__ == "__main__":
    unittest.main()
