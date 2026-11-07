"""v4.9 §1.2–§1.4 — pipeline hygiene beyond turn counting.

  * §1.2 consumers: per-prompt recall and the SYNC-CONFLICT nag stay silent on
    a bot/pipeline-marked prompt (recall_daily untouched); PreCompact neither
    counts nor dumps one.
  * §1.3 the state.json session map: real-turn Stops and recall telemetry
    stamp `seen`; the Stop hook's locked save stamps entries that lack it
    (never dropping them for that) and, past 300 entries, drops sessions unseen
    for 30 days, then caps to the 300 most recent — never the current one.
    The live map had grown to 1,765 sessions (~300 KB rewritten every Stop).
  * §1.4 a new session log starts with `<!-- cwd: <Stop input cwd> -->`.

Everything runs against a per-test scratch GOWTH_MEM_HOME through the real
entry points (the hook mains / the bash wrappers as subprocesses).
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "hooks" / "scripts"
sys.path.insert(0, str(SCRIPTS))

SCRUB = ("AI_AGENT", "GOWTH_WORKSPACE", "CLAUDE_SUBAGENT", "GOWTH_MEM_OFF", "CLAUDE_CODE_ENTRYPOINT")
SDK_VERIFY = ("STAGE=VERIFY\nVerify the mention below BEFORE the operator decides anything. READ-ONLY.\n\n"
              "1. Who is asking, which service does it belong to?")
PASTED_TASK = ("### TASK 469044d829 R13 ###\nSTAGE=EXECUTE\nOperator APPROVED. Execute the PLAN from the "
               "VERIFY stage (same session).")
GOOD = "why did we choose pelican-router for the ingress gateway last time, and what was the rationale?"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _env(vault: Path, **extra) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in SCRUB}
    env["GOWTH_MEM_HOME"] = str(vault)
    env["CLAUDE_CONFIG_DIR"] = str(vault / "claude-config")
    env.update(extra)
    return env


class _StopCase(unittest.TestCase):
    """A scratch vault + a transcript in Claude Code 2.1.x record shapes."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="gowth_hyg_"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.home = self.root / "vault"
        ws = self.home / "workspaces" / "default"
        (ws / "journal").mkdir(parents=True)
        (ws / "workspace.json").write_text(json.dumps({"name": "default"}))
        (self.home / "config.json").write_text(json.dumps({"active_workspace": "default"}))
        self.write_settings()
        self.tx = self.root / "transcript.jsonl"
        self.tx.write_text("")
        self.sid = "hyg-" + uuid.uuid4().hex[:8]
        self.prompt_id = ""

    def write_settings(self, **extra) -> None:
        s = {"auto_journal": {"journal_every": 99, "auto_journal_enabled": True},
             "reflection": {"enabled": True, "turn_interval": 99, "min_review_turns": 1},
             "journal": {"auto_forget_enabled": False},
             "sync": {"auto_sync_on_stop": False}}
        s.update(extra)
        (self.home / "settings.json").write_text(json.dumps(s))

    def _append(self, rec: dict) -> None:
        rec.setdefault("uuid", str(uuid.uuid4()))
        with self.tx.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    def human(self, text: str) -> None:
        self.prompt_id = str(uuid.uuid4())
        self._append({"type": "user", "promptId": self.prompt_id, "origin": {"kind": "human"},
                      "message": {"role": "user", "content": text}})
        self._append({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "done"},
            {"type": "tool_use", "name": "Edit", "input": {"file_path": "/repo/x.py"}}]}})

    def stop(self, **extra) -> dict:
        payload = {"session_id": self.sid, "hook_event_name": "Stop", "stop_hook_active": False,
                   "transcript_path": str(self.tx), "prompt_id": self.prompt_id, **extra}
        r = subprocess.run([sys.executable, str(SCRIPTS / "auto-journal.py")], input=json.dumps(payload),
                           capture_output=True, text=True, env=_env(self.home), timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self._append({"type": "system", "subtype": "stop_hook_summary", "hookErrors": [],
                      "hookAdditionalContext": [], "preventedContinuation": False})
        return json.loads(r.stdout) if r.stdout.strip() else {}

    def state(self) -> dict:
        p = self.home / "state.json"
        return json.loads(p.read_text()) if p.is_file() else {}

    def sessions(self) -> dict:
        return self.state().get("session", {})

    def seed_sessions(self, sessions: dict) -> None:
        st = self.state() or {"version": 2, "files": {}}
        st["session"] = sessions
        (self.home / "state.json").write_text(json.dumps(st))

    def log_path(self) -> Path:
        day = datetime.now().strftime("%Y-%m-%d")
        return self.home / "workspaces" / "default" / "journal" / "sessions" / f"{day}-{self.sid[:8]}.md"

    def log(self) -> str:
        p = self.log_path()
        return p.read_text(encoding="utf-8") if p.is_file() else ""


# ─── §1.3 session map ────────────────────────────────────────────────────────

class SessionMapThroughTheHookTest(_StopCase):
    def test_live_entries_without_seen_survive_400_stale_ones(self):
        now = int(time.time())
        stale = {f"stale-{i:04d}": {"turn_count": 3, "total_turns": 9, "seen": now - 40 * 86400}
                 for i in range(400)}
        live = {"live-a": {"turn_count": 4, "total_turns": 7, "review_count": 2, "last_prompt_key": "k-a"},
                "live-b": {"turn_count": 1, "total_turns": 1, "review_count": 1,
                           "recall": {"injected": 1, "entries": 1, "chars": 90, "ids": [3]}}}
        self.seed_sessions({**stale, **live})
        self.human("rotate the staging certificate")
        self.stop()
        s = self.sessions()
        self.assertEqual([k for k in s if k.startswith("stale-")], [],
                         "past 300 entries, sessions unseen for 30 days are dropped")
        for sid, before in live.items():
            self.assertIn(sid, s, f"{sid} lacked `seen` — it must be stamped, never dropped")
            for k, v in before.items():
                self.assertEqual(s[sid][k], v, f"{sid}.{k} lost")
            self.assertGreaterEqual(s[sid]["seen"], now)
        self.assertEqual(s[self.sid]["total_turns"], 1)
        self.assertGreaterEqual(s[self.sid]["seen"], now)
        self.assertEqual(len(s), 3)

    def test_old_hook_session_recreating_its_entry_survives_three_prunes(self):
        """An older plugin version in another session keeps (re)writing its
        entry without `seen` while the map keeps overflowing."""
        now = int(time.time())
        for rnd in (1, 2, 3):
            st = self.state() or {"version": 2, "files": {}, "session": {}}
            sessions = st.setdefault("session", {})
            sessions["old-hook"] = {"turn_count": rnd, "total_turns": rnd * 10, "review_count": rnd}
            for i in range(400):
                sessions[f"stale-{rnd}-{i:03d}"] = {"turn_count": 1, "seen": now - 31 * 86400}
            (self.home / "state.json").write_text(json.dumps(st))
            self.human(f"round {rnd}: check the queue lag")
            self.stop()
            s = self.sessions()
            self.assertIn("old-hook", s, f"prune {rnd} dropped the old-hook session")
            self.assertEqual(s["old-hook"]["total_turns"], rnd * 10, "its counters survive")
            self.assertIn("seen", s["old-hook"])
            self.assertEqual([k for k in s if k.startswith("stale-")], [])
            self.assertEqual(s[self.sid]["total_turns"], rnd)

    def test_upgrade_drops_no_live_session_the_same_day(self):
        """Every v4.8 entry lacks `seen` (1,765 on the live machine) and gets the
        same fresh stamp: no save that day may drop any of them by sid order —
        a live one would restart its `## turn N` numbering inside today's log.
        A day later the dead ones still hold that stamp and the cap applies."""
        unstamped = {f"v48-{i:04d}": {"turn_count": 2, "total_turns": 5} for i in range(400)}
        self.seed_sessions(unstamped)
        self.human("first prompt after the upgrade")
        self.stop()
        s = self.sessions()
        self.assertEqual(len(s), 401)
        self.assertTrue(all(isinstance(v.get("seen"), int) for v in s.values()))
        self.human("second prompt after the upgrade")
        self.stop()
        s = self.sessions()
        self.assertEqual(len(s), 401, "same day: nothing seen within 24 h is pruned")
        self.assertEqual(s["v48-0000"]["total_turns"], 5)
        # a day later (simulated): the stamps age, the cap applies
        st = self.state()
        for sid, entry in st["session"].items():
            if sid.startswith("v48-"):
                entry["seen"] -= 2 * 86400
        (self.home / "state.json").write_text(json.dumps(st))
        self.human("a prompt the next day")
        self.stop()
        s = self.sessions()
        self.assertEqual(len(s), 300, "the cap applies once the stamps are over a day old")
        self.assertIn(self.sid, s)
        self.assertEqual(s[self.sid]["total_turns"], 3)

    def test_cap_keeps_the_300_most_recent(self):
        now = int(time.time())
        older = {f"r-{i:04d}": {"turn_count": 1, "seen": now - 2 * 86400 - 60 * (i + 1)} for i in range(350)}
        today = {f"t-{i:02d}": {"turn_count": 1, "seen": now - 3600 * (i + 1)} for i in range(5)}
        self.seed_sessions({**older, **today})
        self.human("check the cap")
        self.stop()
        s = self.sessions()
        self.assertEqual(len(s), 300)
        self.assertIn(self.sid, s)
        self.assertEqual(sorted(k for k in s if k.startswith("t-")), sorted(today), "seen today → kept")
        kept = sorted(k for k in s if k.startswith("r-"))
        self.assertEqual(kept, [f"r-{i:04d}" for i in range(294)], "the oldest-seen are the ones dropped")

    def test_non_turn_stop_writes_no_state(self):
        self.human("p1")
        self.stop()
        before = (self.home / "state.json").read_bytes()
        self._append({"type": "user", "promptId": str(uuid.uuid4()), "origin": {"kind": "task-notification"},
                      "message": {"content": "<task-notification>done</task-notification>"}})
        self.stop()
        self.assertEqual((self.home / "state.json").read_bytes(), before)


class SessionMapUnitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.aj = _load("gowth_auto_journal_hyg", "auto-journal.py")

    def prune(self, sessions: dict, current: str, now: int) -> dict:
        state = {"session": sessions}
        self.aj._stamp_and_prune_sessions(state, current, now)
        return state["session"]

    def test_bad_seen_values_are_restamped_not_dropped(self):
        now = 1_800_000_000
        s = self.prune({"a": {"seen": True}, "b": {"seen": "yesterday"}, "c": {"seen": now + 10 * 86400},
                        "d": {"seen": -5}, "e": {"seen": now - 100}, "cur": {"seen": now}}, "cur", now)
        for k in ("a", "b", "c", "d"):
            self.assertEqual(s[k]["seen"], now, k)
        self.assertEqual(s["e"]["seen"], now - 100, "a valid stamp is kept as is")

    def test_current_session_is_never_dropped(self):
        now = 1_800_000_000
        sessions = {f"s{i:03d}": {"seen": now - 2 * 86400 - i} for i in range(400)}
        sessions["zzz-current"] = {"seen": now - 29 * 86400}  # caller stamps it; even if not
        s = self.prune(sessions, "zzz-current", now)
        self.assertIn("zzz-current", s)
        self.assertEqual(len(s), 300)
        s = self.prune({**{f"s{i:03d}": {"seen": now - 40 * 86400} for i in range(400)},
                        "zzz-current": {"seen": now - 40 * 86400}}, "zzz-current", now)
        self.assertEqual(set(s), {"zzz-current"}, "even past the 30-day line")

    def test_ties_break_on_the_session_id_deterministically(self):
        now = 1_800_000_000
        base = {f"t{i:03d}": {"seen": now - 2 * 86400} for i in range(320)}
        a = self.prune(json.loads(json.dumps(base)), "cur", now)
        b = self.prune(json.loads(json.dumps(base)), "cur", now)
        self.assertEqual(sorted(a), sorted(b))
        self.assertEqual(len(a), 300)
        self.assertNotIn("t000", a, "(seen, sid) descending: the smallest ids go first on a tie")
        self.assertIn("t319", a)

    def test_entries_seen_within_a_day_are_never_pruned(self):
        now = 1_800_000_000
        s = self.prune({f"d{i:03d}": {"seen": now - 3600} for i in range(500)}, "cur", now)
        self.assertEqual(len(s), 500, "a session seen today may still be writing today's log")

    def test_small_maps_are_only_stamped(self):
        now = 1_800_000_000
        s = self.prune({"a": {"seen": now - 400 * 86400}, "b": {}}, "a", now)
        self.assertEqual(set(s), {"a", "b"}, "no prune at or under 300 entries")
        self.assertEqual(s["b"]["seen"], now)


class RecallSeenStampTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gowth_hyg_rec_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._old = os.environ.get("GOWTH_MEM_HOME")
        os.environ["GOWTH_MEM_HOME"] = str(self.tmp)
        self.addCleanup(self._restore)
        self.rp = _load("gowth_recall_prompt_hyg", "_recall_prompt.py")

    def _restore(self):
        if self._old is None:
            os.environ.pop("GOWTH_MEM_HOME", None)
        else:
            os.environ["GOWTH_MEM_HOME"] = self._old

    def state(self) -> dict:
        return json.loads((self.tmp / "state.json").read_text())

    def test_record_stamps_written_and_existing_entries_only(self):
        (self.tmp / "state.json").write_text(json.dumps({"session": {"known": {"turn_count": 3}}}))
        t0 = int(time.time())
        self.rp._record("stranger", [], 0)
        self.assertNotIn("stranger", self.state()["session"], "no entry is created just for a stamp")
        self.rp._record("known", [], 0)
        self.assertGreaterEqual(self.state()["session"]["known"]["seen"], t0)
        self.assertEqual(self.state()["session"]["known"]["turn_count"], 3)
        self.rp._record("injected", [{"id": 7}], 120)
        rec = self.state()["session"]["injected"]
        self.assertGreaterEqual(rec["seen"], t0)
        self.assertEqual(rec["recall"]["ids"], [7])
        self.assertEqual(self.state()["recall_daily"][date.today().isoformat()]["prompts"], 3)


# ─── §1.4 session-log provenance ─────────────────────────────────────────────

class CwdHeaderTest(_StopCase):
    def test_new_log_carries_the_cwd_once(self):
        cwd = str(self.root / "projects" / "api")
        self.human("rename the parser module")
        self.stop(cwd=cwd)
        lines = self.log().splitlines()
        self.assertTrue(lines[0].startswith("# Session log — "))
        self.assertEqual(lines[1], f"<!-- cwd: {cwd} -->")
        self.human("now add the retry test")
        self.stop(cwd=cwd)
        self.human("and the docs, from another directory")
        self.stop(cwd=str(self.root / "elsewhere"))
        text = self.log()
        self.assertEqual(text.count("<!-- cwd:"), 1, "written when the log is created, never on later turns")
        self.assertEqual(text.count("## turn "), 3)

    def test_no_cwd_no_line(self):
        self.human("a prompt without a cwd in the Stop input")
        self.stop()
        self.assertTrue(self.log().startswith("# Session log — "))
        self.assertNotIn("<!-- cwd:", self.log())

    def test_secret_in_the_cwd_is_redacted(self):
        self.human("deploy it")
        self.stop(cwd="/srv/glpat-ABCDEFGHIJKLMNOPQRST/app")
        line = self.log().splitlines()[1]
        self.assertTrue(line.startswith("<!-- cwd: /srv/") and line.endswith(" -->"), line)
        self.assertIn("[REDACTED:", line)
        self.assertNotIn("glpat-ABC", self.log())

    def test_hostile_cwd_neither_stalls_nor_lands(self):
        """A 200 KB `a.a.a…` cwd (the shape that once made _privacy take 144 s
        on one Stop) is sanitized only within the 500-char window + slack."""
        self.human("an ordinary prompt")
        t0 = time.perf_counter()
        self.stop(cwd="/x/" + "a." * 100_000)
        self.assertLess(time.perf_counter() - t0, 5.0)
        self.assertIn("**User:** an ordinary prompt", self.log(), "positive signal: the turn was captured")
        self.assertNotIn("<!-- cwd:", self.log(), "longer than 500 chars → no line")


class CwdHeaderUnitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cap = _load("gowth_capture_hyg", "_capture.py")

    def test_cases(self):
        f = self.cap.cwd_header_line
        self.assertEqual(f("/Users/me/proj"), "<!-- cwd: /Users/me/proj -->")
        self.assertEqual(f("/Users/me/My  Drive/x "), "<!-- cwd: /Users/me/My  Drive/x  -->",
                         "the path is verbatim: no collapse, no strip")
        for bad in (None, "", "   ", "relative/path", " /leading/space", 42, "/x/-->/y", "/x/<!--/y",
                    "/" + "p" * 500, "/a b/c\nd", "/a\tb", "/a b", "/a\x85b", "/a\x00b",
                    "/x" + " " * 600 + "y"):
            self.assertEqual(f(bad), "", repr(bad)[:40])
        self.assertEqual(f("/" + "p" * 499), "<!-- cwd: /" + "p" * 499 + " -->", "500 chars is allowed")

    def test_growth_is_flat(self):
        """The new input reaches _privacy.sanitize and the whitespace collapse:
        10k/20k/40k chars on adversarial shapes stay flat (bounded window)."""
        shapes = {
            "a-dot": lambda n: "/" + "a." * (n // 2),
            "whitespace-run": lambda n: "/x" + " " * n + "y",
            "unclosed-private": lambda n: "/" + "<private>" * (n // 9),
            "token-prefix-flood": lambda n: "/" + "glpat-" * (n // 6),
            "separators": lambda n: "/" + "--- " * (n // 4),
        }
        for name, make in shapes.items():
            times = []
            for size in (10_000, 20_000, 40_000):
                text = make(size)
                best = None
                for _ in range(3):
                    t0 = time.perf_counter()
                    out = self.cap.cwd_header_line(text)
                    dt = time.perf_counter() - t0
                    best = dt if best is None else min(best, dt)
                times.append(best)
                self.assertEqual(out, "", (name, size))
            self.assertLess(times[2], 0.5, (name, times))
            self.assertLessEqual(times[2], 2.5 * times[0] + 0.01, (name, times))
        self.assertTrue(self.cap.cwd_header_line("/" + "a." * 200).startswith("<!-- cwd: /a.a."),
                        "positive signal: a short path of the same shape is written")

    def test_repeated_capture_is_byte_identical(self):
        tmp = Path(tempfile.mkdtemp(prefix="gowth_hyg_cap_"))
        old = os.environ.get("GOWTH_MEM_HOME")
        try:
            os.environ["GOWTH_MEM_HOME"] = str(tmp)
            tx = tmp / "t.jsonl"
            tx.write_text("\n".join(json.dumps(r) for r in (
                {"type": "user", "uuid": "u1", "origin": {"kind": "human"}, "message": {"content": "q one"}},
                {"type": "assistant", "uuid": "a1", "message": {"content": [{"type": "text", "text": "a"}]}},
            )) + "\n")
            snaps = []
            for _ in range(3):
                self.assertTrue(self.cap.capture_turn(str(tx), "default", "sessabcd1234", 1, {}, cwd="/w/proj"))
                logs = list((tmp / "workspaces" / "default" / "journal" / "sessions").glob("*.md"))
                self.assertEqual(len(logs), 1)
                snaps.append(logs[0].read_bytes())
            self.assertEqual(len(set(snaps)), 1)
            self.assertIn(b"<!-- cwd: /w/proj -->", snaps[0])
        finally:
            if old is None:
                os.environ.pop("GOWTH_MEM_HOME", None)
            else:
                os.environ["GOWTH_MEM_HOME"] = old
            shutil.rmtree(tmp, ignore_errors=True)


# ─── §1.2 consumers: recall, conflict nag, PreCompact ───────────────────────

class RecallSkipsPipelinePromptsTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="gowth_hyg_rp_"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.vault = self.root / "vault"
        self.proj = self.root / "proj"
        self.proj.mkdir()
        wsd = self.vault / "workspaces" / "demo"
        (wsd / "t").mkdir(parents=True)
        (wsd / "workspace.json").write_text('{"name": "demo"}')
        (wsd / "t" / "00-README.md").write_text("---\nslug: t\ntitle: T\n---\n# T\n")
        (wsd / "t" / "2026-09-10-a.md").write_text(
            "---\nslug: t-a\n---\n## [decision] Use pelican-router for the ingress gateway\n\n"
            "Because pelican-router terminates gRPC at the edge. Rationale: fewer hops on the hot path.\n")
        (self.vault / "config.json").write_text(json.dumps({"active_workspace": "demo"}))
        self.settings()
        r = subprocess.run([sys.executable, str(SCRIPTS / "_index.py")], capture_output=True, text=True,
                           env=_env(self.vault), timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)

    def settings(self, prefixes=None) -> None:
        s = {"layout_version": 3, "recall": {"on_prompt_score_threshold": 0.0}}
        if prefixes is not None:
            s["capture"] = {"pipeline_prefixes": prefixes}
        (self.vault / "settings.json").write_text(json.dumps(s))

    def run_sh(self, prompt: str) -> subprocess.CompletedProcess:
        ev = json.dumps({"prompt": prompt, "cwd": str(self.proj), "session_id": "rp-sess"})
        return subprocess.run(["bash", str(SCRIPTS / "recall-on-prompt.sh")], input=ev, capture_output=True,
                              text=True, env=_env(self.vault), timeout=60)

    def daily_prompts(self) -> int:
        p = self.vault / "state.json"
        if not p.is_file():
            return 0
        day = json.loads(p.read_text()).get("recall_daily", {}).get(date.today().isoformat(), {})
        return int(day.get("prompts", 0))

    def test_pipeline_prompts_get_no_recall_and_no_telemetry(self):
        for prompt in ("STAGE=VERIFY\n" + GOOD, "### TASK 469044d829 R13 ###\n" + GOOD,
                       "Below is a conversation log from a Claude Code coding session.\n" + GOOD):
            r = self.run_sh(prompt)
            self.assertEqual((r.returncode, r.stdout), (0, ""), prompt[:30])
            self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(self.daily_prompts(), 0, "recall_daily is untouched by pipeline prompts")
        r = self.run_sh(GOOD)  # positive control: the same words from a person
        self.assertIn("[gowth-mem:recall ws=demo]", r.stdout)
        self.assertEqual(self.daily_prompts(), 1)

    def test_settings_prefix_silences_recall(self):
        self.settings(prefixes=["[nightly-bot]"])
        r = self.run_sh("[nightly-bot] " + GOOD)
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertEqual(self.daily_prompts(), 0)
        self.assertIn("gowth-mem:recall", self.run_sh(GOOD).stdout)


class ConflictNagSkipsPipelinePromptsTest(unittest.TestCase):
    def setUp(self):
        self.vault = Path(tempfile.mkdtemp(prefix="gowth_hyg_cd_"))
        self.addCleanup(shutil.rmtree, self.vault, True)
        (self.vault / "settings.json").write_text(json.dumps({"layout_version": 3,
                                                              "capture": {"pipeline_prefixes": ["[bot]"]}}))
        (self.vault / "SYNC-CONFLICT.md").write_text("# SYNC-CONFLICT\n\n- workspaces/x/docs/handoff.md\n")

    def nag(self, prompt) -> str:
        r = subprocess.run(["bash", str(SCRIPTS / "conflict-detect.sh")], input=json.dumps({"prompt": prompt}),
                           capture_output=True, text=True, env=_env(self.vault), timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        return r.stdout

    def test_pipeline_prompts_are_not_nagged(self):
        for prompt in (SDK_VERIFY, PASTED_TASK, "[bot] nightly digest"):
            self.assertEqual(self.nag(prompt), "", prompt[:30])
        out = self.nag("why does the sync keep failing?")
        self.assertIn("SYNC-CONFLICT pending", out, "positive control: a person is still told")
        self.assertIn("SYNC-CONFLICT pending", self.nag(None), "an event without a prompt is still nagged")


class PrecompactIgnoresPipelinePromptsTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="gowth_hyg_pc_"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.vault = self.root / "vault"
        wsd = self.vault / "workspaces" / "default"
        (wsd / "journal").mkdir(parents=True)
        (wsd / "workspace.json").write_text('{"name": "default"}')
        (self.vault / "config.json").write_text(json.dumps({"active_workspace": "default"}))
        (self.vault / "settings.json").write_text(json.dumps({"capture": {"pipeline_prefixes": ["[bot]"]}}))
        old = time.time() - 3600  # not "recently flushed"
        for p in self.vault.rglob("*"):
            os.utime(p, (old, old))
        self.tx = self.root / "t.jsonl"

    def transcript(self, prompts) -> str:
        recs = []
        for i, (text, source) in enumerate(prompts):
            rec = {"type": "user", "uuid": f"u{i}", "message": {"content": text}}
            if source == "sdk":
                rec["promptSource"] = "sdk"
            else:
                rec["origin"] = {"kind": "human"}
            recs.append(rec)
            recs.append({"type": "assistant", "uuid": f"a{i}",
                         "message": {"content": [{"type": "text", "text": f"answer {i}"}]}})
        self.tx.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        return str(self.tx)

    def flush(self, tx: str) -> subprocess.CompletedProcess:
        r = subprocess.run([sys.executable, str(SCRIPTS / "precompact-flush.py")],
                           input=json.dumps({"transcript_path": tx, "trigger": "manual"}),
                           capture_output=True, text=True, env=_env(self.vault), timeout=60)
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        return r

    def journal(self) -> str:
        p = self.vault / "workspaces" / "default" / "journal" / f"{datetime.now():%Y-%m-%d}.md"
        return p.read_text() if p.is_file() else ""

    def test_pipeline_rounds_are_neither_counted_nor_dumped(self):
        tx = self.transcript([("please fix the login bug", "human"), (SDK_VERIFY, "sdk"),
                              (PASTED_TASK, "human"), ("[bot] digest", "human"),
                              ("also update the docs", "human")])
        self.flush(tx)
        dump = self.journal()
        self.assertIn("please fix the login bug", dump, "positive signal: the dump ran")
        self.assertIn("also update the docs", dump)
        for marker in ("STAGE=", "### TASK", "[bot] digest"):
            self.assertNotIn(marker, dump)

    def test_a_pure_pipeline_session_is_not_dumped(self):
        tx = self.transcript([(SDK_VERIFY, "sdk"), (PASTED_TASK, "human"), ("[bot] digest", "human")])
        self.flush(tx)
        self.assertEqual(self.journal(), "", "no human turns → nothing to flush")
        pf = _load("gowth_precompact_hyg", "precompact-flush.py")
        self.assertEqual(pf.user_turn_count(tx), 1, "without the settings snapshot only [bot] counts")
        self.assertEqual(pf.user_turn_count(tx, {"capture": {"pipeline_prefixes": ["[bot]"]}}), 0)


if __name__ == "__main__":
    unittest.main()
