"""v4.8 Task 3: `_memfile` — the managed block inside <ws>/memory/MEMORY.md.

Claude Code attaches the first 200 lines / 25 KB of auto-memory MEMORY.md at
session start, on resume and after every compaction, and — unlike hook
output — never persists it to a preview (measured on 2.1.283). gowth-mem owns a
deterministic block between two markers; everything below the end marker is
Claude's own index and is preserved byte-for-byte.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "hooks" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _memfile  # type: ignore  # noqa: E402

HANDOFF = """# handoff — demo

## 2026-09-13 snapshot
- host:mac 2026-09-13 [blocker] waiting on the vault token
- host:mac 2026-09-13 [done] shipped v4

## 2026-09-11 snapshot
- host:mac 2026-09-11 [done] older work
"""


def _readme(slug: str, title: str, touched: str, summary: str) -> str:
    return (f"---\nslug: {slug}\ntitle: {title}\ntype: topic\nlast_touched: {touched}\n---\n"
            f"# {title}\n\n{summary}\n\n## Aspects (auto)\n")


class _MemfileCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_memfile_")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        os.environ.pop("GOWTH_WORKSPACE", None)
        self.home = Path(self.tmp)
        self.ws = "demo"
        self.wsd = self.home / "workspaces" / self.ws
        (self.wsd / "docs").mkdir(parents=True)
        (self.wsd / "journal").mkdir()
        (self.wsd / "workspace.json").write_text('{"name": "demo"}')
        (self.wsd / "docs" / "handoff.md").write_text(HANDOFF)
        (self.home / "shared").mkdir()
        (self.home / "shared" / "secrets.md").write_text(
            "# secrets (pointers only)\n\n- `FAKE_API_KEY` — env var for the fake API\n"
            "- `DB_URL` — connection string env var\nFAKE_TOKEN=abc123\n")
        (self.home / "settings.json").write_text('{"layout_version": 3}')
        for slug, touched, summary in (("alpha", "2026-09-01", "Alpha is about A."),
                                       ("beta", "2026-09-12", "Beta is about B."),
                                       ("gamma", "2026-08-20", "Gamma is about G.")):
            d = self.wsd / slug
            d.mkdir()
            (d / "00-README.md").write_text(_readme(slug, slug.title(), touched, summary))
        nested = self.wsd / "alpha" / "child"
        nested.mkdir()
        (nested / "00-README.md").write_text(_readme("alpha-child", "Child", "2026-09-10", "Nested topic."))

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)


class RenderTest(_MemfileCase):
    def test_block_within_budget(self):
        block = _memfile.render(self.ws)
        self.assertLessEqual(block.count("\n"), _memfile.MAX_LINES)
        self.assertLessEqual(len(block), _memfile.MAX_CHARS)
        self.assertTrue(block.startswith(_memfile.BEGIN_FMT.format(ws=self.ws)))
        self.assertTrue(block.rstrip("\n").endswith(_memfile.END))

    def test_sections_present_in_order(self):
        block = _memfile.render(self.ws)
        # no index.db in this fixture → "## Recent decisions" is empty and omitted
        heads = ["## Rules", "## Handoff", "## Topics", "## Secrets (pointers only)", "## Using memory"]
        positions = [block.index(h) for h in heads]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("## Recent decisions", block)
        self.assertIn("[gowth-mem:bootstrap workspace=demo", block)

    def test_rules_digest_is_short(self):
        self.assertLessEqual(len(_memfile.rules_digest()), 25)

    def test_secret_values_never_emitted(self):
        block = _memfile.render(self.ws)
        self.assertIn("FAKE_API_KEY", block)
        self.assertIn("DB_URL", block)
        self.assertNotIn("abc123", block)

    def test_handoff_newest_first_and_live_first(self):
        block = _memfile.render(self.ws)
        self.assertLess(block.index("[blocker]"), block.index("[done] shipped"))
        self.assertLess(block.index("2026-09-13 snapshot"), block.index("2026-09-11 snapshot"))

    def test_topic_index_sorted_by_last_touched_with_nested_slug(self):
        lines = _memfile.topic_index(self.ws, max_lines=40)
        slugs = [l.split(" — ")[0][2:] for l in lines]
        self.assertEqual(slugs, ["beta", "alpha/child", "alpha", "gamma"])
        self.assertIn("(last_touched 2026-09-12)", lines[0])
        self.assertIn("Beta is about B.", lines[0])

    def test_shrink_order_drops_decisions_then_topics_then_handoff(self):
        # 60 handoff lines + 40 topics + 10 decisions cannot fit a 90-line budget
        (self.wsd / "docs" / "handoff.md").write_text(
            "## 2026-09-13 s\n" + "".join(f"- host:mac 2026-09-13 [done] item {i}\n" for i in range(80)))
        for i in range(45):
            d = self.wsd / f"t{i:02d}"
            d.mkdir()
            (d / "00-README.md").write_text(_readme(f"t{i:02d}", f"T{i}", "2026-09-05", f"Topic {i}."))
        block = _memfile.render(self.ws, free_zone_lines=100)
        self.assertLessEqual(block.count("\n"), 90)
        self.assertNotIn("## Recent decisions", block)
        lines = block.splitlines()
        t0 = lines.index("## Topics") + 1
        t1 = next(i for i in range(t0, len(lines)) if lines[i].startswith("## "))
        self.assertEqual(len(lines[t0:t1]), 10)
        h0 = lines.index("## Handoff") + 1
        h1 = next(i for i in range(h0, len(lines)) if lines[i].startswith("## "))
        self.assertEqual(len(lines[h0:h1]), 20)
        self.assertTrue(lines[h0].startswith("### 2026-09-13"), "handoff headers are demoted to H3")

    def test_floor_keeps_rules_and_using_memory(self):
        block = _memfile.render(self.ws, free_zone_lines=185)
        self.assertIn("## Rules", block)
        self.assertIn("## Using memory", block)
        self.assertNotIn("## Handoff", block)
        self.assertNotIn("## Topics", block)
        self.assertEqual(block.count("\n"), _memfile.floor_lines())

    def test_render_is_deterministic_across_processes(self):
        code = ("import sys; sys.path.insert(0, %r); import _memfile; "
                "sys.stdout.write(_memfile.render('demo'))" % str(SCRIPTS))
        outs = []
        for _ in range(2):
            r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                               env={**os.environ, "GOWTH_MEM_HOME": self.tmp})
            self.assertEqual(r.returncode, 0, r.stderr)
            outs.append(r.stdout)
        self.assertEqual(outs[0], outs[1])
        self.assertNotIn(os.uname().nodename, outs[0])

    def test_hook_bootstrap_under_8500_and_handoff_first(self):
        text = _memfile.render_hook_bootstrap(self.ws)
        self.assertLess(len(text), 8500)
        self.assertNotIn(_memfile.END, text)
        self.assertLess(text.index("## Handoff"), text.index("## Rules"))
        self.assertTrue(text.startswith("[gowth-mem:bootstrap workspace=demo"))

    def _seed_decisions(self):
        import datetime as _d
        recent = (_d.date.today() - _d.timedelta(days=1)).isoformat()
        old = (_d.date.today() - _d.timedelta(days=40)).isoformat()
        (self.wsd / "beta" / f"{recent}-choice.md").write_text(
            "---\nslug: beta-choice\ntags: []\n---\n"
            "## [decision] Use FTS5 for recall\n\nBecause it is stdlib. Rationale: no deps.\n")
        (self.wsd / "alpha" / f"{recent}-bullet.md").write_text(
            "---\nslug: alpha-bullet\n---\n- [decision] Keep the bullet form too — rationale: legacy files\n")
        (self.wsd / "gamma" / f"{old}-stale.md").write_text(
            "---\nslug: gamma-stale\n---\n## [decision] An old choice nobody needs today\n\nRationale: x.\n")
        return recent

    def test_recent_decisions_come_from_dated_aspects_without_an_index(self):
        """Review I2: decisions are read from the synced files (filename date),
        never from the machine-local index.db."""
        recent = self._seed_decisions()
        self.assertFalse((self.home / "index.db").exists())
        lines = _memfile.recent_decisions(self.ws)
        self.assertTrue(any("[decision]" in l and "FTS5" in l and recent in l for l in lines), lines)
        self.assertTrue(any("bullet form" in l for l in lines), lines)
        self.assertFalse(any("old choice" in l for l in lines), lines)
        self.assertTrue(all(l.startswith(f"- {recent} [decision] ") for l in lines), lines)

    def test_recent_decisions_growth_is_linear(self):
        """Review N2 (CLAUDE.md regex rule 3): the first _DECISION_RE
        (`\\s*(.+?)\\s*$`) was quadratic on whitespace runs — 40k spaces inside
        one decision line cost 5 s per render, on the Stop, SessionStart and
        rebase-merge paths. ~2x per doubling or it is not linear."""
        import datetime as _d
        recent = (_d.date.today() - _d.timedelta(days=1)).isoformat()
        flood = self.wsd / "beta" / f"{recent}-flood.md"
        times = []
        for n in (10_000, 20_000, 40_000):
            flood.write_text("---\nslug: beta-flood\n---\n"
                             "## [decision] Use the router" + " " * n + "because it is fast\n"
                             "- [decision] tabs too" + " \t" * (n // 2) + "end\n")
            t = time.perf_counter()
            lines = _memfile.recent_decisions(self.ws)
            times.append(time.perf_counter() - t)
            self.assertTrue(any("Use the router" in l for l in lines), lines)
        self.assertLess(times[-1], 0.5, times)
        self.assertLess(times[2], max(times[1], 0.01) * 3, times)
        t = time.perf_counter()
        _memfile.render(self.ws)
        self.assertLess(time.perf_counter() - t, 1.0)

    def test_block_identical_with_and_without_index_db(self):
        self._seed_decisions()
        without = _memfile.render(self.ws)
        r = subprocess.run([sys.executable, str(SCRIPTS / "_index.py")], capture_output=True, text=True,
                           env={**os.environ, "GOWTH_MEM_HOME": self.tmp})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.home / "index.db").is_file())
        self.assertEqual(without, _memfile.render(self.ws))
        self.assertIn("## Recent decisions", without)

    def test_block_carries_no_machine_local_version_or_nudge(self):
        """Review I2: a drifted peer's "update not loaded in THIS session" text and
        its version were rendered INTO the synced block and pushed to every
        machine. Those lines belong to the SessionStart header only."""
        import _version  # type: ignore
        base = _memfile.render(self.ws)
        self.assertIn("\n[gowth-mem:bootstrap workspace=demo]\n", base)
        patched = {}
        for mod in (_version, _memfile):
            for name, fake in (("drift_nudge", lambda *a, **k: "=== gowth-mem update not loaded in THIS session ===\nFix here: /reload-plugins"),
                               ("version_tag", lambda *a, **k: " v9.9.9")):
                if hasattr(mod, name):
                    patched[(mod, name)] = getattr(mod, name)
                    setattr(mod, name, fake)
        try:
            drifted = _memfile.render(self.ws)
            hook = _memfile.render_hook_bootstrap(self.ws)
        finally:
            for (mod, name), orig in patched.items():
                setattr(mod, name, orig)
        self.assertEqual(drifted, base)
        self.assertNotIn("v9.9.9", drifted)
        self.assertNotIn("reload-plugins", drifted)
        # the hook FALLBACK bootstrap still tells this machine about its drift
        self.assertIn("v9.9.9", hook)
        self.assertIn("reload-plugins", hook)


class WriteTest(_MemfileCase):
    def test_write_creates_file_and_returns_true(self):
        self.assertTrue(_memfile.write(self.ws))
        p = _memfile.memfile_path(self.ws)
        self.assertEqual(p, self.wsd / "memory" / "MEMORY.md")
        self.assertTrue(p.is_file())

    def test_free_zone_preserved_byte_for_byte(self):
        _memfile.write(self.ws)
        p = _memfile.memfile_path(self.ws)
        p.write_text(p.read_text() + "- my own note [note](note.md)\n\n  indented\n")
        _memfile.write(self.ws)
        text = p.read_text()
        self.assertTrue(text.endswith("- my own note [note](note.md)\n\n  indented\n"))
        self.assertEqual(text.count(_memfile.END), 1)

    def test_begin_marker_without_end_is_free_zone(self):
        p = _memfile.memfile_path(self.ws)
        p.parent.mkdir(parents=True)
        p.write_text(_memfile.BEGIN_FMT.format(ws=self.ws) + "\nstale line kept\n")
        _memfile.write(self.ws)
        text = p.read_text()
        self.assertIn("stale line kept", text.split(_memfile.END, 1)[1])
        self.assertEqual(text.count(_memfile.END), 1)

    def test_hash_gate_no_rewrite(self):
        self.assertTrue(_memfile.write(self.ws))
        p = _memfile.memfile_path(self.ws)
        old = 1_600_000_000
        os.utime(p, (old, old))
        self.assertFalse(_memfile.write(self.ws))
        self.assertEqual(int(p.stat().st_mtime), old)

    def test_sources_changed_tracks_handoff_mtime(self):
        _memfile.write(self.ws)
        self.assertFalse(_memfile.sources_changed(self.ws))
        h = self.wsd / "docs" / "handoff.md"
        h.write_text(HANDOFF + "- host:mac 2026-09-14 [next] newer\n")
        future = time.time() + 5
        os.utime(h, (future, future))
        self.assertTrue(_memfile.sources_changed(self.ws))

    def test_sources_changed_closes_after_a_noop_write(self):
        """Review M3: when the block is unchanged write() never touched MEMORY.md,
        so a newer source kept sources_changed() true on every later Stop
        (re-render + forced reindex each time)."""
        self.assertTrue(_memfile.write(self.ws))
        h = self.wsd / "docs" / "handoff.md"
        future = time.time() + 5
        os.utime(h, (future, future))                    # newer, but same digest
        self.assertTrue(_memfile.sources_changed(self.ws))
        self.assertFalse(_memfile.write(self.ws))        # block unchanged → no rewrite
        self.assertFalse(_memfile.sources_changed(self.ws), "gate must close after a no-op write")

    def test_sources_changed_ignores_index_db(self):
        _memfile.write(self.ws)
        db = self.home / "index.db"
        db.write_bytes(b"")
        future = time.time() + 5
        os.utime(db, (future, future))
        self.assertFalse(_memfile.sources_changed(self.ws))

    def test_block_rendered_by_older_code_is_refreshed(self):
        """Review m1: after an upgrade the block on disk still carried the old
        header (version + drift nudge); with plugin.json out of the sources
        nothing reopened the gate until an unrelated file changed."""
        p = _memfile.memfile_path(self.ws)
        p.parent.mkdir(parents=True, exist_ok=True)
        old = ("<!-- gowth-mem:begin ws=demo -->\n[gowth-mem:bootstrap workspace=demo v4.7.6]\n"
               "=== gowth-mem update not loaded in THIS session ===\n## Rules\nold\n## Using memory\nold\n"
               "<!-- gowth-mem:end -->\n- my note\n")
        p.write_text(old)
        future = time.time() + 5
        os.utime(p, (future, future))                    # newer than every source
        self.assertTrue(_memfile.sources_changed(self.ws), "an older render must reopen the gate")
        self.assertTrue(_memfile.write(self.ws))
        text = p.read_text()
        self.assertIn("\n[gowth-mem:bootstrap workspace=demo]\n", text)
        self.assertNotIn("v4.7.6", text)
        self.assertTrue(text.endswith("- my note\n"))
        self.assertFalse(_memfile.sources_changed(self.ws))

    def test_deleted_source_reopens_the_gate(self):
        _memfile.write(self.ws)
        self.assertFalse(_memfile.sources_changed(self.ws))
        (self.wsd / "gamma" / "00-README.md").unlink()
        self.assertTrue(_memfile.sources_changed(self.ws), "a deleted README must trigger a re-render")
        self.assertTrue(_memfile.write(self.ws))
        self.assertNotIn("- gamma —", _memfile.memfile_path(self.ws).read_text())

    def test_free_zone_lines_shrink_the_block(self):
        _memfile.write(self.ws)
        p = _memfile.memfile_path(self.ws)
        p.write_text(p.read_text() + "".join(f"- note {i}\n" for i in range(120)))
        _memfile.write(self.ws)
        block, _free = _memfile.split(p.read_text())
        self.assertLessEqual(block.count("\n"), 190 - 120 + 1)


if __name__ == "__main__":
    unittest.main()
