"""Review C2 (v4.8): `<ws>/memory/` is Claude Code's own auto-memory. Nothing in
the vault's maintenance may rewrite it: the Stop-hook prune (`_prune.py
--all-workspaces`, every journal cadence) matched Claude's `- [Title](file.md)`
index lines and `- [ ]` checklist items with ENTRY_RE and deleted them as
"superseded"/"duplicate"; the index treated MEMORY.md as topic content.

Both run through their REAL entry points (subprocess), with a positive control
(a topic aspect that IS pruned / indexed) so a vacuous pass cannot hide.
"""
from __future__ import annotations

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

FREE_ZONE = (
    "- [Legacy deploy notes](deploy_legacy.md) — (deprecated) kept for history\n"
    "- [ ] migrate the staging database to the new cluster before friday\n"
    "- [ ] migrate the staging database to the new cluster before friday morning\n"
)
TASKS = (
    "# tasks\n\n"
    "- [ ] migrate the staging database to the new cluster before friday\n"
    "- [ ] migrate the staging database to the new cluster before friday morning\n"
    "- [Old plan](old_plan.md) — (obsolete) see new_plan.md\n"
)
ASPECT = (
    "---\nslug: t-a\n---\n"
    "- [exp] alpha finding about wombats (deprecated) replaced by beta\n"
    "- [exp] beta finding about wombats stands on its own evidence here\n"
)


class MemoryDirProtectedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gowth_memprot_"))
        self.env = {**os.environ, "GOWTH_MEM_HOME": str(self.tmp)}
        self.env.pop("GOWTH_WORKSPACE", None)
        wsd = self.tmp / "workspaces" / "demo"
        (wsd / "memory").mkdir(parents=True)
        (wsd / "docs").mkdir()
        (wsd / "t").mkdir()
        (wsd / "workspace.json").write_text(json.dumps({"name": "demo"}))
        (wsd / "docs" / "handoff.md").write_text("## 2026-09-13 s\n- host:mac 2026-09-13 [next] go\n")
        (wsd / "t" / "00-README.md").write_text("---\nslug: t\ntitle: T\nlast_touched: 2026-09-10\n---\n# T\n\nAbout T.\n")
        self.aspect = wsd / "t" / "2026-09-10-a.md"
        self.aspect.write_text(ASPECT)
        (self.tmp / "shared").mkdir()
        (self.tmp / "settings.json").write_text(json.dumps({"layout_version": 3}))
        (self.tmp / "config.json").write_text(json.dumps({"active_workspace": "demo"}))
        os.environ["GOWTH_MEM_HOME"] = str(self.tmp)
        import _memfile  # type: ignore
        self.memfile = wsd / "memory" / "MEMORY.md"
        self.memfile.write_text(_memfile.render("demo") + FREE_ZONE)
        self.tasks = wsd / "memory" / "project_tasks.md"
        self.tasks.write_text(TASKS)

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, script: str, *args: str) -> subprocess.CompletedProcess:
        r = subprocess.run([sys.executable, str(SCRIPTS / script), *args],
                           capture_output=True, text=True, env=self.env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        return r

    def test_stop_hook_prune_never_touches_memory_files(self):
        before_mem = self.memfile.read_bytes()
        before_tasks = self.tasks.read_bytes()
        r = self._run("_prune.py", "--all-workspaces")
        self.assertEqual(self.memfile.read_bytes(), before_mem, "MEMORY.md free zone was rewritten")
        self.assertEqual(self.tasks.read_bytes(), before_tasks, "Claude's memory note was rewritten")
        self.assertNotIn("memory/", r.stdout)
        # positive control: the topic aspect's deprecated entry IS pruned
        self.assertIn("2026-09-10-a.md", r.stdout)
        self.assertNotIn("alpha finding", self.aspect.read_text())
        self.assertIn("beta finding", self.aspect.read_text())

    def test_index_skips_memory_dir(self):
        self._run("_index.py")
        db = sqlite3.connect(str(self.tmp / "index.db"))
        try:
            mem = db.execute("SELECT count(*) FROM chunks WHERE path LIKE '%/memory/%'").fetchone()[0]
            topic = db.execute("SELECT count(*) FROM chunks WHERE path LIKE '%2026-09-10-a.md'").fetchone()[0]
        finally:
            db.close()
        self.assertEqual(mem, 0, "memory/*.md must not be indexed as topic content")
        self.assertGreaterEqual(topic, 1)

    def test_incremental_index_skips_memory_dir(self):
        self._run("_index.py")
        self.tasks.write_text(TASKS + "- [ ] a brand new task line about numbats\n")
        fut = os.stat(self.aspect).st_mtime + 60
        os.utime(self.tasks, (fut, fut))
        self._run("_index.py", "--incremental")
        db = sqlite3.connect(str(self.tmp / "index.db"))
        try:
            mem = db.execute("SELECT count(*) FROM chunks WHERE path LIKE '%/memory/%'").fetchone()[0]
        finally:
            db.close()
        self.assertEqual(mem, 0)


if __name__ == "__main__":
    unittest.main()
