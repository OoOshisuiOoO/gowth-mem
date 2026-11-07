"""v4.9 WP-B1 — `_curate.py`: the curator digest, the worklog and the coverage cursor.

Spec §4.3/§4.4. Pinned here:
  * the readers (turn heading, cwd header, worklog frontmatter, processed
    ranges, worklog names) — positive signals and refusals;
  * the digest: every section has its own budget and a `+N more — <how>` line
    when cut (never a silent cut); only turns PRINTED in §2 go into
    `processed`; pipeline turns never appear; short turns collapse;
    corrections carry the previous Claude line;
  * the coverage cursor: per-log union over every worklog of the workspace
    (any host) within raw_ttl_days + hold_days;
  * `commit`: frontmatter from the prep JSON (never from the body), phase 1/2
    rules, every rejection, `unchanged`, 3 identical passes;
  * `prep`: --dry writes nothing; non-dry writes .curator/<ws>/prep-* and the
    ignore line, prints the path + a <= 1,500-char index; `already-curated`
    from the local tree and from origin/<branch> after a real fetch;
  * `due` (pure, fixed clock): hour, curated today, one host per workspace +
    failover, curator.hosts, misses → paused, attempts, the signal floor.

Everything runs on a per-test scratch GOWTH_MEM_HOME; CLI cases run the real
entry point (`python3 _curate.py …`) as a subprocess.
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
from datetime import date, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "hooks" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _curate  # type: ignore  # noqa: E402

SCRUB = ("AI_AGENT", "GOWTH_WORKSPACE", "CLAUDE_SUBAGENT", "GOWTH_MEM_OFF", "CLAUDE_CODE_ENTRYPOINT")
NOW = "2026-11-07T10:40"
TODAY = "2026-11-07"


def _git(cwd, *args):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main",
                        "-C", str(cwd), *args], capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout


class _Vault(unittest.TestCase):
    WS = "w"

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="gowth_cur_"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.vault = self.root / "vault"
        self.wsd = self.vault / "workspaces" / self.WS
        (self.wsd / "journal" / "sessions").mkdir(parents=True)
        (self.wsd / "docs").mkdir(parents=True)
        (self.wsd / "workspace.json").write_text(json.dumps({"name": self.WS}))
        (self.vault / "config.json").write_text(json.dumps({"host_id": "mac"}))
        self._old_env = {k: os.environ.get(k) for k in ("GOWTH_MEM_HOME",) + SCRUB}
        for k in SCRUB:
            os.environ.pop(k, None)
        os.environ["GOWTH_MEM_HOME"] = str(self.vault)
        self.addCleanup(self._restore_env)
        self.settings({})

    def _restore_env(self):
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def settings(self, obj: dict) -> None:
        (self.vault / "settings.json").write_text(json.dumps(obj))

    def env(self, **extra) -> dict:
        env = {k: v for k, v in os.environ.items() if k not in SCRUB}
        env["GOWTH_MEM_HOME"] = str(self.vault)
        env["CLAUDE_CONFIG_DIR"] = str(self.root / "claude-config")
        env.update(extra)
        return env

    def cli(self, *args, inp: str | None = None, timeout: int = 120) -> str:
        r = subprocess.run([sys.executable, str(SCRIPTS / "_curate.py"), *args], capture_output=True,
                           text=True, env=self.env(), input=inp, timeout=timeout)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        return r.stdout

    def write_log(self, stem: str, turns: list, cwd: str | None = None, extra: str = "") -> Path:
        """turns: [(n, "HH:MM", user, claude, actions)]"""
        lines = [f"# Session log — {stem[:10]} — {stem[11:]}"]
        if cwd:
            lines.append(f"<!-- cwd: {cwd} -->")
        lines += ["", "_Auto-captured turn log (user prompt + Claude summary + actions trace)._"]
        for n, hhmm, user, claude, actions in turns:
            lines += ["", f"## turn {n} — {hhmm}", f"**User:** {user}", f"**Claude:** {claude}",
                      f"**Actions:** {actions}"]
        p = self.wsd / "journal" / "sessions" / f"{stem}.md"
        p.write_text("\n".join(lines) + "\n" + extra)
        return p

    def write_worklog(self, day: str, host: str, run6: str, processed: list, ws: str | None = None) -> Path:
        ws = ws or self.WS
        d = self.vault / "workspaces" / ws / "docs" / "worklog"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{day}-{host}-{run6}.md"
        p.write_text("---\ntype: worklog\nws: %s\ndate: %s\nhost: %s\nrun: %s000000\nphase: 2\n"
                     "window_start: %sT00:00\nwindow_end: %sT09:00\nvault_sha: \"\"\nprocessed: %s\n"
                     "stats: turns=0\n---\n# Worklog\n\n## Curator actions\n"
                     % (ws, day, host, run6, day, day, json.dumps(processed)))
        return p

    def files(self) -> dict:
        out = {}
        for p in sorted(self.vault.rglob("*")):
            if p.is_file():
                out[str(p.relative_to(self.vault))] = (p.read_bytes(), p.stat().st_mtime_ns)
        return out


def _turn(n, user=None, actions="Edit(x.py) → Bash(pytest -q)", hhmm=None):
    return (n, hhmm or f"10:{n % 60:02d}", user or f"please implement feature number {n} with tests",
            f"did thing {n}", actions)


# ── readers ──────────────────────────────────────────────────────────────────

class ReadersTest(unittest.TestCase):
    def test_turn_heading(self):
        r = _curate.read_turn_heading
        self.assertEqual(r("## turn 3 — 10:40"), (3, "10:40"))
        self.assertEqual(r("## turn 12"), (12, ""))
        self.assertEqual(r("## turn 7 — 09:05 extra"), (7, "09:05"))
        self.assertEqual(r("## turn 7 — 9:05"), (7, ""))
        for bad in ("## turn 0", "## turn 12abc", "## turn ", "## turn 1234567890", "### turn 3",
                    "## turnover 3", "## turn -3", "## turn ٣", None, 5):
            self.assertIsNone(r(bad), bad)

    def test_cwd_header(self):
        r = _curate.read_cwd_header
        self.assertEqual(r("# Session log — x\n<!-- cwd: /Users/me/My Repo/a -->\n\nbody"), "/Users/me/My Repo/a")
        self.assertEqual(r("# t\n<!-- cwd: /x -->"), "/x")
        for bad in ("# t\n<!-- cwd: relative/x -->\n", "# t\n<!-- cwd: /x\n", "# t\n\n<!-- cwd: /x -->\n",
                    "# t\n<!-- cwd: /a --> b -->\n", "# t\n<!-- cwd: /" + "a" * 600 + " -->\n", "", None,
                    "<!-- cwd: /x -->\n"):
            self.assertIsNone(r(bad), bad)

    def test_parse_log_fields_and_review_block(self):
        text = ("# Session log — 2026-11-07 — abcd1234\n<!-- cwd: /r -->\n\n## turn 1 — 10:00\n**User:** hi there\n"
                "**Claude:** hello\n**Actions:** Read(a.py)\n\n## [self-review] 2026-11-07\n**User:** not a turn\n\n"
                "## turn 2 — 10:05\n**User:** next\n")
        got = _curate.parse_log(text)
        self.assertEqual(got["cwd"], "/r")
        self.assertEqual([(t["n"], t["user"]) for t in got["turns"]], [(1, "hi there"), (2, "next")])
        self.assertEqual(got["turns"][0]["actions"], "Read(a.py)")

    def test_ranges_round_trip_and_refusals(self):
        self.assertEqual(_curate.format_ranges([5, 1, 2, 3, 9, 7, 8, 3]), "1-3,5,7-9")
        self.assertEqual(_curate.parse_ranges("1-3,5,7-9"), [(1, 3), (5, 5), (7, 9)])
        for bad in ("", "1-", "3-1", "a", "1,,2", "0", "99999999999", "1-99999999999", None):
            self.assertIsNone(_curate.parse_ranges(bad), bad)
        cov = _curate.parse_processed(["2026-11-07-abcd1234:1-3,5", "2026-11-07-abcd1234:4",
                                       "junk", 5, "not-a-stem:1", "2026-11-07-abcd1234:x"])
        self.assertEqual(cov, {"2026-11-07-abcd1234": [(1, 5)]})
        self.assertTrue(_curate._covered(cov, "2026-11-07-abcd1234", 4))
        self.assertFalse(_curate._covered(cov, "2026-11-07-abcd1234", 6))

    def test_worklog_names(self):
        p = _curate.parse_worklog_name
        self.assertEqual(p("2026-11-07-mac-3f2a9c.md"), ("2026-11-07", "mac", "3f2a9c"))
        self.assertEqual(p("2026-11-07-my-host-1-3f2a9c.md"), ("2026-11-07", "my-host-1", "3f2a9c"))
        for bad in ("2026-11-07-mac-3f2a9.md", "2026-11-07-Mac-3f2a9c.md", "2026-13-07-mac-3f2a9c.md",
                    "2026-11-07--3f2a9c.md", "notes.md", "2026-11-07-mac-3f2a9c.txt"):
            self.assertIsNone(p(bad), bad)

    def test_correction_shapes(self):
        yes = ("No, that's wrong", "nope — use port 8080", "Wrong file", "that's not what I asked",
               "thats wrong again", "not what i meant", "You forgot the tests", "you broke the build",
               "stop, revert it", "sai rồi, dùng cổng khác", "Không đúng", "nhầm rồi", "đính chính: x",
               "làm lại đi", "hoàn tác giúp", "please revert that change", "can you undo your last edit",
               "rollback this now", "revert cái đó", "  No.", "that’s wrong")
        no = ("nothing else to do", "nowhere near done", "please add tests", "stopwatch module",
              "the wrongness metric", "revert-proxy settings are fine", "", "undo")
        for t in yes:
            self.assertTrue(_curate.is_correction(t), t)
        for t in no:
            self.assertFalse(_curate.is_correction(t), t)
        # only the first 80 chars count
        self.assertFalse(_curate.is_correction("x" * 90 + " no that's wrong"))

    def test_handoff_four_styles(self):
        text = "\n".join([
            "# Handoff — trade (2026-01-01)",
            "## Entries",
            "- host:mac 2026-10-01 [next] style one own date",
            "host:mac [doing] style two undated heading",
            "## host:ndp 2026-10-02 ~11:45 — session",
            "- [ ] style three unchecked box",
            "- [x] done box is not live",
            "### Next",
            "- [ ] style three under an undated H3 inherits",
            "## host:mac — 2026-10-03 run (see 2026-11-30 later)",
            "**Next:** style four bold label",
            "**Vừa xong:** done label is not live",
            "- host:mac 2026-10-04 [done] done tag is not live",
            "- host:mac 2026-10-05 [done+blocker] compound is live",
            "  - [ ] indented continuation is not an item",
        ])
        items = _curate.handoff_live_items(text)
        got = [(it["tag"], it["date"]) for it in items]
        self.assertEqual(got, [("next", "2026-10-01"), ("doing", None), ("todo", "2026-10-02"),
                               ("todo", "2026-10-02"), ("next", "2026-10-03"),
                               ("done+blocker", "2026-10-05")])
        self.assertEqual(items[0]["line"], 3)
        self.assertIn("style one own date", items[0]["text"])

    def test_worklog_meta_reader(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        p = root / "x.md"
        p.write_text('---\ntype: worklog\nws: w\nphase: 2\nvault_sha: ""\nprocessed: ["a:1"]\n---\nbody\n')
        meta = _curate.read_worklog_meta(p)
        self.assertEqual((meta["ws"], meta["phase"], meta["vault_sha"], meta["processed"]), ("w", 2, "", ["a:1"]))
        p.write_text('---\ntype: note\n---\n')
        self.assertIsNone(_curate.read_worklog_meta(p))
        p.write_text('---\ntype: worklog\nprocessed: [unclosed\n---\n')
        self.assertEqual(_curate.read_worklog_meta(p)["processed"], [])
        p.write_text('no frontmatter')
        self.assertIsNone(_curate.read_worklog_meta(p))


class HostIdTest(unittest.TestCase):
    def test_normalize_and_fallbacks(self):
        import _home  # type: ignore
        self.assertEqual(_home.host_id({"host_id": "Sh1Su102s-Mac_mini.local"}), "sh1su102s-mac-mini-local")
        self.assertEqual(_home.host_id({"host_id": "  --Mac--  "}), "mac")
        self.assertLessEqual(len(_home.host_id({"host_id": "a" * 80})), 24)
        self.assertEqual(_home.host_id({"host_id": "a" * 23 + "-b"}), "a" * 23)
        from unittest import mock
        with mock.patch("socket.gethostname", return_value="Duys-MacBook.local"):
            self.assertEqual(_home.host_id({}), "duys-macbook")
            self.assertEqual(_home.host_id({"host_id": "!!!"}), "duys-macbook")
        with mock.patch("socket.gethostname", return_value="...."):
            self.assertEqual(_home.host_id({}), "host")
        with mock.patch("socket.gethostname", side_effect=OSError("x")):
            self.assertEqual(_home.host_id({"host_id": 5}), "host")


# ── the digest ───────────────────────────────────────────────────────────────

class DigestTest(_Vault):
    def test_budget_cut_has_more_line_and_processed_is_printed_turns_only(self):
        self.settings({"curator": {"max_prep_chars": 8000}})
        self.write_log("2026-11-06-abcd1234", [_turn(n) for n in range(1, 41)])
        out = self.cli("prep", "--ws", self.WS, "--now", NOW)
        prep_md = Path(out.splitlines()[0])
        md = prep_md.read_text()
        meta = json.loads(prep_md.with_suffix(".json").read_text())
        printed = [int(ln.split(" turn ")[1].split()[0]) for ln in md.splitlines()
                   if ln.startswith("- 2026-11-06-abcd1234 turn ")]
        self.assertGreater(len(printed), 3)
        self.assertLess(len(printed), 40)
        self.assertEqual(printed, list(range(1, len(printed) + 1)), "oldest first, no gaps")
        self.assertIn(f"+{40 - len(printed)} more — left unprocessed; the next prep starts with them", md)
        self.assertEqual(meta["processed"], [f"2026-11-06-abcd1234:1-{len(printed)}"])
        self.assertEqual(meta["turns"], len(printed))
        self.assertEqual(meta["unprocessed_left"], 40 - len(printed))
        self.assertLessEqual(len(md), 8000 + 2000)

    def test_unprocessed_after_commit_is_the_cut_turns(self):
        self.settings({"curator": {"max_prep_chars": 8000}})
        self.write_log("2026-11-06-abcd1234", [_turn(n) for n in range(1, 41)])
        before = _curate.unprocessed(self.WS, today=TODAY)
        self.assertEqual(before, {"2026-11-06-abcd1234": list(range(1, 41))})
        out = self.cli("prep", "--ws", self.WS, "--now", NOW)
        meta = json.loads(Path(out.splitlines()[0]).with_suffix(".json").read_text())
        n = meta["turns"]
        body = "## Done\n- [done] a — evidence: session abcd1234 turn 1\n## Curator actions\n- FACT x — y\n"
        got = self.cli("commit", "--ws", self.WS, "--run", meta["run"], "--phase", "1", "--worklog", "-", inp=body)
        self.assertTrue(got.strip().endswith("\twritten"), got)
        after = _curate.unprocessed(self.WS, today=TODAY)
        self.assertEqual(after, {"2026-11-06-abcd1234": list(range(n + 1, 41))})
        # the next prep starts with the cut turns
        out2 = self.cli("prep", "--ws", self.WS, "--now", "2026-11-07T11:00", "--dry")
        first = next(ln for ln in out2.splitlines() if ln.startswith("- 2026-11-06-abcd1234 turn "))
        self.assertTrue(first.startswith(f"- 2026-11-06-abcd1234 turn {n + 1} "), first)
        # the window continues from the worklog's window_end
        self.assertIn("- window: 2026-11-07T10:40 → 2026-11-07T11:00 (informational)", out2)

    def test_every_cut_section_says_how_many_more(self):
        self.settings({"curator": {"max_prep_chars": 8000, "stale_days": 1, "max_research": 1}})
        self.write_log("2026-11-06-abcd1234", [_turn(n) for n in range(1, 60)])
        topic = self.wsd / "t"
        topic.mkdir()
        (topic / "00-README.md").write_text("---\nslug: t\ntitle: T\n---\n# T\n\n## TL;DR\n\n> T topic\n")
        (topic / "2026-11-06-notes.md").write_text(
            "\n".join(f"- [ref] entry number {i} about the curator digest budget — Source: x.md" for i in range(80)))
        (topic / "2025-01-01-old.md").write_text(
            "\n".join(f"- [hypothesis] old guess {i} about routing — Verify: run it" for i in range(80)))
        (self.wsd / "docs" / "handoff.md").write_text(
            "## Entries\n" + "".join(f"- host:mac 2026-10-{(i % 28) + 1:02d} [next] stale item {i}\n" for i in range(80)))
        import _gaps  # type: ignore
        for i in range(40):
            _gaps.add(self.WS, "research" if i % 2 else "ask", f"what is the answer to question number {i} {'x' * i}",
                      force=True, settings={})
        out = self.cli("prep", "--ws", self.WS, "--now", NOW, "--dry")
        for sid, hint in (("2", "left unprocessed"), ("4c", "the aspect files of the §7 topics hold them"),
                          ("5", "read docs/handoff.md"), ("6", f"_gaps.py --ws {self.WS} list"),
                          ("9", "--type hypothesis"), ("10", "list --kind research")):
            sec = out.split(f"## §{sid} ", 1)[1].split("\n## §", 1)[0]
            self.assertRegex(sec, r"\n\+\d+ more — ", f"§{sid} was cut silently")
            self.assertIn(hint, sec, sid)
        self.assertIn("[next]", out.split("## §5 ", 1)[1])

    def test_short_turns_collapse_and_count_as_processed(self):
        self.write_log("2026-11-06-abcd1234", [_turn(1), _turn(2, user="ok", actions=""),
                                                _turn(3, user="tiếp", actions=""), _turn(4)])
        out = self.cli("prep", "--ws", self.WS, "--now", NOW)
        md = Path(out.splitlines()[0]).read_text()
        self.assertIn("- 2026-11-06-abcd1234: 2 short turn(s) without actions (turns 2-3)", md)
        meta = json.loads(Path(out.splitlines()[0]).with_suffix(".json").read_text())
        self.assertEqual(meta["processed"], ["2026-11-06-abcd1234:1-4"])

    def test_pipeline_turns_are_never_printed_counted_or_processed(self):
        self.settings({"capture": {"pipeline_prefixes": ["[bot-round]"]}})
        self.write_log("2026-11-06-abcd1234", [
            _turn(1, user="STAGE=VERIFY Verify the mention below BEFORE the operator decides anything."),
            _turn(2, user="### TASK 469044d829 R13 ### STAGE=EXECUTE Operator APPROVED."),
            _turn(3, user="Below is a conversation log from a Claude Code coding session. Summarize it."),
            _turn(4, user="[bot-round] scheduled sweep of the queue"),
            _turn(5, user="deploy the staging build with STAGE=PROD set, a person typed this"),
        ])
        self.assertEqual(_curate.unprocessed(self.WS, today=TODAY), {"2026-11-06-abcd1234": [5]})
        out = self.cli("prep", "--ws", self.WS, "--now", NOW, "--dry")
        self.assertIn("turn 5", out)
        for n in (1, 2, 3, 4):
            self.assertNotIn(f"2026-11-06-abcd1234 turn {n} ", out)

    def test_correction_item_carries_the_previous_claude_line(self):
        self.write_log("2026-11-06-abcd1234", [
            (1, "10:00", "set the port for the api gateway", "I set the port to 9090 in config.yaml",
             "Edit(config.yaml)"),
            (2, "10:05", "no, that's wrong — it must be 8080, check the docs", "Fixed: port 8080", "Edit(config.yaml)"),
        ])
        out = self.cli("prep", "--ws", self.WS, "--now", NOW, "--dry")
        sec3 = out.split("## §3 ", 1)[1].split("\n## §", 1)[0]
        self.assertIn("- 2026-11-06-abcd1234 turn 2 10:05", sec3)
        self.assertIn("Before (Claude, turn 1): I set the port to 9090 in config.yaml", sec3)
        self.assertIn("User: no, that's wrong — it must be 8080", sec3)

    def test_project_commits_from_the_cwd_header(self):
        repo = self.root / "proj repo"
        repo.mkdir()
        _git(repo, "init", "-q")
        (repo / "a.txt").write_text("a")
        _git(repo, "add", "a.txt")
        _git(repo, "commit", "-q", "-m", "feat: the commit the curator should cite")
        sha = _git(repo, "rev-parse", "HEAD").strip()
        self.write_log("2026-11-06-abcd1234", [_turn(1)], cwd=str(repo))
        out = self.cli("prep", "--ws", self.WS, "--dry", "--now", datetime.now().strftime("%Y-%m-%dT%H:%M"))
        sec = out.split("## §4b ", 1)[1].split("\n## §", 1)[0]
        self.assertIn(f"- proj repo@{sha[:12]} feat: the commit the curator should cite", sec)

    def test_entries_topics_and_cards_of_the_window(self):
        topic = self.wsd / "routing"
        topic.mkdir()
        (topic / "00-README.md").write_text("---\nslug: routing\ntitle: Routing\n---\n# Routing\n\n## TL;DR\n\n"
                                            "> Cốt lõi 1 dòng (TODO).\n")
        (topic / "2026-11-06-decision.md").write_text("## [decision] Route by folder — rationale: identity\n")
        (topic / "lessons.md").write_text("# Lessons\n\n## Entries\n\n## [2026-11-06] Slug from frontmatter\n")
        out = self.cli("prep", "--ws", self.WS, "--now", NOW, "--dry")
        sec4c = out.split("## §4c ", 1)[1].split("\n## §", 1)[0]
        self.assertIn("workspaces/w/routing/2026-11-06-decision.md: ## [decision] Route by folder", sec4c)
        self.assertIn("workspaces/w/routing/lessons.md: ## [2026-11-06] Slug from frontmatter", sec4c)
        sec7 = out.split("## §7 ", 1)[1].split("\n## §", 1)[0]
        self.assertIn("- routing (workspaces/w/routing/00-README.md) card=todo-summary", sec7)

    def test_answered_elsewhere_candidates(self):
        import _gaps  # type: ignore
        (self.wsd / "docs" / "handoff.md").write_text("## Entries\n- host:mac 2026-11-01 [done] ship the pelican router\n")
        qid, st = _gaps.add(self.WS, "ask", "Handoff item 'host:mac 2026-11-01 [next] ship the pelican router' "
                                            "looks done — tick it?", why="commit", source="curator", settings={})
        self.assertEqual(st, "added")
        topic = self.wsd / "gw"
        topic.mkdir()
        (topic / "00-README.md").write_text("---\nslug: gw\ntitle: GW\n---\n# GW\n")
        (topic / "2026-11-06-port.md").write_text("- [ref] the zebra gateway listens on port 8080 — Source: gw.yaml\n")
        q2, _ = _gaps.add(self.WS, "research", "which port does the zebra gateway listen on", settings={})
        out = self.cli("prep", "--ws", self.WS, "--now", NOW, "--dry")
        sec6 = out.split("## §6 ", 1)[1].split("\n## §", 1)[0]
        self.assertIn(f"- candidate {qid}: the handoff item is no longer live", sec6)
        self.assertIn(f"- candidate {q2}: its key terms appear in workspaces/w/gw/2026-11-06-port.md", sec6)
        sec10 = out.split("## §10 ", 1)[1].split("\n## §", 1)[0]
        self.assertIn(q2, sec10)

    def test_dry_prep_writes_nothing(self):
        self.write_log("2026-11-06-abcd1234", [_turn(n) for n in range(1, 8)])
        before = self.files()
        out = self.cli("prep", "--ws", self.WS, "--now", NOW, "--dry")
        self.assertIn("## §2 Unprocessed turns", out)
        self.assertEqual(self.files(), before)
        self.assertFalse((self.vault / ".curator").exists())

    def test_prep_writes_files_ignore_line_and_a_short_index(self):
        self.write_log("2026-11-06-abcd1234", [_turn(n) for n in range(1, 8)])
        out = self.cli("prep", "--ws", self.WS, "--now", NOW)
        lines = out.strip().splitlines()
        prep_md = Path(lines[0])
        self.assertTrue(prep_md.is_file())
        self.assertEqual(prep_md.parent, self.vault / ".curator" / self.WS)
        self.assertTrue(prep_md.name.startswith(f"prep-{TODAY}-"))
        self.assertTrue(prep_md.with_suffix(".json").is_file())
        self.assertLessEqual(len(out.strip()), 1500)
        self.assertIn("§2 7", out)
        self.assertIn(".curator/", (self.vault / ".gitignore").read_text().splitlines())
        # the index line ranges point at the section headings
        md_lines = prep_md.read_text().splitlines()
        meta = json.loads(prep_md.with_suffix(".json").read_text())
        for sid, info in meta["sections"].items():
            self.assertTrue(md_lines[info["line_start"] - 1].startswith(f"## §{sid} "), sid)

    def test_prep_three_passes_byte_identical(self):
        self.write_log("2026-11-06-abcd1234", [_turn(n) for n in range(1, 8)])
        outs, blobs = [], []
        for _ in range(3):
            out = self.cli("prep", "--ws", self.WS, "--now", NOW)
            outs.append(out)
            p = Path(out.splitlines()[0])
            blobs.append((p.read_bytes(), p.with_suffix(".json").read_bytes()))
        self.assertEqual(outs[0], outs[1])
        self.assertEqual(outs[1], outs[2])
        self.assertEqual(blobs[0], blobs[1])
        self.assertEqual(blobs[1], blobs[2])
        self.assertIn(b"## \xc2\xa72 Unprocessed turns", blobs[0][0])

    def test_already_curated_from_the_local_tree(self):
        self.write_log("2026-11-06-abcd1234", [_turn(n) for n in range(1, 8)])
        self.write_worklog(TODAY, "laptop", "aaaaaa", [])
        self.assertEqual(self.cli("prep", "--ws", self.WS, "--now", NOW).strip(), "already-curated:laptop")
        self.assertEqual(list(self.vault.glob(f".curator/{self.WS}/prep-*")), [])
        # a worklog of THIS host does not stop a second run
        (self.wsd / "docs" / "worklog" / f"{TODAY}-laptop-aaaaaa.md").unlink()
        self.write_worklog(TODAY, "mac", "bbbbbb", [])
        self.assertTrue(self.cli("prep", "--ws", self.WS, "--now", NOW).startswith(str(self.vault)))

    def test_already_curated_from_origin_after_a_fetch(self):
        today = date.today().isoformat()
        bare = self.root / "remote.git"
        _git(self.root, "init", "-q", "--bare", "-b", "main", str(bare))
        peer = self.root / "peer"
        _git(self.root, "clone", "-q", str(bare), str(peer))
        # the vault clones the EMPTY remote: the peer's worklog arrives only via the fetch
        shutil.rmtree(self.vault)
        _git(self.root, "clone", "-q", str(bare), str(self.vault))
        (self.wsd / "journal" / "sessions").mkdir(parents=True)
        (self.wsd / "workspace.json").write_text(json.dumps({"name": self.WS}))
        (self.vault / "config.json").write_text(json.dumps({"host_id": "mac", "remote": str(bare),
                                                            "branch": "main"}))
        self.settings({})
        wl = peer / "workspaces" / self.WS / "docs" / "worklog"
        wl.mkdir(parents=True)
        (wl / f"{today}-peerhost-cccccc.md").write_text("---\ntype: worklog\n---\n")
        _git(peer, "add", "-A")
        _git(peer, "commit", "-q", "-m", "peer worklog")
        _git(peer, "push", "-q", "origin", "HEAD:main")
        self.write_log(f"{today}-abcd1234", [_turn(n) for n in range(1, 8)])
        self.assertFalse((self.wsd / "docs" / "worklog").exists())
        self.assertEqual(self.cli("prep", "--ws", self.WS).strip(), "already-curated:peerhost")

    def test_bad_workspace_is_refused(self):
        self.assertEqual(self.cli("prep", "--ws", "../x", "--dry").strip(), "rejected:bad_ws")
        self.assertEqual(self.cli("prep", "--ws", "nosuch", "--dry").strip(), "rejected:no_workspace")
        self.assertEqual(self.cli("prep", "--ws", self.WS, "--now", "yesterday").strip(), "rejected:bad_now")


# ── the digest's unused budget (round 2) ─────────────────────────────────────

def _section(md: str, sid: str) -> str:
    return md.split(f"## §{sid} ", 1)[1].split("\n## §", 1)[0]


def _items(md: str, sid: str) -> list:
    return [ln for ln in _section(md, sid).split("\n") if ln.startswith("- ")]


class DigestBudgetGrowthTest(_Vault):
    """Live devops/trade digests used 29k of 40k while §4c showed 7 of 208
    entries: a section's unused budget now goes to the cut sections — tier 1
    (§6 §4c §7 §5 §4b §10) round-robin, then tier 2 (§4 §9 §8) — and never to
    §2 (its printed turns are `processed`) or §3."""

    NOWDT = datetime.fromisoformat(NOW)

    def build(self, grow=True):
        from unittest import mock
        if grow:
            return _curate.build_digest(self.WS, _curate.read_settings(), self.NOWDT)
        with mock.patch.object(_curate, "_grow_sections", return_value=0):
            return _curate.build_digest(self.WS, _curate.read_settings(), self.NOWDT)

    def topic(self, slug="t", entries=80, hyps=0) -> None:
        topic = self.wsd / slug
        topic.mkdir()
        (topic / "00-README.md").write_text(f"---\nslug: {slug}\ntitle: T\n---\n# T\n\n## TL;DR\n\n> T topic\n")
        (topic / "2026-11-06-notes.md").write_text("\n".join(
            f"- [ref] entry number {i} about the curator digest budget — Source: x.md" for i in range(entries)))
        if hyps:
            # longer than a §4c line: once §4c's next item no longer fits, a §9 one cannot either
            (topic / "2025-01-01-old.md").write_text("\n".join(
                f"- [hypothesis] old guess {i} about routing tables, the gateway retry policy and the fallback "
                f"order of the regional upstream pools — Verify: run it" for i in range(hyps)))

    def test_unused_budget_grows_cut_sections_within_the_total(self):
        self.settings({"curator": {"max_prep_chars": 8000}})
        self.write_log("2026-11-06-abcd1234", [_turn(1), _turn(2)])        # a short §2, an empty §3
        self.topic(entries=80)
        base_md, base_meta = self.build(grow=False)
        md, meta = self.build()
        base_n, n = len(_items(base_md, "4c")), len(_items(md, "4c"))
        self.assertGreaterEqual(base_n, 1)
        self.assertGreater(n, base_n + 10, "the unused budget went to §4c")
        self.assertIn(f"\n+{80 - n} more — the aspect files of the §7 topics hold them", _section(md, "4c"))
        self.assertLessEqual(len(md), 8000, "growth never exceeds curator.max_prep_chars")
        self.assertGreater(len(md), 7000, "and uses most of it")
        self.assertEqual(meta["sections"]["4c"]["items"], n)
        self.assertEqual(meta["sections"]["4c"]["more"], 80 - n)
        # the index line ranges still point at the section headings
        lines = md.split("\n")
        for sid, info in meta["sections"].items():
            self.assertTrue(lines[info["line_start"] - 1].startswith(f"## §{sid} "), sid)
        # §2 is untouched: same printed turns, same processed set, same run
        self.assertEqual(_section(md, "2"), _section(base_md, "2"))
        self.assertEqual((meta["processed"], meta["run"]), (base_meta["processed"], base_meta["run"]))

    def test_section_2_never_grows(self):
        self.settings({"curator": {"max_prep_chars": 8000}})
        self.write_log("2026-11-06-abcd1234", [_turn(n) for n in range(1, 60)])
        base_md, base_meta = self.build(grow=False)
        md, meta = self.build()
        self.assertEqual(_section(md, "2"), _section(base_md, "2"))
        self.assertIn("\n+", _section(md, "2"), "§2 is still cut")
        self.assertEqual(meta["processed"], base_meta["processed"])
        self.assertEqual(meta["unprocessed_left"], base_meta["unprocessed_left"])

    def test_tier_one_is_served_before_tier_two(self):
        self.settings({"curator": {"max_prep_chars": 8000}})
        self.write_log("2026-11-06-abcd1234", [_turn(1)])
        self.topic(entries=200, hyps=80)                    # §4c (tier 1) cannot be served in full
        base_md, _ = self.build(grow=False)
        md, _ = self.build()
        self.assertGreater(len(_items(md, "4c")), len(_items(base_md, "4c")))
        self.assertEqual(len(_items(md, "9")), len(_items(base_md, "9")), "§9 (tier 2) waits for tier 1")
        self.assertIn("\n+", _section(md, "9"))
        # once tier 1 is served in full, tier 2 grows
        shutil.rmtree(self.wsd / "t")
        self.topic(entries=3, hyps=80)
        base_md, _ = self.build(grow=False)
        md, _ = self.build()
        self.assertNotIn("more —", _section(md, "4c"))
        self.assertGreater(len(_items(md, "9")), len(_items(base_md, "9")))

    def test_research_candidates_are_exactly_the_printed_section_10(self):
        import _gaps  # type: ignore
        self.settings({"curator": {"max_prep_chars": 8000, "max_research": 4}})
        self.write_log("2026-11-06-abcd1234", [_turn(1)])
        ids = []
        for i in range(6):
            qid, st = _gaps.add(self.WS, "research", f"which retention period applies to archive bucket "
                                                     f"{'abcdefghij'[i]} {'y' * 120}", force=True, settings={})
            self.assertEqual(st, "added")
            ids.append(qid)
        base_md, base_meta = self.build(grow=False)
        md, meta = self.build()
        printed = [ln.split()[1] for ln in _items(md, "10")]
        self.assertGreater(len(printed), len(_items(base_md, "10")), "§10 grew past its own budget")
        self.assertEqual(len(printed), 4, "never past curator.max_research")
        self.assertIn("\n+2 more — ", _section(md, "10"))
        self.assertEqual(meta["research_candidates"], printed)
        self.assertEqual(base_meta["research_candidates"], [ln.split()[1] for ln in _items(base_md, "10")])

    def test_three_passes_with_growth_are_byte_identical(self):
        self.settings({"curator": {"max_prep_chars": 8000}})
        self.write_log("2026-11-06-abcd1234", [_turn(1), _turn(2)])
        self.topic(entries=80)
        blobs = []
        for _ in range(3):
            out = self.cli("prep", "--ws", self.WS, "--now", NOW)
            p = Path(out.splitlines()[0])
            blobs.append((out, p.read_bytes(), p.with_suffix(".json").read_bytes()))
        self.assertEqual(blobs[0], blobs[1])
        self.assertEqual(blobs[1], blobs[2])
        self.assertGreater(blobs[0][1].count(b"- workspaces/w/t/2026-11-06-notes.md: "), 10)


class ProjectCommitsAndHintsTest(_Vault):
    def test_beyond_20_commits_per_repo_is_a_more_line(self):
        repo = self.root / "proj"
        repo.mkdir()
        _git(repo, "init", "-q")
        for i in range(25):
            (repo / "f.txt").write_text(str(i))
            _git(repo, "add", "f.txt")
            _git(repo, "commit", "-q", "-m", f"feat: change number {i}")
        self.write_log("2026-11-06-abcd1234", [_turn(1)], cwd=str(repo))
        out = self.cli("prep", "--ws", self.WS, "--dry", "--now", datetime.now().strftime("%Y-%m-%dT%H:%M"))
        sec = _section(out, "4b")
        self.assertEqual(len([ln for ln in sec.split("\n") if ln.startswith("- proj@")]), 20)
        self.assertIn("- proj@", sec)
        self.assertIn("feat: change number 24", sec, "newest first")
        self.assertIn("\n+5 more — beyond 20 per repo or the budget. Full list (a person, not the curator", sec)

    def test_one_repo_probe_per_working_directory(self):
        from unittest import mock
        repo = self.root / "proj"
        repo.mkdir()
        _git(repo, "init", "-q")
        (repo / "a.txt").write_text("a")
        _git(repo, "add", "a.txt")
        _git(repo, "commit", "-q", "-m", "feat: the only commit")
        for i in range(6):
            self.write_log(f"2026-11-06-abcd12{i:02d}", [_turn(1)], cwd=str(repo))
        self.write_log("2026-11-06-ffff0000", [_turn(1)], cwd=str(self.root / "not-a-repo"))
        calls = []
        real = _curate._repo_root

        def spy(cwd):
            calls.append(cwd)
            return real(cwd)

        with mock.patch.object(_curate, "_repo_root", side_effect=spy):
            md, _meta = _curate.build_digest(self.WS, {}, datetime.now().replace(microsecond=0))
        self.assertEqual(sorted(calls), sorted([str(repo), str(self.root / "not-a-repo")]))
        self.assertIn("feat: the only commit", _section(md, "4b"), "the repo was found and read")

    def test_vault_change_hint_never_asks_the_curator_to_run_git(self):
        _git(self.vault, "init", "-q")
        f = self.wsd / "t.md"
        for i in range(45):
            f.write_text(str(i))
            _git(self.vault, "add", "-A")
            _git(self.vault, "commit", "-q", "-m", f"add: entry {i}")
        out = self.cli("prep", "--ws", self.WS, "--dry", "--now", datetime.now().strftime("%Y-%m-%dT%H:%M"))
        sec = _section(out, "4")
        self.assertEqual(len([ln for ln in sec.split("\n") if ln.startswith("- ")]), 40, "<= 40 commits")
        self.assertIn("\n+5 more — older commits; §4c and §7 show what they wrote. Full list (a person, "
                      "not the curator — it never runs git): git -C ", sec)


# ── coverage ─────────────────────────────────────────────────────────────────

class CoverageTest(_Vault):
    def test_union_over_hosts_and_worklogs_within_the_horizon(self):
        self.write_log("2026-11-05-aaaa1111", [_turn(n) for n in range(1, 11)])
        self.write_log("2026-11-06-bbbb2222", [_turn(n) for n in range(1, 6)])
        self.write_worklog("2026-11-05", "mac", "111111", ["2026-11-05-aaaa1111:1-4"])
        self.write_worklog("2026-11-06", "laptop", "222222", ["2026-11-05-aaaa1111:3-6,9", "2026-11-06-bbbb2222:2"])
        # older than raw_ttl_days (7) + hold_days (30): ignored
        self.write_worklog("2026-09-01", "mac", "333333", ["2026-11-06-bbbb2222:1-5"])
        got = _curate.unprocessed(self.WS, today=TODAY)
        self.assertEqual(got, {"2026-11-05-aaaa1111": [7, 8, 10], "2026-11-06-bbbb2222": [1, 3, 4, 5]})
        self.assertEqual(_curate.count_unprocessed(self.WS, today=TODAY), 7)
        self.settings({"curator": {"hold_days": 365}})
        self.assertEqual(_curate.unprocessed(self.WS, today=TODAY),
                         {"2026-11-05-aaaa1111": [7, 8, 10]})

    def test_malformed_processed_entries_are_ignored(self):
        self.write_log("2026-11-06-bbbb2222", [_turn(n) for n in range(1, 4)])
        p = self.write_worklog("2026-11-06", "mac", "444444", ["2026-11-06-bbbb2222:1"])
        p.write_text(p.read_text().replace('["2026-11-06-bbbb2222:1"]',
                                           '["2026-11-06-bbbb2222:1-999999999999", 7, "x:2", "2026-11-06-bbbb2222:2"]'))
        self.assertEqual(_curate.unprocessed(self.WS, today=TODAY), {"2026-11-06-bbbb2222": [1, 3]})


# ── commit ───────────────────────────────────────────────────────────────────

BODY = ("## Done\n### 2026-11-06\n- [done] shipped x — evidence: session abcd1234 turn 2\n"
        "## Curator actions\n- FACT workspaces/w/t/a.md — one\n- QUESTION Q-1a2b3c4d (ask) — q\n"
        "- ANSWERED Q-5e6f7a8b — a\n")


class CommitTest(_Vault):
    def prep(self) -> dict:
        self.write_log("2026-11-06-abcd1234", [_turn(n) for n in range(1, 8)])
        out = self.cli("prep", "--ws", self.WS, "--now", NOW)
        return json.loads(Path(out.splitlines()[0]).with_suffix(".json").read_text())

    def commit(self, run, phase, body=BODY, ws=None):
        return self.cli("commit", "--ws", ws or self.WS, "--run", run, "--phase", str(phase),
                        "--worklog", "-", inp=body).strip()

    def test_phase_1_then_2_and_the_frontmatter_comes_from_the_prep(self):
        meta = self.prep()
        forged = ("---\ntype: worklog\nprocessed: [\"2026-11-06-abcd1234:1-999\"]\nhost: evil\n---\n"
                  "# My own title\n\n> my own quote\n\n" + BODY)
        out = self.commit(meta["run"], 1, forged)
        path = self.vault / out.split("\t")[0]
        self.assertTrue(out.endswith("\twritten"), out)
        self.assertEqual(path.name, f"{TODAY}-mac-{meta['run'][:6]}.md")
        got = _curate.read_worklog_meta(path)
        self.assertEqual(got["processed"], meta["processed"])
        self.assertEqual((got["host"], got["run"], got["phase"], got["ws"]), ("mac", meta["run"], 1, self.WS))
        text = path.read_text()
        self.assertEqual(text.count("\n---\n"), 1, "exactly one frontmatter block")
        self.assertNotIn("my own quote", text)
        self.assertIn("> Written by the gowth-mem curator", text)
        self.assertIn("stats: turns=7 corrections=0 facts=1 corrections_written=0 cards=0 questions_added=1 "
                      "questions_closed=1 proposals=0 researched=0", text)
        out2 = self.commit(meta["run"], 2)
        self.assertTrue(out2.endswith("\twritten"), out2)
        self.assertEqual(_curate.read_worklog_meta(path)["phase"], 2)

    def test_three_identical_commits_write_once(self):
        meta = self.prep()
        outs = [self.commit(meta["run"], 1)] + [self.commit(meta["run"], 2) for _ in range(3)]
        self.assertEqual([o.split("\t")[1] for o in outs], ["written", "written", "unchanged", "unchanged"])
        path = self.vault / outs[0].split("\t")[0]
        b1 = path.read_bytes()
        self.commit(meta["run"], 2)
        self.assertEqual(path.read_bytes(), b1)

    def test_phase_2_needs_phase_1(self):
        meta = self.prep()
        self.assertTrue(self.commit(meta["run"], 2).endswith("\trejected:no_phase1"))

    def test_rejections(self):
        meta = self.prep()
        run = meta["run"]
        self.assertEqual(self.commit("0123456789ab", 1), "-\trejected:no_prep")
        self.assertEqual(self.commit("nothex", 1), "-\trejected:no_prep")
        self.assertTrue(self.commit(run, 1, "## Done\n" + "x" * 16_100 + "\n## Curator actions\n")
                        .endswith("\trejected:too_long"))
        self.assertTrue(self.commit(run, 1, "## Done\n- nothing\n").endswith("\trejected:missing_actions"))
        self.assertTrue(self.commit(run, 1, BODY + "- FACT x — token = ghp_" + "a" * 36 + "\n")
                        .endswith("\trejected:secret_leak"))
        self.assertTrue(self.commit(run, 1, BODY + "- SKIPPED [gowth-mem:curator ws=w] quoted\n")
                        .endswith("\trejected:control_token"))
        self.assertEqual(self.cli("commit", "--ws", self.WS, "--run", run, "--phase", "3", "--worklog", "-",
                                  inp=BODY).strip(), "-\trejected:bad_phase")
        # a prep JSON for another workspace / run is a mismatch
        pj = next((self.vault / ".curator" / self.WS).glob(f"prep-*-{run}.json"))
        data = json.loads(pj.read_text())
        data["ws"] = "other"
        pj.write_text(json.dumps(data))
        self.assertEqual(self.commit(run, 1), "-\trejected:run_mismatch")
        data["ws"] = self.WS
        pj.write_text(json.dumps(data))
        # a worklog of another run at the same path is a mismatch
        target = self.vault / "workspaces" / self.WS / "docs" / "worklog" / f"{TODAY}-mac-{run[:6]}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("---\ntype: worklog\nrun: ffffffffffff\n---\n")
        self.assertTrue(self.commit(run, 1).endswith("\trejected:run_mismatch"))
        self.assertTrue(self.commit(run, 2).endswith("\trejected:run_mismatch"))

    def test_a_symlink_out_of_the_workspace_is_refused(self):
        meta = self.prep()
        outside = self.root / "outside"
        outside.mkdir()
        wl = self.wsd / "docs" / "worklog"
        wl.symlink_to(outside, target_is_directory=True)
        out = self.commit(meta["run"], 1)
        self.assertTrue(out.endswith("\trejected:bad_path"), out)
        self.assertEqual(list(outside.iterdir()), [], "nothing written through the symlink")
        # a symlinked docs/ as well
        wl.unlink()
        docs = self.wsd / "docs"
        shutil.move(str(docs), str(self.root / "realdocs"))
        docs.symlink_to(self.root / "realdocs", target_is_directory=True)
        out = self.commit(meta["run"], 1)
        self.assertTrue(out.endswith("\trejected:bad_path"), out)
        self.assertFalse((self.root / "realdocs" / "worklog").exists())
        # positive control: the workspace's own directory again → written
        docs.unlink()
        shutil.move(str(self.root / "realdocs"), str(docs))
        out = self.commit(meta["run"], 1)
        self.assertTrue(out.endswith("\twritten"), out)
        self.assertTrue((self.vault / out.split("\t")[0]).is_file())

    def test_lock_timeout(self):
        meta = self.prep()
        from _lock import file_lock  # type: ignore
        from unittest import mock
        with file_lock(f"curator-{self.WS}", timeout=2):
            with mock.patch.object(_curate, "LOCK_TIMEOUT", 0.2):
                path, status = _curate.commit(self.WS, meta["run"], 1, BODY)
        self.assertEqual(status, "rejected:lock_timeout")
        self.assertFalse(path.exists())


class StatusTest(_Vault):
    def test_status_line_and_json(self):
        import _gaps  # type: ignore
        yday = (date.today() - timedelta(days=1)).isoformat()
        dby = (date.today() - timedelta(days=2)).isoformat()
        self.write_log(f"{yday}-abcd1234", [_turn(n) for n in range(1, 4)])
        self.write_worklog(dby, "mac", "abcdef", [f"{yday}-abcd1234:1"])
        _gaps.add(self.WS, "ask", "which region hosts the primary database", settings={})
        _gaps.add(self.WS, "research", "what is the retention of the audit bucket", settings={})
        topic = self.wsd / "t"
        topic.mkdir()
        (topic / "00-README.md").write_text("---\nslug: t\ntitle: T\n---\n# T\n\n## TL;DR\n\n> Cốt lõi 1 dòng (TODO).\n")
        line = self.cli("status", "--ws", self.WS).strip()
        self.assertIn(f"curator ws=w: last {dby} (mac, run abcdef", line)
        self.assertIn("open questions 2 (ask 1, research 1)", line)
        self.assertIn("cards todo 1 / over 0 / stale 0", line)
        self.assertIn("unprocessed 2 turn(s) in 1 log(s)", line)
        rows = json.loads(self.cli("status", "--json"))
        self.assertEqual([r["ws"] for r in rows], [self.WS])
        self.assertEqual(rows[0]["misses"], 0)
        self.assertEqual(self.cli("status", "--ws", "../x").strip(), "rejected:bad_ws")


# ── due (pure; fixed clock) ──────────────────────────────────────────────────

class DueTest(_Vault):
    def due(self, now="2026-11-07T10:40", state=None, capture_on=True, settings=None):
        return _curate.due(self.WS, settings if settings is not None else {}, state or {},
                           capture_on=capture_on, now=datetime.fromisoformat(now))

    def claim(self, day, n, age_minutes=0):
        d = self.vault / ".curator" / self.WS
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"claim-{day}-{n}"
        p.write_text("x")
        t = time.time() - age_minutes * 60
        os.utime(p, (t, t))
        return p

    def test_floor_then_due(self):
        self.write_log("2026-11-06-abcd1234", [_turn(n) for n in range(1, 5)])
        self.assertEqual(self.due()[0], False)
        self.assertEqual(self.due()[1]["why"], "floor:4")
        self.write_log("2026-11-06-abcd1234", [_turn(n) for n in range(1, 6)])
        self.assertEqual(self.due()[0], 1)

    def test_hour_gate(self):
        self.write_log("2026-11-06-abcd1234", [_turn(n) for n in range(1, 9)])
        s = {"curator": {"hour": 11}}
        self.assertEqual(self.due("2026-11-07T10:59", settings=s), (False, {"misses": None, "why": "hour"}))
        self.assertEqual(self.due("2026-11-07T11:00", settings=s)[0], 1)

    def test_curated_today_and_one_host_per_workspace(self):
        self.write_log("2026-11-06-abcd1234", [_turn(n) for n in range(1, 9)])
        self.write_worklog("2026-11-07", "mac", "aaaaaa", [])
        self.assertEqual(self.due()[1]["why"], "curated-today")
        (self.wsd / "docs" / "worklog" / "2026-11-07-mac-aaaaaa.md").unlink()
        self.write_worklog("2026-11-06", "laptop", "bbbbbb", [])
        self.assertEqual(self.due()[1]["why"], "curated-by:laptop")
        self.assertEqual(self.due("2026-11-08T10:00")[0], 1, "failover after 2 days")
        # newest by (date, then host): the same date from "mac" sorts after "laptop"
        self.write_worklog("2026-11-06", "mac", "cccccc", [])
        self.assertEqual(self.due()[0], 1)

    def test_newest_worklog_from_this_host_keeps_it_curating(self):
        self.write_log("2026-11-06-abcd1234", [_turn(n) for n in range(1, 9)])
        self.write_worklog("2026-11-05", "laptop", "bbbbbb", [])
        self.write_worklog("2026-11-06", "mac", "cccccc", [])
        self.assertEqual(self.due()[0], 1)

    def test_curator_hosts(self):
        self.write_log("2026-11-06-abcd1234", [_turn(n) for n in range(1, 9)])
        self.assertEqual(self.due(settings={"curator": {"hosts": ["laptop"]}})[1]["why"], "not-in-hosts")
        self.assertEqual(self.due(settings={"curator": {"hosts": ["Mac.local"]}})[0], 1)

    def test_misses_pause_and_a_worklog_resets_them(self):
        self.write_log("2026-11-06-abcd1234", [_turn(n) for n in range(1, 9)])
        self.claim("2026-11-04", 1)
        self.assertEqual(self.due()[1]["misses"], 1)
        self.assertEqual(self.due()[0], 1)
        self.claim("2026-11-05", 1)
        self.claim("2026-11-05", 2)
        res, info = self.due()
        self.assertEqual((res, info["misses"]), ("paused", 2))
        self.write_worklog("2026-11-05", "mac", "dddddd", [])
        self.assertEqual(self.due()[1]["misses"], 0)
        self.assertEqual(self.due()[0], 1)

    def test_attempts(self):
        self.write_log("2026-11-06-abcd1234", [_turn(n) for n in range(1, 9)])
        now = datetime.now().strftime("%Y-%m-%dT%H:%M")
        today = now[:10]
        self.claim(today, 1, age_minutes=30)
        self.assertEqual(self.due(now)[1]["why"], "claimed")
        self.claim(today, 1, age_minutes=121)
        self.assertEqual(self.due(now)[0], 2)
        self.claim(today, 2, age_minutes=200)
        self.assertEqual(self.due(now)[1]["why"], "claimed", "no attempt 3")

    def test_capture_off_uses_the_state_counter(self):
        self.assertEqual(self.due(capture_on=False, state={"curator_turns": {self.WS: 4}})[0], False)
        self.assertEqual(self.due(capture_on=False, state={"curator_turns": {self.WS: 5}})[0], 1)
        self.assertEqual(self.due(capture_on=False, state={"curator_turns": {self.WS: "x"}})[0], False)

    def test_disabled_string_false(self):
        self.write_log("2026-11-06-abcd1234", [_turn(n) for n in range(1, 9)])
        self.assertEqual(self.due(settings={"curator": {"enabled": "false"}})[1]["why"], "disabled")

    def test_claim_is_exclusive_and_ensures_the_ignore_line(self):
        now = datetime.fromisoformat("2026-11-07T10:40")
        self.assertTrue(_curate.claim(self.WS, 1, now))
        self.assertFalse(_curate.claim(self.WS, 1, now))
        self.assertTrue((self.vault / ".curator" / self.WS / "claim-2026-11-07-1").is_file())
        self.assertIn(".curator/", (self.vault / ".gitignore").read_text().splitlines())
        self.assertFalse(_curate.claim("../x", 1, now))

    def test_claim_cli_refuses_a_recent_claim(self):
        out = self.cli("claim", "--ws", self.WS).strip()
        self.assertTrue(out.endswith("-1\tclaimed"), out)
        self.assertTrue(self.cli("claim", "--ws", self.WS).strip().startswith("-\trefused:recent_claim:"))
        today = date.today().isoformat()
        self.write_worklog(today, "mac", "eeeeee", [])
        self.assertTrue(self.cli("claim", "--ws", self.WS).strip().endswith("-2\tclaimed"))


# ── growth probes (the 4-test rule: linear in input size) ───────────────────

def _timed(fn, arg, reps=3) -> float:
    best = None
    for _ in range(reps):
        t0 = time.perf_counter()
        fn(arg)
        dt = time.perf_counter() - t0
        best = dt if best is None else min(best, dt)
    return best


class GrowthTest(unittest.TestCase):
    SIZES = (10_000, 20_000, 40_000)

    def assert_linear(self, fn, make, label):
        times = [_timed(fn, make(n)) for n in self.SIZES]
        # ~2x per doubling: 4x for 10k → 40k is linear, 16x quadratic. A floor
        # keeps sub-millisecond noise from failing the ratio.
        if times[-1] > 0.02:
            self.assertLess(times[-1] / max(times[0], 1e-4), 8.0, f"{label}: {times}")
        self.assertLess(times[-1], 2.0, f"{label}: {times}")

    def test_correction_shapes(self):
        shapes = {
            "unclosed": lambda n: "revert " + "(" * n,
            "whitespace": lambda n: "no" + " " * n + "x",
            "separators": lambda n: "revert " + "- " * (n // 2),
            "prefix-flood": lambda n: "undo " * (n // 5),
            "vi-flood": lambda n: "sai " * (n // 4),
        }
        for name, make in shapes.items():
            self.assert_linear(_curate.is_correction, make, name)

    def test_turn_heading_and_parse_log(self):
        shapes = {
            "digits": lambda n: "## turn " + "1" * n,
            "spaces": lambda n: "## turn 1" + " " * n,
            "repeated": lambda n: "## turn " * (n // 8),
            "dash-flood": lambda n: "## turn 3 — " + "—" * n,
        }
        for name, make in shapes.items():
            self.assert_linear(_curate.read_turn_heading, make, name)
            self.assert_linear(_curate.parse_log, lambda n, m=make: "# t\n" + m(n) + "\n" + m(n), name + "-log")

    def test_cwd_header(self):
        shapes = {
            "long-path": lambda n: "# t\n<!-- cwd: /" + "a" * n + " -->\n",
            "close-flood": lambda n: "# t\n<!-- cwd: /" + " -->" * (n // 4) + "\n",
            "open-flood": lambda n: "# t\n" + "<!-- cwd: " * (n // 10) + "\n",
            "no-newline": lambda n: "# t" + " " * n,
        }
        for name, make in shapes.items():
            self.assert_linear(_curate.read_cwd_header, make, name)

    def test_handoff_items(self):
        shapes = {
            "unclosed-bracket": lambda n: "## h 2026-11-01\n" + ("- [" * (n // 3) + "\n") * 4,
            "bold-flood": lambda n: "## h 2026-11-01\n" + ("**" * (n // 2) + "\n") * 4,
            "date-flood": lambda n: "## " + "2026-11-0" * (n // 9) + "\n- [ ] x\n",
            "box-flood": lambda n: "## h 2026-11-01\n" + "- [ ] x\n" * (n // 8),
            "whitespace": lambda n: "## h\n- host:" + " " * n + "[next] x\n",
        }
        for name, make in shapes.items():
            self.assert_linear(_curate.handoff_live_items, make, name)

    def test_worklog_frontmatter(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        p = root / "w.md"

        def reader(text):
            p.write_text(text)
            return _curate.read_worklog_meta(p)

        shapes = {
            "unclosed-list": lambda n: '---\ntype: worklog\nprocessed: ["' + "a" * n + "\n---\n",
            "commas": lambda n: "---\ntype: worklog\nprocessed: [" + "," * n + "]\n---\n",
            "ranges": lambda n: '---\ntype: worklog\nprocessed: ["2026-11-06-abcd1234:' + "1-1," * (n // 4) + '1"]\n---\n',
            "brackets": lambda n: "---\ntype: worklog\nprocessed: " + "[" * min(n, 900) + "]" * min(n, 900) + "\n---\n",
            "no-close": lambda n: "---\ntype: worklog\n" + "k: v\n" * (n // 5),
        }
        for name, make in shapes.items():
            self.assert_linear(reader, make, name)
        # the parsed ranges of a long valid list stay bounded and linear
        self.assert_linear(_curate.parse_processed,
                           lambda n: ["2026-11-06-abcd1234:" + ",".join(str(i) for i in range(1, n // 6))],
                           "parse_processed")


class EndToEndHostileInputTest(_Vault):
    """Rule 1 of the 4-test rule: the real entry point with hostile input, and
    positive signals that the input was parsed."""

    def test_prep_on_hostile_logs_and_handoff(self):
        flood = "## turn " + "1" * 40_000
        self.write_log("2026-11-06-abcd1234", [
            _turn(1, user="revert " + "(" * 40_000),
            _turn(2, user="no" + " " * 40_000 + "that's wrong"),
        ], cwd="/" + " -->" * 100, extra=flood + "\n## turn 3 — 10:03\n**User:** real turn after the flood\n"
                                          "**Claude:** ok\n**Actions:** Read(a.py)\n")
        (self.wsd / "docs" / "handoff.md").write_text(
            "## host:mac 2026-01-02 run\n" + "- [" * 20_000 + "\n" + "**" * 20_000 + "\n- [ ] the one live box\n")
        t0 = time.time()
        out = self.cli("prep", "--ws", self.WS, "--now", NOW, "--dry", timeout=60)
        self.assertLess(time.time() - t0, 30)
        self.assertIn("- 2026-11-06-abcd1234 turn 3 10:03", out)            # parsed past the flood
        self.assertIn("User: real turn after the flood", out)
        self.assertIn("[todo]", out.split("## §5 ", 1)[1])                 # the live box was found
        self.assertIn("the one live box", out)
        sec3 = out.split("## §3 ", 1)[1].split("\n## §", 1)[0]
        self.assertIn("turn 2", sec3)                                       # "no …" is a correction shape
        # 3 non-dry passes over the same hostile input: byte-identical files
        blobs = []
        for _ in range(3):
            o = self.cli("prep", "--ws", self.WS, "--now", NOW, timeout=60)
            p = Path(o.splitlines()[0])
            blobs.append((p.read_bytes(), p.with_suffix(".json").read_bytes()))
        self.assertEqual(blobs[0], blobs[1])
        self.assertEqual(blobs[1], blobs[2])

    def test_hostile_worklog_frontmatter_through_status_and_prep(self):
        self.write_log("2026-11-06-abcd1234", [_turn(n) for n in range(1, 5)])
        d = self.wsd / "docs" / "worklog"
        d.mkdir(parents=True)
        today = date.today().isoformat()
        (d / f"{today}-mac-aaaaaa.md").write_text(
            "---\ntype: worklog\nprocessed: [\"" + "a" * 40_000 + "\n---\n")                 # unclosed list
        (d / f"{today}-mac-bbbbbb.md").write_text(
            "---\ntype: worklog\nprocessed: [" + "," * 40_000 + "]\n---\n")                   # separator flood
        (d / f"{today}-mac-cccccc.md").write_text(                                          # over-long list
            '---\ntype: worklog\nprocessed: ["2026-11-06-abcd1234:' + "4-4," * 10_000 + '4"]\n---\n')
        (d / f"{today}-mac-dddddd.md").write_text("---\ntype: worklog\n" + "k: v\n" * 8_000)  # never closed
        (d / f"{today}-mac-eeeeee.md").write_text(                                          # the valid one
            '---\ntype: worklog\nprocessed: ["2026-11-06-abcd1234:1-2"]\n---\n')
        t0 = time.time()
        rows = json.loads(self.cli("status", "--ws", self.WS, "--json", timeout=60))
        self.assertLess(time.time() - t0, 30)
        # positive signal: the valid worklog was parsed beside the hostile ones (turns 1-2
        # covered); the over-long range list counts for nothing (turn 4 stays unprocessed)
        self.assertEqual(rows[0]["unprocessed_turns"], 2)
        self.assertEqual(_curate.unprocessed(self.WS), {"2026-11-06-abcd1234": [3, 4]})
        out = self.cli("prep", "--ws", self.WS, "--dry", timeout=60)
        self.assertIn("- 2026-11-06-abcd1234 turn 3 ", out)
        self.assertNotIn("- 2026-11-06-abcd1234 turn 1 ", out)


if __name__ == "__main__":
    unittest.main()
