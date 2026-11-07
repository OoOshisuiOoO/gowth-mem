"""v4.9 WP-B1 — the curator's touch points in other modules.

  * `_sync`: `.curator/` is in the default .gitignore and backfilled into an
    existing one (3 passes byte-identical); `_record_autosync` takes the state
    lock and skips the stamp when it is busy;
  * `_forget`: the curator hold — a session log with unprocessed turns is kept
    up to curator.hold_days past its TTL (never a pipeline-only or a fully
    curated log; off with curator.enabled "false"; never past ttl + hold);
  * `_query.py --curated` excludes journal/, docs/worklog/, docs/questions.md
    and memory/;
  * per-prompt recall never serves a worklog (`_recall_prompt.EXCLUDES`);
  * `_prune.collect_files` never returns a worklog (pin);
  * `bootstrap-load.py --report` prints one curator line per workspace.
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
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "hooks" / "scripts"
sys.path.insert(0, str(SCRIPTS))
SCRUB = ("AI_AGENT", "GOWTH_WORKSPACE", "CLAUDE_SUBAGENT", "GOWTH_MEM_OFF", "CLAUDE_CODE_ENTRYPOINT")


class _Vault(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="gowth_curint_"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.vault = self.root / "vault"
        self.wsd = self.vault / "workspaces" / "w"
        (self.wsd / "journal" / "sessions").mkdir(parents=True)
        (self.wsd / "docs").mkdir()
        (self.wsd / "workspace.json").write_text('{"name": "w"}')
        (self.vault / "config.json").write_text(json.dumps({"host_id": "mac", "active_workspace": "w"}))
        (self.vault / "settings.json").write_text("{}")
        self._old = {k: os.environ.get(k) for k in ("GOWTH_MEM_HOME",) + SCRUB}
        for k in SCRUB:
            os.environ.pop(k, None)
        os.environ["GOWTH_MEM_HOME"] = str(self.vault)
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def env(self) -> dict:
        env = {k: v for k, v in os.environ.items() if k not in SCRUB}
        env["GOWTH_MEM_HOME"] = str(self.vault)
        env["CLAUDE_CONFIG_DIR"] = str(self.root / "cc")
        return env

    def settings(self, obj: dict) -> None:
        (self.vault / "settings.json").write_text(json.dumps(obj))

    def log(self, stem: str, users: list, age_days: float = 0) -> Path:
        body = "".join(f"\n## turn {i} — 10:0{i % 10}\n**User:** {u}\n**Claude:** ok\n**Actions:** Read(a.py)\n"
                       for i, u in enumerate(users, 1))
        p = self.wsd / "journal" / "sessions" / f"{stem}.md"
        p.write_text(f"# Session log — {stem[:10]} — {stem[11:]}\n\n_x_\n" + body)
        if age_days:
            t = time.time() - age_days * 86400
            os.utime(p, (t, t))
        return p


class SyncTest(_Vault):
    def test_curator_dir_is_ignored_by_default_and_backfilled(self):
        import _sync  # type: ignore
        self.assertIn(".curator/", _sync._DEFAULT_GITIGNORE.splitlines())
        self.assertIn(".curator/", _sync._REQUIRED_IGNORES)
        _sync.write_default_gitignore(self.vault)
        self.assertIn(".curator/", (self.vault / ".gitignore").read_text().splitlines())
        gi = self.vault / ".gitignore"
        gi.write_text("# mine\nconfig.json\n")
        blobs = []
        for _ in range(3):
            _sync.write_default_gitignore(self.vault)
            blobs.append(gi.read_bytes())
        self.assertEqual(blobs[0], blobs[1])
        self.assertEqual(blobs[1], blobs[2])
        self.assertEqual(blobs[0].decode().splitlines().count(".curator/"), 1)
        self.assertTrue(blobs[0].startswith(b"# mine\nconfig.json\n"))

    def test_record_autosync_takes_the_state_lock(self):
        import _sync  # type: ignore
        from _lock import file_lock  # type: ignore
        sp = self.vault / "state.json"
        sp.write_text(json.dumps({"session": {"s": {"turn_count": 1}}}))
        t0 = time.time()
        with file_lock("state", timeout=2):
            _sync._record_autosync()
        self.assertGreaterEqual(time.time() - t0, 1.5, "it waited for the lock")
        self.assertNotIn("last_autosync", json.loads(sp.read_text()), "stamp skipped while the lock is held")
        _sync._record_autosync()
        st = json.loads(sp.read_text())
        self.assertIn("last_autosync", st)
        self.assertEqual(st["session"], {"s": {"turn_count": 1}}, "other keys survive")


class ForgetHoldTest(_Vault):
    def forget(self) -> dict:
        import _forget  # type: ignore
        return _forget.forget_workspace("w", 7, 75_000, True, False, self.vault)

    def test_unprocessed_log_is_held_past_its_ttl(self):
        held = self.log((date.today() - timedelta(days=10)).isoformat() + "-aaaa1111",
                        ["a real question about the router"], age_days=10)
        r = self.forget()
        self.assertTrue(held.exists(), "unprocessed turns → kept up to hold_days past the TTL")
        self.assertEqual(r["archived"], 0)

    def test_curated_pipeline_only_and_too_old_logs_are_archived(self):
        stem_done = (date.today() - timedelta(days=10)).isoformat() + "-bbbb2222"
        done = self.log(stem_done, ["a curated turn", "another curated turn"], age_days=10)
        d = self.wsd / "docs" / "worklog"
        d.mkdir()
        (d / f"{(date.today() - timedelta(days=9)).isoformat()}-mac-abcdef.md").write_text(
            "---\ntype: worklog\nprocessed: [\"%s:1-2\"]\n---\n" % stem_done)
        pipe = self.log((date.today() - timedelta(days=10)).isoformat() + "-cccc3333",
                        ["Below is a conversation log from a Claude Code coding session. Summarize."], age_days=10)
        old = self.log((date.today() - timedelta(days=40)).isoformat() + "-dddd4444",
                       ["never curated, but beyond ttl + hold"], age_days=40)
        r = self.forget()
        for p in (done, pipe, old):
            self.assertFalse(p.exists(), p.name)
        self.assertEqual(r["archived"], 3)

    def test_hold_off_when_the_curator_is_disabled(self):
        self.settings({"curator": {"enabled": "false"}})
        p = self.log((date.today() - timedelta(days=10)).isoformat() + "-eeee5555", ["question"], age_days=10)
        self.forget()
        self.assertFalse(p.exists())

    def test_hold_days_zero_disables_the_hold(self):
        self.settings({"curator": {"hold_days": 0}})
        p = self.log((date.today() - timedelta(days=10)).isoformat() + "-ffff6666", ["question"], age_days=10)
        self.forget()
        self.assertFalse(p.exists())


class QueryCuratedTest(_Vault):
    def setUp(self):
        super().setUp()
        t = self.wsd / "t"
        t.mkdir()
        (t / "00-README.md").write_text("---\nslug: t\ntitle: T\n---\n# T\n")
        (t / "2026-11-06-note.md").write_text("- [ref] quokkatoken curated entry about the router. Source: a.md\n")
        (self.wsd / "journal" / "2026-11-06.md").write_text("# j\n\nquokkatoken journal mention\n")
        (self.wsd / "journal" / "sessions" / "2026-11-06-abcd1234.md").write_text(
            "# s\n\n## turn 1 — 10:00\n**User:** quokkatoken in a session log\n")
        (self.wsd / "docs" / "worklog").mkdir()
        (self.wsd / "docs" / "worklog" / "2026-11-06-mac-abcdef.md").write_text(
            "---\ntype: worklog\n---\n# Worklog\n\n## Curator actions\n- FACT quokkatoken worklog line\n")
        (self.wsd / "docs" / "questions.md").write_text(
            "# Open questions — w\n\n## Open\n- Q-1a2b3c4d · ask · 2026-11-06 · where is quokkatoken kept\n## Closed\n")
        (self.wsd / "memory").mkdir()
        (self.wsd / "memory" / "notes.md").write_text("# notes\n\nquokkatoken auto memory note\n")
        r = subprocess.run([sys.executable, str(SCRIPTS / "_index.py")], capture_output=True, text=True,
                           env=self.env(), timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)

    def query(self, *extra) -> str:
        r = subprocess.run([sys.executable, str(SCRIPTS / "_query.py"), "--ws", "w", "--limit", "20",
                            *extra, "quokkatoken"], capture_output=True, text=True, env=self.env(), timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_curated_flag_keeps_only_curated_memory(self):
        plain = self.query()
        for part in ("/t/2026-11-06-note.md", "/journal/", "/docs/worklog/", "/docs/questions.md"):
            self.assertIn(part, plain, f"sanity: {part} is indexed and found without --curated")
        cur = self.query("--curated")
        self.assertIn("workspaces/w/t/2026-11-06-note.md", cur)
        for part in ("/journal/", "/docs/worklog/", "/docs/questions.md", "/memory/"):
            self.assertNotIn(part, cur, part)


class RecallExcludesWorklogTest(_Vault):
    def test_worklogs_never_qualify_for_per_prompt_recall(self):
        import _recall_prompt  # type: ignore
        self.assertIn("docs/worklog/", _recall_prompt.EXCLUDES)
        d = self.wsd / "docs" / "worklog"
        d.mkdir()
        text = ("pelicanrouter zebragateway quokkaport configuration migration "
                "pelicanrouter zebragateway quokkaport configuration migration")
        (d / "2026-11-06-mac-abcdef.md").write_text(f"---\ntype: worklog\n---\n# Worklog\n\n## Done\n- [done] {text}\n")
        t = self.wsd / "t"
        t.mkdir()
        (t / "00-README.md").write_text("---\nslug: t\ntitle: T\n---\n# T\n")
        (t / "2026-11-06-x.md").write_text(f"- [ref] {text} — Source: x.md\n")
        r = subprocess.run([sys.executable, str(SCRIPTS / "_index.py")], capture_output=True, text=True,
                           env=self.env(), timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        hits = _recall_prompt.select("pelicanrouter zebragateway quokkaport configuration migration", "w",
                                     {"recall": {"on_prompt_score_threshold": 0}}, set())
        paths = [h["path"] for h in hits]
        self.assertTrue(any(p.endswith("/t/2026-11-06-x.md") for p in paths), paths)
        self.assertFalse(any("/docs/worklog/" in p for p in paths), paths)


class PruneNeverReadsWorklogsTest(_Vault):
    def test_collect_files_skips_worklogs(self):
        import _prune  # type: ignore
        d = self.wsd / "docs" / "worklog"
        d.mkdir()
        (d / "2026-11-06-mac-abcdef.md").write_text("- [decision] something (superseded by x)\n")
        (self.wsd / "docs" / "ref.md").write_text("- [ref] x — Source: y\n")
        files = [str(p) for p in _prune.collect_files("w")]
        self.assertTrue(any(f.endswith("docs/ref.md") for f in files), files)
        self.assertFalse(any("/docs/worklog/" in f for f in files), files)


class BootstrapReportTest(_Vault):
    def test_report_has_one_curator_line_per_workspace(self):
        (self.vault / "workspaces" / "v2" / "docs").mkdir(parents=True)
        d = self.wsd / "docs" / "worklog"
        d.mkdir()
        (d / f"{date.today().isoformat()}-mac-abcdef.md").write_text(
            "---\ntype: worklog\nws: w\nrun: abcdef123456\nphase: 2\nprocessed: []\n---\n")
        r = subprocess.run([sys.executable, str(SCRIPTS / "bootstrap-load.py"), "--report"], input="",
                           capture_output=True, text=True, env=self.env(), timeout=120, cwd=str(self.root))
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = [ln for ln in r.stdout.splitlines() if ln.startswith("curator ws=")]
        self.assertEqual([ln.split(":")[0] for ln in lines], ["curator ws=v2", "curator ws=w"])
        w = next(ln for ln in lines if ln.startswith("curator ws=w:"))
        self.assertIn(f"last {date.today().isoformat()} (mac, run abcdef, phase 2)", w)
        self.assertIn("misses 0", w)
        self.assertIn("open questions 0", w)
        self.assertIn("cards todo 0", w)


if __name__ == "__main__":
    unittest.main()
