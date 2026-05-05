import importlib.util
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "hooks" / "scripts"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class RegressionTests(unittest.TestCase):
    def test_sync_uses_public_remote_url_when_token_is_present(self):
        sync = load_module("gowth_sync", SCRIPTS / "_sync.py")
        auto_sync = load_module("gowth_auto_sync", SCRIPTS / "auto-sync.py")
        self.assertEqual(
            sync.auth_url("https://github.com/example/mem.git", "secret-token"),
            "https://github.com/example/mem.git",
        )
        self.assertEqual(
            auto_sync.auth_url("https://github.com/example/mem.git", "secret-token"),
            "https://github.com/example/mem.git",
        )

    def test_sync_passes_token_via_http_header_without_printing_it(self):
        sync = load_module("gowth_sync", SCRIPTS / "_sync.py")
        auto_sync = load_module("gowth_auto_sync", SCRIPTS / "auto-sync.py")
        for module in (sync, auto_sync):
            cmd = module.git_cmd("https://github.com/example/mem.git", "secret-token", "fetch", "origin")
            joined = " ".join(cmd)
            self.assertIn("http.https://github.com/example/mem.git.extraHeader=AUTHORIZATION: basic ", joined)
            self.assertNotIn("secret-token", joined)

    def test_install_command_uses_shared_workspaces_layout(self):
        text = (ROOT / "commands" / "mem-install.md").read_text()
        self.assertIn("shared", text)
        self.assertIn("workspaces", text)
        self.assertNotIn("mkdir -p ~/.gowth-mem/{topics,docs,journal,skills}", text)

    def test_readme_describes_current_shared_workspaces_layout(self):
        text = (ROOT / "README.md").read_text()
        self.assertIn("shared/", text)
        self.assertIn("workspaces/<ws>/", text)
        self.assertNotIn("├── topics/", text)

    def test_no_command_references_flat_topics_path(self):
        cmds = ROOT / "commands"
        for md in cmds.glob("*.md"):
            text = md.read_text()
            self.assertNotIn(
                "~/.gowth-mem/topics/",
                text,
                f"{md.name} still references old ~/.gowth-mem/topics/ layout",
            )

    def test_no_command_references_bare_agents_md(self):
        cmds = ROOT / "commands"
        for md in cmds.glob("*.md"):
            text = md.read_text()
            for line in text.splitlines():
                if "gowth-mem/AGENTS.md" in line and "shared/AGENTS.md" not in line:
                    if "<ws>/.gowth-mem/AGENTS.md" in line:
                        continue
                    self.fail(
                        f"{md.name}: bare ~/.gowth-mem/AGENTS.md reference "
                        f"(should be shared/AGENTS.md): {line.strip()[:80]}"
                    )

    def test_install_skill_references_shared_agents(self):
        skill = ROOT / "skills" / "mem-install" / "SKILL.md"
        text = skill.read_text()
        self.assertIn("shared/AGENTS.md", text)
        for line in text.splitlines():
            if "gowth-mem/AGENTS.md" in line and "shared/AGENTS.md" not in line:
                self.fail(f"mem-install SKILL.md: bare AGENTS.md ref: {line.strip()[:80]}")

    def test_hooks_can_write_debug_log_when_enabled(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "mem"
            env = os.environ.copy()
            env["GOWTH_MEM_HOME"] = str(home)
            env["GOWTH_MEM_DEBUG"] = "1"
            code = "import sys; sys.path.insert(0, 'hooks/scripts'); from _debug import log_debug; log_debug('unit-test', 'hello')"
            subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, check=True)
            log = home / "logs" / "hooks.log"
            self.assertTrue(log.exists())
            self.assertIn("unit-test: hello", log.read_text())


class ConsolidationTests(unittest.TestCase):
    def test_consolidate_compiles(self):
        subprocess.run(
            [sys.executable, "-m", "py_compile", str(SCRIPTS / "_consolidate.py")],
            check=True,
        )

    def test_weighted_score_sums_to_one(self):
        mod = load_module("consolidate", SCRIPTS / "_consolidate.py")
        total = (mod.W_FREQUENCY + mod.W_RELEVANCE + mod.W_DIVERSITY
                 + mod.W_RECENCY + mod.W_CONSOLIDATION + mod.W_RICHNESS)
        self.assertAlmostEqual(total, 1.0, places=5)

    def test_normalize_signals_handles_empty(self):
        mod = load_module("consolidate", SCRIPTS / "_consolidate.py")
        self.assertEqual(mod.normalize_signals([]), [])

    def test_normalize_signals_scales_to_one(self):
        mod = load_module("consolidate", SCRIPTS / "_consolidate.py")
        signals = [
            {"frequency": 10, "relevance": 2, "diversity": 5,
             "recency": 0.8, "consolidation": 3, "richness": 4},
            {"frequency": 5, "relevance": 1, "diversity": 2,
             "recency": 0.4, "consolidation": 1, "richness": 2},
        ]
        normed = mod.normalize_signals(signals)
        self.assertAlmostEqual(normed[0]["frequency"], 1.0)
        self.assertAlmostEqual(normed[1]["frequency"], 0.5)

    def test_deep_phase_splits_by_score(self):
        mod = load_module("consolidate", SCRIPTS / "_consolidate.py")
        candidates = [
            ("high.md", {"frequency": 20, "relevance": 5, "diversity": 10,
                         "recency": 1.0, "consolidation": 10, "richness": 15}),
            ("low.md", {"frequency": 1, "relevance": 0.1, "diversity": 0,
                        "recency": 0.01, "consolidation": 0, "richness": 0}),
        ]
        result = mod.deep_phase(candidates)
        self.assertIn("promote", result)
        self.assertIn("maintain", result)
        self.assertIn("prune_candidates", result)
        all_paths = ([s["path"] for s in result["promote"]]
                     + [s["path"] for s in result["maintain"]]
                     + [s["path"] for s in result["prune_candidates"]])
        self.assertEqual(len(all_paths), 2)


