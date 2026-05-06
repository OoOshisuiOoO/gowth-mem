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

    def test_pull_rebase_auto_stashes_dirty_tree_and_restores(self):
        auto_sync = load_module("gowth_auto_sync", SCRIPTS / "auto-sync.py")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bare = root / "remote.git"
            local = root / "local"
            other = root / "other"
            subprocess.run(["git", "init", "--bare", "-b", "main", str(bare)], check=True)
            # Seed remote via "other" clone
            subprocess.run(["git", "clone", str(bare), str(other)], check=True, capture_output=True)
            (other / "seed.md").write_text("seed\n")
            for c in (
                ["git", "-C", str(other), "-c", "user.name=t", "-c", "user.email=t@x", "add", "."],
                ["git", "-C", str(other), "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-m", "seed"],
                ["git", "-C", str(other), "push", "origin", "main"],
            ):
                subprocess.run(c, check=True, capture_output=True)
            # Local clone matches remote
            subprocess.run(["git", "clone", str(bare), str(local)], check=True, capture_output=True)
            # Remote advances
            (other / "remote_new.md").write_text("from remote\n")
            for c in (
                ["git", "-C", str(other), "-c", "user.name=t", "-c", "user.email=t@x", "add", "."],
                ["git", "-C", str(other), "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-m", "remote update"],
                ["git", "-C", str(other), "push", "origin", "main"],
            ):
                subprocess.run(c, check=True, capture_output=True)
            # Local has unstaged dirty change (mirrors user's bug)
            (local / "seed.md").write_text("seed\nlocal-dirty\n")
            local_dirty_path = local / "untracked.md"
            local_dirty_path.write_text("untracked content\n")

            rc = auto_sync.pull_rebase(local, "main", quiet=True, remote=str(bare), token=None)
            self.assertEqual(rc, 0)
            # Remote commit pulled in
            self.assertTrue((local / "remote_new.md").is_file())
            # Local dirty restored
            self.assertEqual((local / "seed.md").read_text(), "seed\nlocal-dirty\n")
            self.assertTrue(local_dirty_path.is_file())
            # No stash entry left behind
            stash_list = subprocess.run(
                ["git", "-C", str(local), "stash", "list"], capture_output=True, text=True, check=True)
            self.assertEqual(stash_list.stdout.strip(), "")

    def test_pull_rebase_clean_tree_unaffected(self):
        auto_sync = load_module("gowth_auto_sync", SCRIPTS / "auto-sync.py")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bare = root / "remote.git"
            local = root / "local"
            subprocess.run(["git", "init", "--bare", "-b", "main", str(bare)], check=True)
            subprocess.run(["git", "clone", str(bare), str(local)], check=True, capture_output=True)
            (local / "x.md").write_text("x\n")
            for c in (
                ["git", "-C", str(local), "-c", "user.name=t", "-c", "user.email=t@x", "add", "."],
                ["git", "-C", str(local), "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-m", "x"],
                ["git", "-C", str(local), "push", "-u", "origin", "main"],
            ):
                subprocess.run(c, check=True, capture_output=True)
            rc = auto_sync.pull_rebase(local, "main", quiet=True, remote=str(bare), token=None)
            self.assertEqual(rc, 0)
            stash_list = subprocess.run(
                ["git", "-C", str(local), "stash", "list"], capture_output=True, text=True, check=True)
            self.assertEqual(stash_list.stdout.strip(), "")


class ResearchTests(unittest.TestCase):
    def test_research_compiles(self):
        subprocess.run(
            [sys.executable, "-m", "py_compile", str(SCRIPTS / "_research.py")],
            check=True,
        )

    def test_has_source_ref_accepts_one_colon_inline(self):
        mod = load_module("research", SCRIPTS / "_research.py")
        self.assertTrue(mod.has_source_ref("see dreaming.ts:593 for the sweep loop"))

    def test_has_source_ref_accepts_two_colon_repo_prefix(self):
        mod = load_module("research", SCRIPTS / "_research.py")
        self.assertTrue(mod.has_source_ref("openclaw:src/memory/dreaming.ts:120"))

    def test_has_source_ref_accepts_repo_frontmatter(self):
        mod = load_module("research", SCRIPTS / "_research.py")
        self.assertTrue(mod.has_source_ref("---\ntype: locate\nrepo: openclaw/openclaw\n---\n\nbody"))

    def test_has_source_ref_accepts_source_line(self):
        mod = load_module("research", SCRIPTS / "_research.py")
        self.assertTrue(mod.has_source_ref("Source: openclaw/openclaw cloned 2026-05-06"))

    def test_has_source_ref_rejects_plain_prose(self):
        mod = load_module("research", SCRIPTS / "_research.py")
        self.assertFalse(mod.has_source_ref("Just some plain text without any references."))

    def test_word_count_excludes_frontmatter(self):
        mod = load_module("research", SCRIPTS / "_research.py")
        text = "---\ntype: distilled\ntopic: foo\n---\n\nhello world here"
        self.assertEqual(mod.word_count(text), 3)

    def test_word_count_no_frontmatter(self):
        mod = load_module("research", SCRIPTS / "_research.py")
        self.assertEqual(mod.word_count("hello world here"), 3)

    def test_validate_slug_rejects_invalid_chars(self):
        mod = load_module("research", SCRIPTS / "_research.py")
        with self.assertRaises(SystemExit):
            mod._validate_slug("foo bar")  # space invalid
        with self.assertRaises(SystemExit):
            mod._validate_slug("foo/bar")  # slash invalid
        with self.assertRaises(SystemExit):
            mod._validate_slug("-foo")  # leading dash invalid (anchor [a-z0-9])

    def test_validate_slug_accepts_dash_and_underscore(self):
        mod = load_module("research", SCRIPTS / "_research.py")
        self.assertEqual(mod._validate_slug("foo-bar_2"), "foo-bar_2")

    def test_quality_gate_in_isolated_home(self):
        mod = load_module("research", SCRIPTS / "_research.py")
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            env = os.environ.copy()
            env["GOWTH_MEM_HOME"] = str(home)
            # Bootstrap topic via subprocess (uses isolated home)
            r = subprocess.run(
                [sys.executable, str(SCRIPTS / "_research.py"), "--start", "demo", "--ws", "ws1"],
                env=env, cwd=ROOT, check=True, capture_output=True, text=True,
            )
            self.assertIn("scaffolded", r.stdout)
            # No raw notes besides _locate.md template (which has source attribution
            # via frontmatter) → distill should fail because distilled.md is missing's word
            # gate isn't reachable; but raw notes count >= 1 so not the no-raw-error.
            r2 = subprocess.run(
                [sys.executable, str(SCRIPTS / "_research.py"), "--lint", "demo", "--ws", "ws1"],
                env=env, cwd=ROOT, capture_output=True, text=True,
            )
            # Should FAIL because distilled.md missing
            self.assertEqual(r2.returncode, 1)
            self.assertIn("distilled.md missing", r2.stdout)


if __name__ == "__main__":
    unittest.main()
