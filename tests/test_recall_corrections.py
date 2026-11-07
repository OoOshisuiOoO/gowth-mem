"""v4.9 WP-B2 (spec §6.2): corrections on the read path.

A correction is a new entry of the same type that names its target:
    [decision] Correction: <old> → <new> (<date>; Corrects: <vault-rel path>; Source: …)
`_index` records `Corrects:` paths (<= 3 per chunk) in a `corrections` table
(idempotent migration; a path's rows are replaced when it is reindexed);
`_query` annotates a hit on a corrected path; per-prompt recall injects the
newest correction right after the old entry — never the old entry alone.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "hooks" / "scripts"
sys.path.insert(0, str(SCRIPTS))

_PROMPTS = json.loads((ROOT / "tests" / "fixtures" / "recall_prompts.json").read_text(encoding="utf-8"))
GENERIC = _PROMPTS["generic"]
REALISTIC = _PROMPTS["realistic"]


def _scrubbed_env(vault: Path, tmp: Path) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in ("AI_AGENT", "GOWTH_WORKSPACE", "CLAUDE_SUBAGENT", "GOWTH_MEM_OFF",
                        "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_PLUGIN_ROOT")}
    env.update({"GOWTH_MEM_HOME": str(vault), "HOME": str(tmp / "home"),
                "CLAUDE_CONFIG_DIR": str(tmp / "claude"), "GOWTH_MEM_NO_AUTOHEAL": "1"})
    return env


OLD ="workspaces/demo/t/2026-09-10-a.md"
FIX = "workspaces/demo/t/2026-11-01-fix.md"
FILES = {
    "t/2026-09-10-a.md": "## [decision] Use pelican-router for the ingress gateway\n\nBecause pelican-router "
                         "terminates gRPC at the edge. Rationale: fewer hops on the hot path.\n",
    "t/2026-11-01-fix.md": "## [decision] Correction: pelican-router → egret proxy for the ingress gateway "
                           f"(2026-11-01; Corrects: {OLD}; Source: deploy/egret.yaml)\n\nBecause the egret proxy "
                           "now has gRPC health checks. Rationale: one fewer hop than pelican-router.\n",
    "t/2026-09-10-b.md": "## [ref] heron-cache eviction is LRU with a 512 MB ceiling\n\nSource: heron-cache docs "
                         "v2.3. The eviction sweep runs every 30 s.\n",
    "t/2026-09-11-c.md": "## [exp] osprey-queue dropped messages when the consumer lagged\n\nThe osprey-queue "
                         "prefetch was 1000; lowering it to 50 stopped the drops.\n",
}
TARGETED = [
    "why did we choose pelican-router for the ingress gateway, and what was the rationale?",
    "pelican-router ingress gateway decision rationale please",
    "remind me what we decided about the ingress gateway and pelican-router last month",
    "does pelican-router still terminate gRPC at the edge for the ingress gateway?",
]


class _Case(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gowth_corr_"))
        self.vault = self.tmp / "vault"
        self.proj = self.tmp / "proj"
        self.proj.mkdir()
        self.wsd = self.vault / "workspaces" / "demo"
        (self.wsd / "t").mkdir(parents=True)
        (self.wsd / "docs").mkdir()
        (self.wsd / "workspace.json").write_text('{"name": "demo"}')
        (self.wsd / "t" / "00-README.md").write_text("---\nslug: t\ntitle: T\n---\n# T\n")
        for rel, body in FILES.items():
            (self.wsd / rel).write_text(f"---\nslug: x\n---\n{body}")
        (self.vault / "config.json").write_text(json.dumps({"workspace_map": {f"{self.proj.resolve()}/**": "demo"}}))
        self.settings({})
        self.env = _scrubbed_env(self.vault, self.tmp)
        self._old = os.environ.get("GOWTH_MEM_HOME")
        os.environ["GOWTH_MEM_HOME"] = str(self.vault)
        self.index()
        import _index  # type: ignore
        import _recall_prompt  # type: ignore
        self.ix = _index
        self.rp = _recall_prompt
        self.n = 0

    def tearDown(self):
        if self._old is None:
            os.environ.pop("GOWTH_MEM_HOME", None)
        else:
            os.environ["GOWTH_MEM_HOME"] = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def settings(self, recall: dict) -> dict:
        s = {"layout_version": 3, "recall": {"on_prompt_score_threshold": 0.0, **recall}}
        (self.vault / "settings.json").write_text(json.dumps(s))
        return s

    def index(self, *args: str) -> None:
        r = subprocess.run([sys.executable, str(SCRIPTS / "_index.py"), *args], capture_output=True,
                           text=True, env=self.env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)

    def rows(self) -> list:
        db = sqlite3.connect(str(self.vault / "index.db"))
        try:
            return db.execute("SELECT c.target, c.correction_path, c.date, ch.content FROM corrections c "
                              "LEFT JOIN chunks ch ON ch.id = c.chunk_id "
                              "ORDER BY c.target, c.correction_path, c.date").fetchall()
        finally:
            db.close()

    def hook(self, prompt: str, sid: str | None = None) -> str:
        self.n += 1
        ev = json.dumps({"prompt": prompt, "cwd": str(self.proj), "session_id": sid or f"s{self.n}"})
        r = subprocess.run(["bash", str(SCRIPTS / "recall-on-prompt.sh")], input=ev, capture_output=True,
                           text=True, timeout=60, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"] if r.stdout.strip() else ""

    def assert_never_alone(self, ctx: str) -> bool:
        """True when the old entry was injected — then with its annotation and
        the correction right after it."""
        lines = ctx.splitlines()
        for i, ln in enumerate(lines):
            if ln.startswith(f"- {OLD} "):
                self.assertIn(f"- {OLD} (corrected 2026-11-01: {FIX}) [decision]", ln)
                self.assertTrue(i + 1 < len(lines) and lines[i + 1].startswith(f"- {FIX} "),
                                f"the correction must follow the old entry:\n{ctx}")
                return True
        return False


# ─── the index table ─────────────────────────────────────────────────────────

class IndexTableTest(_Case):
    def test_row_recorded_with_date_and_chunk(self):
        rows = self.rows()
        self.assertEqual(len(rows), 1, rows)
        target, path, date, content = rows[0]
        self.assertEqual((target, path, date), (OLD, FIX, "2026-11-01"))
        self.assertIn("egret proxy now has gRPC health checks", content, "chunk_id points at the correction")

    def test_migration_is_idempotent(self):
        before = self.rows()
        self.index()
        self.index()
        self.assertEqual(self.rows(), before)
        self.index("--full")
        self.assertEqual(self.rows(), before)

    def test_rows_are_replaced_when_the_path_is_reindexed(self):
        fix = self.wsd / "t" / "2026-11-01-fix.md"
        fix.write_text(fix.read_text().replace(f"Corrects: {OLD}", "Corrects: t/2026-09-11-c.md"))
        self.assertEqual(self.ix.reindex_paths([fix]), 1)
        self.assertEqual([r[:3] for r in self.rows()],
                         [("workspaces/demo/t/2026-09-11-c.md", FIX, "2026-11-01")],
                         "a workspace-relative target is made vault-relative; the old row is gone")

    def test_three_passes_are_identical(self):
        fix = self.wsd / "t" / "2026-11-01-fix.md"
        snapshots = []
        for _ in range(3):
            self.ix.reindex_paths([fix])
            snapshots.append(self.rows())
        self.assertEqual(snapshots[0], snapshots[1])
        self.assertEqual(snapshots[1], snapshots[2])
        self.assertEqual(len(snapshots[0]), 1)

    def test_deleted_correction_drops_its_rows(self):
        (self.wsd / "t" / "2026-11-01-fix.md").unlink()
        self.ix.incremental()
        self.assertEqual(self.rows(), [])

    def test_lazy_migration_backfills_an_old_index(self):
        db = sqlite3.connect(str(self.vault / "index.db"))
        db.execute("DROP TABLE corrections")
        db.commit()
        db.close()
        # a write that touches another file upgrades the index and backfills
        self.assertEqual(self.ix.reindex_paths([self.wsd / "t" / "2026-09-10-b.md"]), 1)
        self.assertEqual([r[:3] for r in self.rows()], [(OLD, FIX, "2026-11-01")])

    def test_at_most_three_per_chunk_fences_and_layers(self):
        many = "".join(f"- [ref] Correction: x → y (2026-11-0{i}; Corrects: t/2026-09-0{i}-x.md; Source: s)\n"
                       for i in range(1, 6))
        (self.wsd / "t" / "2026-11-02-many.md").write_text(f"---\nslug: x\n---\n{many}")
        fenced = ("## [ref] An example\n\nExample:\n```\n[decision] Correction: a → b (2026-11-03; "
                  "Corrects: t/fenced.md; Source: s)\n```\n")
        (self.wsd / "t" / "2026-11-03-fenced.md").write_text(f"---\nslug: x\n---\n{fenced}")
        line = "- [decision] Correction: a → b (2026-11-04; Corrects: t/2026-09-10-b.md; Source: s)\n"
        for rel in ("journal/2026-11-04.md", "journal/sessions/2026-11-04-abcd1234.md", "memory/MEMORY.md",
                    "research/r/notes.md", "docs/worklog/2026-11-04-mac-aaaaaa.md", "docs/questions.md",
                    "docs/handoff.md"):
            p = self.wsd / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(line)
        self.index()
        rows = self.rows()
        many_rows = [r for r in rows if r[1].endswith("2026-11-02-many.md")]
        self.assertEqual([r[0] for r in many_rows],
                         [f"workspaces/demo/t/2026-09-0{i}-x.md" for i in range(1, 4)], "<= 3 per chunk")
        self.assertFalse([r for r in rows if r[0].endswith("fenced.md")], "fenced examples are not corrections")
        self.assertEqual({r[1] for r in rows}, {FIX, "workspaces/demo/t/2026-11-02-many.md"},
                         "raw / derived layers never declare corrections")

    def test_date_falls_back_to_the_filename(self):
        (self.wsd / "t" / "2026-11-05-nodate.md").write_text(
            "---\nslug: x\n---\n- [ref] Correction: the sweep runs every 60 s (Corrects: t/2026-09-10-b.md; Source: docs)\n")
        self.index()
        self.assertIn(("workspaces/demo/t/2026-09-10-b.md", "workspaces/demo/t/2026-11-05-nodate.md", "2026-11-05"),
                      [r[:3] for r in self.rows()])

    def test_normalize_target(self):
        n = self.ix.normalize_target
        src = "workspaces/demo/t/2026-11-01-fix.md"
        self.assertEqual(n("workspaces/demo/t/a.md", src), "workspaces/demo/t/a.md")
        self.assertEqual(n("t/a.md.", src), "workspaces/demo/t/a.md")
        self.assertEqual(n("./t/a.md", src), "workspaces/demo/t/a.md")
        self.assertEqual(n("~/.gowth-mem/workspaces/x/a.md", src), "workspaces/x/a.md")
        self.assertEqual(n(str(self.vault / "workspaces" / "x" / "a.md"), src), "workspaces/x/a.md")
        self.assertEqual(n("t/a.md", "shared/notes.md"), "shared/t/a.md")
        for bad in ("<path>", "", "t/a.txt", "../etc/passwd.md", "t//a.md", "/etc/x.md", "x" * 400 + ".md"):
            self.assertEqual(n(bad, src), "", bad)

    def test_corrects_refs_shapes(self):
        refs = self.ix.corrects_refs
        src = "workspaces/demo/t/2026-11-09-x.md"
        self.assertEqual(refs("(2026-11-01; Corrects: `t/a.md`; Source: x)", src),
                         [("workspaces/demo/t/a.md", "2026-11-01")])
        self.assertEqual(refs("corrects: t/a.md and Corrects: t/a.md", src),
                         [("workspaces/demo/t/a.md", "2026-11-09")], "deduped; filename date fallback")
        self.assertEqual(refs("Uncorrects: t/a.md · xCorrects: t/b.md", src), [], "word boundary before C")
        self.assertEqual(refs("Corrects: [[2026-09-10-a]]", src), [], "a wikilink is not a path")


class GrowthTest(_Case):
    """CLAUDE.md regex rule 3 (the parser is a scan, not a regex, but it runs on
    the index write path): 10k/20k/40k chars on adversarial shapes, ~2x per
    doubling, through the real entry point and directly."""
    SHAPES = {
        "flood": "Corrects:",
        "unclosed": "Corrects: ((((`[[<<",
        "spaces": "Corrects:" + " " * 40,
        "prefix": "Corrects: workspaces/workspaces/workspaces/",
        "dates": "2026-11-01 2026-11-01 Corrects: x",
    }

    def test_direct_scan_is_linear(self):
        src = "workspaces/demo/t/2026-11-09-x.md"
        for name, unit in self.SHAPES.items():
            times = []
            for size in (10_000, 20_000, 40_000):
                text = "(2026-11-01; Corrects: t/first.md)\n" + (unit * (size // len(unit) + 1))[:size]
                t0 = time.perf_counter()
                for _ in range(3):
                    out = self.ix.corrects_refs(text, src)
                times.append(time.perf_counter() - t0)
                self.assertEqual(out[0], ("workspaces/demo/t/first.md", "2026-11-01"), name)
            self.assertLess(times[2], 2.0, (name, times))
            self.assertLessEqual(times[2], 2.6 * times[1] + 0.02, (name, times))
            self.assertLessEqual(times[1], 2.6 * times[0] + 0.02, (name, times))

    def test_reindex_entry_point_is_linear(self):
        p = self.wsd / "t" / "2026-11-09-hostile.md"
        for name, unit in self.SHAPES.items():
            times = []
            for size in (10_000, 20_000, 40_000):
                body = "- [ref] Correction: a → b (2026-11-09; Corrects: t/2026-09-10-b.md; Source: s)\n"
                p.write_text("---\nslug: x\n---\n" + body + (unit * (size // len(unit) + 1))[:size] + "\n")
                t0 = time.perf_counter()
                self.assertEqual(self.ix.reindex_paths([p]), 1)
                times.append(time.perf_counter() - t0)
                self.assertIn(("workspaces/demo/t/2026-09-10-b.md", "workspaces/demo/t/2026-11-09-hostile.md",
                               "2026-11-09"), [r[:3] for r in self.rows()], name)
            self.assertLess(times[2], 5.0, (name, times))
            self.assertLessEqual(times[2], 2.6 * times[1] + 0.05, (name, times))


# ─── _query: annotation ──────────────────────────────────────────────────────

class QueryAnnotationTest(_Case):
    def test_cli_annotates_the_corrected_hit(self):
        r = subprocess.run([sys.executable, str(SCRIPTS / "_query.py"), "--ws", "demo",
                            "pelican-router ingress gateway"], capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        line = next(ln for ln in r.stdout.splitlines() if ln.startswith(OLD))
        self.assertTrue(line.startswith(f"{OLD} (corrected 2026-11-01: {FIX})  [decision]"), line)
        fix_line = next(ln for ln in r.stdout.splitlines() if ln.startswith(FIX))
        self.assertNotIn("(corrected", fix_line)

    def test_query_ex_attaches_the_correction(self):
        import _query  # type: ignore
        hits = _query.query_ex("demo", "", "pelican-router ingress gateway", limit=10)["hits"]
        by_path = {h["path"]: h for h in hits}
        corr = by_path[OLD]["correction"]
        self.assertEqual((corr["date"], corr["path"]), ("2026-11-01", FIX))
        self.assertIn("Correction: pelican-router → egret proxy", corr["heading"])
        self.assertIsNone(by_path[FIX]["correction"])

    def test_old_index_without_the_table_is_fine(self):
        import _query  # type: ignore
        db = sqlite3.connect(str(self.vault / "index.db"))
        db.execute("DROP TABLE corrections")
        db.commit()
        db.close()
        res = _query.query_ex("demo", "", "pelican-router ingress gateway", limit=10)
        self.assertIsNone(res["error"])
        self.assertTrue(res["hits"])
        self.assertTrue(all(h["correction"] is None for h in res["hits"]))


# ─── per-prompt recall: never the old entry alone ────────────────────────────

class RecallPairTest(_Case):
    def test_no_prompt_injects_the_old_entry_without_its_correction(self):
        served = 0
        for p in GENERIC + REALISTIC + TARGETED:
            if self.assert_never_alone(self.hook(p)):
                served += 1
        self.assertGreaterEqual(served, 2, "positive: targeted prompts do reach the old entry")

    def test_one_slot_left_skips_the_old_entry(self):
        self.settings({"on_prompt_max_entries": 1})
        for p in TARGETED:
            ctx = self.hook(p)
            self.assertNotIn(f"- {OLD} ", ctx, p)
            self.assertLessEqual(sum(1 for ln in ctx.splitlines() if ln.startswith("- ")), 1)

    def test_char_budget_keeps_the_pair_atomic(self):
        for max_chars in (300, 400, 500, 600, 700, 900, 2000):
            self.settings({"on_prompt_max_chars": max_chars})
            for p in TARGETED[:2]:
                ctx = self.hook(p)
                self.assertLessEqual(len(ctx), max_chars)
                self.assert_never_alone(ctx)

    def test_correction_is_reinjected_with_the_old_entry_in_the_same_session(self):
        sid = "pair-session"
        first = self.hook("what does the egret proxy correction say about grpc health checks one fewer hop?", sid)
        self.assertIn(f"- {FIX} ", first, "the correction alone may be served")
        self.assertNotIn(f"- {OLD} ", first)
        second = self.hook(TARGETED[0], sid)
        self.assertTrue(self.assert_never_alone(second), second)

    def test_format_block_pair_is_all_or_nothing(self):
        hits = self.rp.select(TARGETED[0], "demo", self.settings({}), set())
        self.assertEqual([h["path"] for h in hits[:2]], [OLD, FIX])
        self.assertEqual(hits[1]["correction_of"], hits[0]["id"])
        full, shown = self.rp.format_block_ex("demo", hits[:2], 100_000)
        need = len(full)
        for budget in range(need - 400, need + 1, 25):
            block, shown = self.rp.format_block_ex("demo", hits[:2], budget)
            self.assertIn(len(shown), (0, 2), budget)
            if shown:
                self.assertTrue(self.assert_never_alone(block))


if __name__ == "__main__":
    unittest.main()
