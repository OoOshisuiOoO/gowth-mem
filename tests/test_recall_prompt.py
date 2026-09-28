"""v4.8 Task 7: gated per-prompt recall on UserPromptSubmit.

Research (SWE-ContextBench 2026): correctly selected concise memory improves
task success and cost; autonomous retrieval that picks the wrong experience
scores BELOW no memory. So the gate is strict — >= 2 distinct query terms in
the chunk, a bm25 threshold, never the same chunk twice per session, <= 3
entries / 2,000 chars — and the default is silence.
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

GENERIC_PROMPTS = [
    "continue", "ok làm đi", "fix the tests", "tiếp đi", "release", "commit and push",
    "what's next?", "run it again", "yes", "no, revert that", "explain", "làm lại",
    "check again", "why?", "thanks", "show me the diff", "format the file",
    "add a comment", "rename it", "ok",
    "please continue with the next step of the plan and keep going until done",
    "can you run the whole test suite one more time and show me the summary",
]

ENTRIES = {
    "2026-09-10-a.md": "## [decision] Use pelican-router for the ingress gateway\n\nBecause pelican-router terminates gRPC at the edge. Rationale: fewer hops on the hot path.\n",
    "2026-09-10-b.md": "## [ref] heron-cache eviction is LRU with a 512 MB ceiling\n\nSource: heron-cache docs v2.3. The eviction sweep runs every 30 s.\n",
    "2026-09-11-c.md": "## [exp] osprey-queue dropped messages when the consumer lagged\n\nThe osprey-queue prefetch was 1000; lowering it to 50 stopped the drops.\n",
    "2026-09-11-d.md": "## [tool] kestrel-cli 4.2 needs KESTREL_TOKEN in the environment\n\nversion 4.2; without the token every kestrel-cli call exits 3.\n",
    "2026-09-12-e.md": "## [decision] Keep the ingress gateway on pelican-router until the egret proxy ships\n\nRationale: the egret proxy lacks gRPC health checks; pelican-router has them.\n",
    "2026-09-12-f.md": "## [ref] The egret proxy config lives in deploy/egret.yaml\n\nSource: repo. Ingress gateway rules are under the `routes:` key.\n",
}


class _RecallCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gowth_rp_"))
        self.vault = self.tmp / "vault"
        self.proj = self.tmp / "proj"
        self.proj.mkdir()
        self.ws = "demo"
        wsd = self.vault / "workspaces" / self.ws
        (wsd / "t").mkdir(parents=True)
        (wsd / "journal").mkdir()
        (wsd / "memory").mkdir()
        (wsd / "docs").mkdir()
        (wsd / "workspace.json").write_text('{"name": "demo"}')
        (wsd / "t" / "00-README.md").write_text("---\nslug: t\ntitle: T\n---\n# T\n")
        for name, body in ENTRIES.items():
            (wsd / "t" / name).write_text(f"---\nslug: t-{name[11:-3]}\n---\n{body}")
        (wsd / "journal" / "2026-09-10.md").write_text("# j\n\npelican-router ingress gateway journal mention\n")
        (wsd / "memory" / "MEMORY.md").write_text("<!-- gowth-mem:begin ws=demo -->\npelican-router ingress gateway\n<!-- gowth-mem:end -->\n")
        (self.vault / "config.json").write_text(json.dumps({"workspace_map": {f"{self.proj.resolve()}/**": "demo"}}))
        self.settings({})
        self.env = {**os.environ, "GOWTH_MEM_HOME": str(self.vault)}
        self.env.pop("GOWTH_WORKSPACE", None)
        self.env.pop("CLAUDE_SUBAGENT", None)
        r = subprocess.run([sys.executable, str(SCRIPTS / "_index.py")], capture_output=True, text=True, env=self.env)
        assert r.returncode == 0, r.stderr
        os.environ["GOWTH_MEM_HOME"] = str(self.vault)
        import _recall_prompt  # type: ignore
        self.rp = _recall_prompt

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def settings(self, recall: dict) -> dict:
        s = {"layout_version": 3, "recall": {"on_prompt_score_threshold": 0.0, **recall}}
        (self.vault / "settings.json").write_text(json.dumps(s))
        return s

    def event(self, prompt: str, **extra) -> str:
        return json.dumps({"prompt": prompt, "cwd": str(self.proj), "session_id": "sess0001", **extra})

    def run_sh(self, stdin: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(SCRIPTS / "recall-on-prompt.sh")], input=stdin,
                              capture_output=True, text=True, timeout=60,
                              env={**self.env, **(env_extra or {})})

    def run_py(self, stdin: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPTS / "_recall_prompt.py")], input=stdin,
                              capture_output=True, text=True, timeout=60, env=self.env)


GOOD = "why did we choose pelican-router for the ingress gateway last time, and what was the rationale?"


class BashGateTest(_RecallCase):
    def test_good_prompt_injects(self):
        r = self.run_sh(self.event(GOOD))
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        ctx = data["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(data["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertIn("[gowth-mem:recall ws=demo]", ctx)
        self.assertIn("2026-09-10-a.md", ctx)
        self.assertNotIn("journal/", ctx)
        self.assertNotIn("MEMORY.md", ctx)

    def test_quote_in_prompt_head_still_reaches_python(self):
        """Review M2: a double quote inside the first 40 chars made the sed head
        extraction stop early, so the prompt looked < 40 bytes and recall was
        skipped for every quoted prompt."""
        r = self.run_sh(self.event('Why the "pelican-router" choice for the ingress gateway, and what was the rationale?'))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("gowth-mem:recall", r.stdout)

    def test_slash_command_is_silent(self):
        r = self.run_sh(self.event("/mem-recall pelican-router ingress gateway rationale please"))
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_bang_command_is_silent(self):
        r = self.run_sh(self.event("!ls -la the ingress gateway directory for pelican-router"))
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_short_prompt_is_silent(self):
        r = self.run_sh(self.event("pelican-router ingress"))
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_disabled_setting_is_silent(self):
        self.settings({"on_prompt_enabled": False})
        r = self.run_sh(self.event(GOOD))
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_subagent_signals_are_silent(self):
        self.assertEqual(self.run_sh(self.event(GOOD, agent_type="subagent")).stdout, "")
        self.assertEqual(self.run_sh(self.event(GOOD, in_loop=True)).stdout, "")
        self.assertEqual(self.run_sh(self.event(GOOD), {"CLAUDE_SUBAGENT": "1"}).stdout, "")

    def test_empty_stdin_exits_0_silently(self):
        r = self.run_sh("")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        r = self.run_py("{}")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertNotIn("Traceback", r.stderr)


class SelectTest(_RecallCase):
    def test_seeded_query_returns_source_first(self):
        hits = self.rp.select(GOOD, self.ws, self.settings({}), set())
        self.assertTrue(hits)
        self.assertIn("2026-09-10-a.md", hits[0]["path"])
        block = self.rp.format_block(self.ws, hits, 2000)
        self.assertLess(len(block), 2000)
        self.assertIn("[decision]", block)

    def test_one_matching_term_is_not_enough(self):
        hits = self.rp.select("please summarize the pelican situation for me today in detail", self.ws,
                              self.settings({}), set())
        self.assertEqual(hits, [])

    def test_low_coverage_prompt_is_silent(self):
        """Review I1: a generic coding prompt that shares two words with an entry
        must not get it — the chunk has to cover >= 50% of the prompt's content
        terms (the shipped gate injected on 97% of real prompts)."""
        s = self.settings({})
        prompt = ("the ingress gateway build is failing with a type error, can you profile "
                  "the parser module and fix the retry loop before the release")
        self.assertEqual(self.rp.select(prompt, self.ws, s, set()), [])
        # positive control: the same two words with high coverage do inject
        hits = self.rp.select("ingress gateway pelican-router", self.ws, s, set())
        self.assertTrue(hits)

    def test_subject_term_must_be_in_the_chunk(self):
        """Review I1: the profile's subject (first identifier, else the longest
        content word) must appear in the chunk even when coverage is met."""
        s = self.settings({})
        self.assertEqual(self.rp.select("ingress gateway supercalifragilistic", self.ws, s, set()), [])
        hits = self.rp.select("pelican-router ingress gateway", self.ws, s, set())
        self.assertTrue(hits)
        self.assertIn("2026-09-10-a.md", hits[0]["path"])

    def test_min_coverage_is_a_knob(self):
        s = self.settings({"on_prompt_min_coverage": 0.1})
        prompt = ("the ingress gateway build is failing with a supercalifragilistic type error, can "
                  "you profile the parser module and fix the retry loop before the release")
        # coverage 2/13 passes at 0.1, but the subject (the longest content word,
        # "supercalifragilistic") is still required → silent
        self.assertEqual(self.rp.select(prompt, self.ws, s, set()), [])
        s = self.settings({"on_prompt_min_coverage": 0.1})
        hits = self.rp.select("ingress gateway pelican-router with some other unrelated words here", self.ws, s, set())
        self.assertTrue(hits, "subject present + low coverage knob → injects")

    def test_already_injected_ids_are_skipped(self):
        s = self.settings({})
        first = self.rp.select(GOOD, self.ws, s, set())
        ids = {h["id"] for h in first}
        self.assertEqual(self.rp.select(GOOD, self.ws, s, ids), [])

    def test_threshold_blocks_weak_scores(self):
        hits = self.rp.select(GOOD, self.ws, self.settings({"on_prompt_score_threshold": -1000.0}), set())
        self.assertEqual(hits, [])

    def test_auto_threshold_scales_with_corpus_size(self):
        # bm25 magnitudes grow with idf ≈ ln(N/df): a fixed cut-off that silences generic
        # prompts on a 15k-chunk vault would block everything on a 6-chunk one.
        self.assertAlmostEqual(self.rp.auto_threshold(100), 2.5 - 1.55 * 4.605, places=2)
        self.assertAlmostEqual(self.rp.auto_threshold(15_609), 2.5 - 1.55 * 9.656, places=2)
        self.assertLess(self.rp.auto_threshold(15_609), -12.0, "must silence the measured generic scores (-5 … -9.7)")
        self.assertGreater(self.rp.auto_threshold(15_609), -14.0, "must keep the weakest measured real query (-14)")
        self.assertLess(self.rp.auto_threshold(15_000), self.rp.auto_threshold(100))
        self.assertEqual(self.rp.auto_threshold(0), -1.0)
        self.assertEqual(self.rp.auto_threshold(2), -1.0)

    def test_auto_setting_uses_the_live_chunk_count(self):
        s = self.settings({"on_prompt_score_threshold": "auto"})
        self.assertIsNone(self.rp.cfg(s)["score_threshold"])
        thr = self.rp.effective_threshold(s)
        self.assertLess(thr, 0.0)
        self.assertGreater(thr, -5.0, "a 6-chunk fixture must not get the 15k-chunk cut-off")
        hits = self.rp.select("why did osprey-queue drop messages when the consumer lagged behind?", self.ws, s, set())
        self.assertTrue(hits, "a specific seeded query must still pass the auto threshold on a small vault")
        self.assertIn("2026-09-11-c.md", hits[0]["path"])
        for p in GENERIC_PROMPTS:
            self.assertEqual(self.rp.select(p, self.ws, s, set()), [], p)

    def test_caps_entries_and_chars(self):
        s = self.settings({"on_prompt_max_entries": 2})
        hits = self.rp.select("what do we know about the ingress gateway and the egret proxy and pelican-router",
                              self.ws, s, set())
        self.assertLessEqual(len(hits), 2)
        long_hits = [{"id": 1, "path": "x.md", "tag": "ref", "heading": "## [ref] Big", "content": "y " * 3000,
                      "bm25_score": -3.0}]
        block = self.rp.format_block(self.ws, long_hits, 2000)
        self.assertLess(len(block), 2000)
        self.assertLessEqual(max(len(l) for l in block.splitlines()), 900)

    def test_generic_prompts_inject_nothing(self):
        s = self.settings({})
        for p in GENERIC_PROMPTS:
            self.assertEqual(self.rp.select(p, self.ws, s, set()), [], p)

    def test_prompt_is_capped_before_profiling(self):
        s = self.settings({})
        self.assertEqual(len(self.rp.capped_prompt("x" * 40_000, s)), 2000)
        self.assertEqual(len(self.rp.capped_prompt("x" * 40_000, self.settings({"on_prompt_prompt_cap": 500}))), 500)

    def test_growth_probe_is_linear(self):
        s = self.settings({})
        shapes = {"unclosed": "((((", "spaces": "    ", "separators": "--- ", "prefix": "token-"}
        for name, unit in shapes.items():
            times = []
            for size in (10_000, 20_000, 40_000):
                prompt = (unit * (size // len(unit) + 1))[:size]
                t0 = time.perf_counter()
                self.rp.select(prompt, self.ws, s, set())
                times.append(time.perf_counter() - t0)
            self.assertLess(times[2], 1.0, (name, times))
            self.assertLessEqual(times[2], 2.5 * times[1] + 0.05, (name, times))
            self.assertLessEqual(times[1], 2.5 * times[0] + 0.05, (name, times))


class TelemetryTest(_RecallCase):
    def test_state_records_injection_and_dedupes_next_prompt(self):
        r1 = self.run_py(self.event(GOOD))
        self.assertEqual(r1.returncode, 0, r1.stderr)
        self.assertIn("gowth-mem:recall", r1.stdout)
        st = json.loads((self.vault / "state.json").read_text())
        rec = st["session"]["sess0001"]["recall"]
        self.assertEqual(rec["injected"], 1)
        self.assertGreater(rec["chars"], 0)
        self.assertTrue(rec["ids"])
        r2 = self.run_py(self.event(GOOD))
        self.assertEqual((r2.returncode, r2.stdout), (0, ""))

    def test_daily_counter_counts_every_profiled_prompt(self):
        """Review M13: the injection RATE must be observable — a silent prompt
        is counted too (per-day totals, not per-prompt rows)."""
        from datetime import date
        self.run_py(self.event(GOOD))
        silent = ("the build is failing with a type error, can you take a look at the parser "
                  "module and fix the retry loop for me please")
        r = self.run_py(self.event(silent))
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        st = json.loads((self.vault / "state.json").read_text())
        day = st["recall_daily"][date.today().isoformat()]
        self.assertEqual(day["prompts"], 2)
        self.assertEqual(day["injected"], 1)
        self.assertGreaterEqual(day["entries"], 1)


if __name__ == "__main__":
    unittest.main()