class LintTests(unittest.TestCase):
    def test_lint_compiles(self):
        subprocess.run(
            [sys.executable, "-m", "py_compile", str(SCRIPTS / "_lint.py")],
            check=True,
        )

    def test_jaccard_identical_strings(self):
        mod = load_module("lint", SCRIPTS / "_lint.py")
        self.assertAlmostEqual(
            mod.jaccard("hello world testing stuff", "hello world testing stuff"), 1.0)

    def test_jaccard_disjoint_strings(self):
        mod = load_module("lint", SCRIPTS / "_lint.py")
        self.assertAlmostEqual(
            mod.jaccard("alpha beta gamma delta", "epsilon zeta theta iota"), 0.0)

    def test_find_contradictions_returns_list(self):
        mod = load_module("lint", SCRIPTS / "_lint.py")
        entries = [
            {"type": "ref", "text": "EMA cross strategy works optimal trending high volume market",
             "source": "backtest_001", "file": "a.md", "line": 1},
            {"type": "ref", "text": "EMA cross strategy fails suboptimal trending high volume conditions",
             "source": "backtest_002", "file": "b.md", "line": 1},
        ]
        contradictions = mod.find_contradictions(entries)
        self.assertIsInstance(contradictions, list)

    def test_extract_entries_parses_typed_lines(self):
        mod = load_module("lint", SCRIPTS / "_lint.py")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Topic\n- [ref] Some verified fact — Source: docs\n- [exp] Debug lesson\n")
            f.flush()
            try:
                entries = mod.extract_entries(Path(f.name))
                self.assertEqual(len(entries), 2)
                self.assertEqual(entries[0]["type"], "ref")
                self.assertEqual(entries[1]["type"], "exp")
            finally:
                os.unlink(f.name)


class MultiSignalTests(unittest.TestCase):
    def test_recall_compiles(self):
        subprocess.run(
            [sys.executable, "-m", "py_compile", str(SCRIPTS / "recall-active.py")],
            check=True,
        )

    def test_multi_signal_score_unknown_path_returns_tier(self):
        mod = load_module("recall", SCRIPTS / "recall-active.py")
        with tempfile.NamedTemporaryFile(suffix=".md") as tf:
            p = Path(tf.name)
            score = mod.multi_signal_score(p, {"files": {}}, 80, time.time())
            self.assertAlmostEqual(score, 0.8, places=2)

    def test_multi_signal_score_with_history_boosts(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            topic_dir = home / "workspaces" / "test" / "topic"
            topic_dir.mkdir(parents=True)
            (topic_dir / "topic.md").write_text("- [ref] test entry\n")
            env = os.environ.copy()
            env["GOWTH_MEM_HOME"] = str(home)
            code = (
                "import sys, time; sys.path.insert(0, 'hooks/scripts');"
                "import importlib.util;"
                "spec = importlib.util.spec_from_file_location('recall', 'hooks/scripts/recall-active.py');"
                "mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod);"
                "now = time.time();"
                "from pathlib import Path; p = Path('" + str(topic_dir / "topic.md") + "');"
                "state = {'files': {'workspaces/test/topic/topic.md': {"
                "'count': 15, 'last_seen': now - 3600,"
                "'query_hashes': ['a','b','c','d','e'],"
                "'days_seen': ['2026-05-01','2026-05-02','2026-05-03']}}};"
                "s1 = mod.multi_signal_score(p, state, 80, now);"
                "s2 = mod.multi_signal_score(p, {'files': {}}, 80, now);"
                "assert s1 > s2, f'{s1} not > {s2}'"
            )
            subprocess.run(
                [sys.executable, "-c", code], cwd=ROOT, env=env, check=True)


if __name__ == "__main__":
    unittest.main()
