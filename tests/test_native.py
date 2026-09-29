"""v4.8 Task 4: `_native` — point a project's Claude Code auto-memory at the vault.

`autoMemoryDirectory` is honoured from `.claude/settings.local.json` and names
the memory directory itself (E2E on 2.1.283). Wiring merges the key, never
clobbers a different value without --force, never touches a git-tracked or
invalid file, and never runs from a hook. Import copies the machine-local
`~/.claude/projects/<slug>/memory/*.md` into the mapped workspace through the
privacy sanitizer.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "hooks" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _native  # type: ignore  # noqa: E402

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@x"}


GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@x", "GIT_CONFIG_NOSYSTEM": "1"}


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "init.defaultBranch=main", "-C", str(cwd), *args],
                          capture_output=True, text=True, check=True, env={**os.environ, **GIT_ENV})


class _NativeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gowth_native_"))
        self.vault = self.tmp / "vault"
        (self.vault / "workspaces" / "demo" / "docs").mkdir(parents=True)
        (self.vault / "workspaces" / "demo" / "workspace.json").write_text('{"name": "demo"}')
        os.environ["GOWTH_MEM_HOME"] = str(self.vault)
        self.proj = self.tmp / "proj"
        self.proj.mkdir()
        self.env = {"HOME": str(self.tmp / "home"), "CLAUDE_CONFIG_DIR": str(self.tmp / "claude")}
        (self.tmp / "claude").mkdir()

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def local(self) -> Path:
        return self.proj / ".claude" / "settings.local.json"

    def _known(self, *projects: Path, with_cwd: bool = False) -> Path:
        """A Claude config dir that knows these projects (a transcript dir per
        slug; with_cwd: a transcript record naming the real cwd, as Claude Code
        writes)."""
        cd = self.tmp / "claude"
        for pr in projects:
            d = cd / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(pr.resolve()))
            d.mkdir(parents=True, exist_ok=True)
            if with_cwd:
                (d / "abc.jsonl").write_text(json.dumps({"type": "user", "cwd": str(pr.resolve()),
                                                          "message": {"content": "hi"}}) + "\n")
        return cd



class WireTest(_NativeCase):
    def test_wire_creates_settings_local_then_already(self):
        self.assertEqual(_native.wire(self.proj, "demo"), "wired")
        data = json.loads(self.local().read_text())
        self.assertEqual(data["autoMemoryDirectory"], _native.memory_dir_value("demo"))
        self.assertEqual(_native.wire(self.proj, "demo"), "already")

    def test_wire_preserves_other_keys_and_indent(self):
        self.local().parent.mkdir()
        self.local().write_text('{\n  "permissions": {"allow": ["Bash"]}\n}\n')
        self.assertEqual(_native.wire(self.proj, "demo"), "wired")
        text = self.local().read_text()
        data = json.loads(text)
        self.assertEqual(data["permissions"], {"allow": ["Bash"]})
        self.assertIn("autoMemoryDirectory", data)
        self.assertTrue(text.startswith("{\n  "))

    def test_wire_conflict_without_force(self):
        self.local().parent.mkdir()
        self.local().write_text('{"autoMemoryDirectory": "/elsewhere"}')
        before = self.local().read_bytes()
        self.assertEqual(_native.wire(self.proj, "demo"), "conflict")
        self.assertEqual(self.local().read_bytes(), before)
        self.assertEqual(_native.wire(self.proj, "demo", force=True), "wired")
        self.assertEqual(json.loads(self.local().read_text())["autoMemoryDirectory"],
                         _native.memory_dir_value("demo"))

    def test_tracked_file_is_left_alone(self):
        subprocess.run(["git", "init", "-q", str(self.proj)], check=True)
        self.local().parent.mkdir()
        self.local().write_text('{"permissions": {}}')
        subprocess.run(["git", "-C", str(self.proj), "add", ".claude/settings.local.json"], check=True)
        subprocess.run(["git", "-C", str(self.proj), "-c", "commit.gpgsign=false", "commit", "-q", "-m", "x"],
                       check=True, env={**os.environ, **GIT_ENV})
        before = self.local().read_bytes()
        self.assertEqual(_native.wire(self.proj, "demo"), "tracked")
        self.assertEqual(self.local().read_bytes(), before)

    def test_invalid_settings_local_is_left_alone(self):
        self.local().parent.mkdir()
        self.local().write_text("{bad json,}")
        self.assertEqual(_native.wire(self.proj, "demo"), "invalid")
        self.assertEqual(self.local().read_text(), "{bad json,}")

    def test_memory_dir_value_uses_tilde_under_home(self):
        home = self.tmp / "home"
        vault = home / ".gowth-mem"
        (vault / "workspaces" / "demo").mkdir(parents=True)
        os.environ["GOWTH_MEM_HOME"] = str(vault)
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(home)
        try:
            self.assertEqual(_native.memory_dir_value("demo"), "~/.gowth-mem/workspaces/demo/memory")
        finally:
            if old_home is not None:
                os.environ["HOME"] = old_home


class IsWiredTest(_NativeCase):
    def test_false_before_and_true_after_wire(self):
        self.assertFalse(_native.is_wired(self.proj, "demo", env=self.env))
        _native.wire(self.proj, "demo")
        self.assertTrue(_native.is_wired(self.proj, "demo", env=self.env))

    def test_tilde_value_resolves(self):
        home = self.tmp / "home"
        vault = home / ".gowth-mem"
        (vault / "workspaces" / "demo").mkdir(parents=True)
        os.environ["GOWTH_MEM_HOME"] = str(vault)
        self.local().parent.mkdir()
        self.local().write_text('{"autoMemoryDirectory": "~/.gowth-mem/workspaces/demo/memory"}')
        self.assertTrue(_native.is_wired(self.proj, "demo", env=self.env))

    def test_env_disable_wins(self):
        _native.wire(self.proj, "demo")
        env = {**self.env, "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
        self.assertFalse(_native.is_wired(self.proj, "demo", env=env))

    def test_host_setting_disable_wins(self):
        _native.wire(self.proj, "demo")
        (self.tmp / "claude" / "settings.json").write_text('{"autoMemoryEnabled": false}')
        self.assertFalse(_native.is_wired(self.proj, "demo", env=self.env))


class ProjectsTest(_NativeCase):
    def test_projects_for_workspaces_maps_existing_globs_and_cwd(self):
        p1 = self.tmp / "p1"
        p1.mkdir()
        cfg = {"workspace_map": {f"{p1}/**": "w1", f"{self.tmp}/missing/**": "w2"}}
        rows = _native.projects_for_workspaces(cfg, cwd=self.proj)
        self.assertIn((p1.resolve(), "w1"), rows)
        self.assertFalse(any(ws == "w2" for _, ws in rows))
        self.assertIn(self.proj.resolve(), [p for p, _ in rows])


class ProjectRootTest(_NativeCase):
    """Review I5: Claude Code reads settings.local.json from the canonical git
    root (the main worktree), not from the cwd."""

    def test_project_root_is_the_git_root_from_a_subdirectory(self):
        git(self.proj, "init", "-q")
        sub = self.proj / "a" / "b"
        sub.mkdir(parents=True)
        self.assertEqual(_native.project_root(sub), self.proj.resolve())

    def test_project_root_of_a_linked_worktree_is_the_main_worktree(self):
        git(self.proj, "init", "-q")
        (self.proj / "f.txt").write_text("x\n")
        git(self.proj, "add", "-A")
        git(self.proj, "commit", "-q", "-m", "base")
        wt = self.tmp / "wt"
        git(self.proj, "worktree", "add", "-q", str(wt), "-b", "wt")
        (wt / "deep").mkdir()
        self.assertEqual(_native.project_root(wt / "deep"), self.proj.resolve())

    def test_project_root_outside_git_is_the_directory_itself(self):
        self.assertEqual(_native.project_root(self.proj), self.proj.resolve())

    def test_wire_and_is_wired_from_a_subdirectory(self):
        git(self.proj, "init", "-q")
        sub = self.proj / "pkg" / "src"
        sub.mkdir(parents=True)
        (self.vault / "config.json").write_text(json.dumps({"workspace_map": {f"{self.proj.resolve()}/**": "demo"}}))
        self.assertEqual(_native.wire(sub, "demo"), "wired")
        self.assertTrue(self.local().is_file(), "settings.local.json belongs at the git root")
        self.assertFalse((sub / ".claude").exists())
        self.assertTrue(_native.is_wired(sub, "demo", self.env))
        self.assertTrue(_native.status(cwd=sub, env=self.env)["wired"])

    def test_double_star_glob_wires_only_the_repos_claude_code_knows(self):
        """Review I5 + live check: the devops glob `/Volumes/Data/Git/fg/**` holds
        ~140 repositories (third-party clones included). Wiring every one would
        spray settings.local.json across the disk; only the repos Claude Code
        has been used in (a `projects/<slug>` dir in its config dir) are wired."""
        base = self.tmp / "fg"
        for name in ("r1", "r2", "r3"):
            (base / name).mkdir(parents=True)
            git(base / name, "init", "-q")
        (base / "plain").mkdir()
        (base / "r1" / "inner").mkdir()
        git(base / "r1" / "inner", "init", "-q")          # nested repo, known too
        cd = self._known(base / "r1", base / "r1" / "inner", base / "plain")
        rows = _native.projects_for_workspaces({"workspace_map": {f"{base}/**": "devops"}}, claude_dir=cd)
        paths = sorted(p for p, ws in rows if ws == "devops")
        # known dirs are wired whether or not they are repos (the host honours a
        # non-repo cwd's .claude/settings.local.json); unknown repos are not
        self.assertEqual(paths, sorted([(base / "r1").resolve(), (base / "r1" / "inner").resolve(),
                                        (base / "plain").resolve()]))
        self.assertNotIn((base / "r3").resolve(), paths, "a repo Claude Code never opened is not wired")
        self.assertNotIn(base.resolve(), paths, "an unknown non-repo glob base must not be wired")

    def test_deep_known_project_is_wired_through_its_transcript_cwd(self):
        """Review m6: a known project five levels under the glob base was
        invisible to the depth-3 walk. Claude Code's transcripts carry the
        real cwd, so a known project is located exactly, at any depth."""
        base = self.tmp / "fg"
        deep = base / "a" / "b" / "c" / "d" / "r5"
        deep.mkdir(parents=True)
        git(deep, "init", "-q")
        cd = self._known(deep, with_cwd=True)
        rows = _native.projects_for_workspaces({"workspace_map": {f"{base}/**": "devops"}}, claude_dir=cd)
        self.assertEqual([p for p, _ in rows], [deep.resolve()])

    def test_double_star_glob_on_a_bare_leaf_dir_wires_the_dir(self):
        leaf = self.tmp / "leaf"
        (leaf / "src").mkdir(parents=True)
        cd = self._known(self.tmp / "elsewhere")
        rows = _native.projects_for_workspaces({"workspace_map": {f"{leaf}/**": "w"}}, claude_dir=cd)
        self.assertEqual([p for p, _ in rows], [leaf.resolve()])

    def test_double_star_glob_without_a_claude_dir_wires_every_repo(self):
        base = self.tmp / "fg"
        for name in ("r1", "r2"):
            (base / name).mkdir(parents=True)
            git(base / name, "init", "-q")
        rows = _native.projects_for_workspaces({"workspace_map": {f"{base}/**": "devops"}},
                                               claude_dir=self.tmp / "no-such-claude-dir")
        self.assertEqual(sorted(p for p, _ in rows), sorted([(base / "r1").resolve(), (base / "r2").resolve()]))

    def test_double_star_glob_on_a_repo_base_wires_the_base(self):
        base = self.tmp / "repo"
        base.mkdir()
        git(base, "init", "-q")
        (base / "sub").mkdir()
        rows = _native.projects_for_workspaces({"workspace_map": {f"{base}/**": "w"}})
        self.assertEqual([p for p, _ in rows], [base.resolve()])


class ImportTest(_NativeCase):
    def _native_memory(self, project: Path, files: dict) -> Path:
        slug = re.sub(r"[^A-Za-z0-9]", "-", str(project.resolve()))
        d = self.tmp / "claude" / "projects" / slug / "memory"
        d.mkdir(parents=True)
        for name, text in files.items():
            (d / name).write_text(text)
        return d

    def setUp(self):
        super().setUp()
        self.p1 = self.tmp / "p1"
        self.p1.mkdir()
        (self.vault / "config.json").write_text(json.dumps(
            {"workspace_map": {f"{self.p1.resolve()}/**": "demo"}}))
        self._native_memory(self.p1, {
            "MEMORY.md": "- [Postgres notes](postgres.md) — connection tips\n",
            "postgres.md": "# Postgres\nkey AKIAIOSFODNN7EXAMPLE must not sync\n",
        })
        self._native_memory(self.tmp / "unmapped", {"MEMORY.md": "- x\n"})

    def test_dry_run_writes_nothing(self):
        rep = _native.import_native(self.tmp / "claude")
        self.assertEqual(rep["imported"], ["postgres.md"])
        self.assertEqual(len(rep["skipped_unmapped"]), 1)
        self.assertFalse((self.vault / "workspaces" / "demo" / "memory").exists())

    def test_apply_copies_sanitizes_and_indexes_once(self):
        rep = _native.import_native(self.tmp / "claude", apply=True)
        mem = self.vault / "workspaces" / "demo" / "memory"
        self.assertTrue((mem / "postgres.md").is_file())
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", (mem / "postgres.md").read_text())
        text = (mem / "MEMORY.md").read_text()
        self.assertIn("- [Postgres notes](postgres.md) — connection tips", text)
        self.assertEqual(rep["index_lines_added"], 1)
        rep2 = _native.import_native(self.tmp / "claude", apply=True)
        self.assertEqual(rep2["index_lines_added"], 0)
        self.assertEqual((mem / "MEMORY.md").read_text().count("Postgres notes"), 1)

    def test_three_way_clash_keeps_every_project_and_dry_run_matches_apply(self):
        """Review I4: the rename was a fixed `<stem>.from-<host>.md`, so a third
        project's copy overwrote the second's, and the dry-run reported three
        imports while apply renamed two into one name."""
        projs = [self.tmp / "work" / n for n in ("alpha", "beta", "gamma")]
        for i, pr in enumerate(projs):
            pr.mkdir(parents=True)
            self._native_memory(pr, {"feedback_testing.md": f"---\nname: testing\n---\nrule from {pr.name} #{i}\n"})
        (self.vault / "config.json").write_text(json.dumps(
            {"workspace_map": {f"{pr.resolve()}/**": "demo" for pr in projs}}))
        dry = _native.import_native(self.tmp / "claude", apply=False)
        rep = _native.import_native(self.tmp / "claude", apply=True)
        for k in ("imported", "renamed", "identical"):
            self.assertEqual(dry[k], rep[k], f"dry-run and apply disagree on {k}")
        mem = self.vault / "workspaces" / "demo" / "memory"
        texts = [f.read_text() for f in mem.glob("feedback_testing*.md")]
        for name in ("alpha", "beta", "gamma"):
            self.assertTrue(any(f"rule from {name}" in t for t in texts), f"{name}'s file was lost")
        self.assertEqual(len(rep["renamed"]), 2)
        self.assertEqual(len(set(rep["renamed"])), 2, "renames must not collide")
        again = _native.import_native(self.tmp / "claude", apply=True)
        self.assertEqual((again["imported"], again["renamed"]), ([], []))
        self.assertEqual(len(again["identical"]), 3)

    def test_memfile_append_takes_the_memfile_lock(self):
        taken = []
        real = _native.file_lock

        class _Rec:
            def __init__(self, name, timeout=30.0):
                taken.append(name)
                self._cm = real(name, timeout=timeout)

            def __enter__(self):
                return self._cm.__enter__()

            def __exit__(self, *a):
                return self._cm.__exit__(*a)

        _native.file_lock = _Rec
        try:
            _native.import_native(self.tmp / "claude", apply=True)
        finally:
            _native.file_lock = real
        self.assertIn("memfile-demo", taken)

    def test_import_maps_native_slugs_by_glob_prefix(self):
        """Review I5: 7 of the live devops memory dirs sit UNDER the glob base;
        exact-slug matching mapped none of them."""
        base = self.tmp / "fg"
        (base / "r1" / "nested").mkdir(parents=True)
        (self.tmp / "fgx" / "r9").mkdir(parents=True)
        (self.vault / "config.json").write_text(json.dumps({"workspace_map": {
            f"{self.p1.resolve()}/**": "demo", f"{base.resolve()}/**": "devops"}}))
        self._native_memory(base / "r1", {"MEMORY.md": "- r1\n", "r1.md": "r1 note\n"})
        self._native_memory(base / "r1" / "nested", {"MEMORY.md": "- nested\n", "nested.md": "nested note\n"})
        self._native_memory(self.tmp / "fgx" / "r9", {"MEMORY.md": "- r9\n"})
        rep = _native.import_native(self.tmp / "claude", apply=False)
        self.assertIn("devops", rep["workspaces"])
        self.assertIn("r1.md", rep["imported"])
        self.assertIn("nested.md", rep["imported"])
        self.assertTrue(any(sl.endswith("-fgx-r9") for sl in rep["skipped_unmapped"]), rep["skipped_unmapped"])
        self.assertFalse(any(sl.endswith("-fg-r1") for sl in rep["skipped_unmapped"]))

    def test_import_maps_a_sibling_dir_the_way_sessions_resolve(self):
        """Review m5: the slug of `bot/AI-trade-v2` starts with the slug prefix
        of `bot/AI-trade/**`, so it imported into `trade` while its sessions
        resolve (first glob match) to `devops`. Known projects map through
        their transcript cwd and the workspace_map globs, in order."""
        bot = self.tmp / "bot"
        (bot / "AI-trade").mkdir(parents=True)
        (bot / "AI-trade-v2").mkdir()
        (self.vault / "config.json").write_text(json.dumps({"workspace_map": {
            f"{(bot / 'AI-trade').resolve()}/**": "trade", f"{bot.resolve()}/**": "devops"}}))
        self._known(bot / "AI-trade-v2", with_cwd=True)
        self._native_memory(bot / "AI-trade-v2", {"MEMORY.md": "- v2\n", "v2.md": "v2 note\n"})
        rep = _native.import_native(self.tmp / "claude", apply=False)
        self.assertIn("devops", rep["workspaces"])
        self.assertNotIn("trade", rep["workspaces"])
        slug = re.sub(r"[^A-Za-z0-9]", "-", str((bot / "AI-trade-v2").resolve()))
        self.assertEqual(rep["mapping"][slug], "devops", "the report must show slug → workspace")

    def test_known_project_without_transcripts_maps_through_its_directory(self):
        """Review R7: a known slug whose transcripts were pruned fell back to the
        slug-prefix rule (AI-trade-old → trade while its sessions say devops).
        The directory is located by slug under the glob bases and mapped
        through the globs in order; a slug that matches no directory is unmapped."""
        bot = self.tmp / "bot"
        (bot / "AI-trade").mkdir(parents=True)
        (bot / "AI-trade-old").mkdir()
        (self.vault / "config.json").write_text(json.dumps({"workspace_map": {
            f"{(bot / 'AI-trade').resolve()}/**": "trade", f"{bot.resolve()}/**": "devops"}}))
        self._native_memory(bot / "AI-trade-old", {"MEMORY.md": "- old\n", "old.md": "old note\n"})
        gone = self.tmp / "bot" / "AI-trade-gone"                      # slug known, directory deleted
        self._native_memory(gone, {"MEMORY.md": "- gone\n"})
        rep = _native.import_native(self.tmp / "claude", apply=False)
        slug_old = re.sub(r"[^A-Za-z0-9]", "-", str((bot / "AI-trade-old").resolve()))
        slug_gone = re.sub(r"[^A-Za-z0-9]", "-", str(gone.resolve()))
        self.assertEqual(rep["mapping"].get(slug_old), "devops")
        self.assertIn(slug_gone, rep["skipped_unmapped"])

    def test_renamed_import_rewrites_its_index_lines(self):
        """Review m4: after a clash rename the project's MEMORY.md index lines
        still pointed at the OTHER project's file."""
        projs = [self.tmp / "work" / n for n in ("alpha", "beta")]
        for i, pr in enumerate(projs):
            pr.mkdir(parents=True)
            self._native_memory(pr, {"notes.md": f"rule from {pr.name}\n",
                                     "MEMORY.md": f"- [Notes](notes.md) — {pr.name}'s notes\n"})
        (self.vault / "config.json").write_text(json.dumps(
            {"workspace_map": {f"{pr.resolve()}/**": "demo" for pr in projs}}))
        rep = _native.import_native(self.tmp / "claude", apply=True)
        self.assertEqual(len(rep["renamed"]), 1)
        renamed = rep["renamed"][0]
        free = _native.split((self.vault / "workspaces" / "demo" / "memory" / "MEMORY.md").read_text())[1]
        self.assertIn("- [Notes](notes.md) — alpha's notes", free)
        self.assertIn(f"- [Notes]({renamed}) — beta's notes", free)
        self.assertNotIn("- [Notes](notes.md) — beta's notes", free)

    def test_clash_keeps_vault_copy_and_renames_incoming(self):
        mem = self.vault / "workspaces" / "demo" / "memory"
        mem.mkdir(parents=True)
        (mem / "postgres.md").write_text("vault version\n")
        rep = _native.import_native(self.tmp / "claude", apply=True)
        self.assertEqual((mem / "postgres.md").read_text(), "vault version\n")
        renamed = [p.name for p in mem.glob("postgres.from-*.md")]
        self.assertEqual(len(renamed), 1)
        self.assertEqual(rep["renamed"], renamed)


