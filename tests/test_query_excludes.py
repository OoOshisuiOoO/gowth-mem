"""v4.8 Task 6: research/ scratch and docs/handoff-archive.md leave default recall.

Audit probe on the live vault: `research/` notes took 9 of 30 top-3 slots and
were rank 1 in 4 of the 5 queries whose curated source was not rank 1; the
fifth was docs/handoff-archive.md. Both stay indexed and reachable with
--include-research; callers can exclude more path prefixes.
"""
from __future__ import annotations

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


class QueryExcludeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_qx_")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        self.ws = "demo"
        wsd = Path(self.tmp) / "workspaces" / self.ws
        (wsd / "t").mkdir(parents=True)
        (wsd / "docs").mkdir()
        (wsd / "journal").mkdir()
        (wsd / "research" / "x").mkdir(parents=True)
        (wsd / "workspace.json").write_text('{"name": "demo"}')
        (wsd / "t" / "00-README.md").write_text("---\nslug: t\ntitle: T\n---\n# T\n")
        (wsd / "t" / "2026-09-10-note.md").write_text(
            "---\nslug: t-note\n---\n- [ref] zebratoken indexing details for the curated entry. Source: test\n")
        (wsd / "research" / "x" / "notes.md").write_text("# scratch\n\nzebratoken research scratch notes\n")
        (wsd / "docs" / "handoff-archive.md").write_text("# archive\n\nzebratoken archived handoff bullet\n")
        (wsd / "journal" / "2026-09-10.md").write_text("# journal\n\nzebratoken journal mention\n")
        r = subprocess.run([sys.executable, str(SCRIPTS / "_index.py")], capture_output=True, text=True,
                           env={**os.environ, "GOWTH_MEM_HOME": self.tmp})
        assert r.returncode == 0, r.stderr
        import _query  # type: ignore
        self.q = _query

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _paths(self, **kw) -> list:
        res = self.q.query_ex(self.ws, "", "zebratoken", limit=20, **kw)
        self.assertIsNone(res["error"], res["error"])
        return [h["path"] for h in res["hits"]]

    def test_default_excludes_research_and_handoff_archive(self):
        paths = self._paths()
        self.assertTrue(any("/t/2026-09-10-note.md" in p for p in paths), paths)
        self.assertTrue(any("/journal/" in p for p in paths), paths)
        self.assertFalse(any("/research/" in p for p in paths), paths)
        self.assertFalse(any("handoff-archive" in p for p in paths), paths)

    def test_include_research_restores_everything(self):
        paths = self._paths(include_research=True)
        self.assertTrue(any("/research/" in p for p in paths), paths)
        self.assertTrue(any("handoff-archive" in p for p in paths), paths)

    def test_extra_exclude_prefix(self):
        paths = self._paths(exclude=("journal/",))
        self.assertFalse(any("/journal/" in p for p in paths), paths)
        self.assertTrue(any("/t/2026-09-10-note.md" in p for p in paths), paths)

    def test_hits_carry_chunk_ids(self):
        res = self.q.query_ex(self.ws, "", "zebratoken", limit=5)
        self.assertTrue(all(isinstance(h.get("id"), int) for h in res["hits"]), res["hits"])

    def test_cli_flag_include_research(self):
        base = [sys.executable, str(SCRIPTS / "_query.py"), "--ws", self.ws, "zebratoken"]
        env = {**os.environ, "GOWTH_MEM_HOME": self.tmp}
        r1 = subprocess.run(base, capture_output=True, text=True, env=env)
        r2 = subprocess.run(base + ["--include-research"], capture_output=True, text=True, env=env)
        self.assertNotIn("/research/", r1.stdout)
        self.assertIn("/research/", r2.stdout)


if __name__ == "__main__":
    unittest.main()