class StatusTest(_NativeCase):
    def test_status_keys(self):
        st = _native.status(cwd=self.proj, env=self.env)
        for k in ("workspace", "wired", "memfile_exists", "host_disabled", "projects", "unmapped"):
            self.assertIn(k, st)

    def test_status_lists_unmapped_memory_dirs_and_budget_rule(self):
        """Review M9: spec §4.1 — status names the machine-local memory dirs
        nothing maps, and the free-zone budget is 190 − floor, not a bare 190."""
        (self.vault / "config.json").write_text(json.dumps({"workspace_map": {f"{self.proj.resolve()}/**": "demo"}}))
        slug = re.sub(r"[^A-Za-z0-9]", "-", str((self.tmp / "orphan").resolve()))
        d = self.tmp / "claude" / "projects" / slug / "memory"
        d.mkdir(parents=True)
        (d / "MEMORY.md").write_text("- x\n")
        st = _native.status(cwd=self.proj, env=self.env)
        self.assertIn(slug, st["unmapped"])
        import _memfile  # type: ignore
        mem = self.vault / "workspaces" / "demo" / "memory"
        mem.mkdir(parents=True)
        n = 190 - _memfile.floor_lines() + 1
        (mem / "MEMORY.md").write_text("<!-- gowth-mem:begin ws=demo -->\nx\n<!-- gowth-mem:end -->\n"
                                       + "".join(f"- note {i}\n" for i in range(n)))
        st = _native.status(cwd=self.proj, env=self.env)
        self.assertTrue(st["free_zone_over_budget"])
        self.assertEqual(st["free_zone_lines"], n)


if __name__ == "__main__":
    unittest.main()
