"""v4.9 WP-B1 — the daily-curator trigger, driven through the real Stop hook
(`auto-journal.py` as a subprocess, as Claude Code runs it).

Spec §4.1/§4.2. Pinned here:
  * due once `curator.min_new_turns` unprocessed (non-pipeline) turns exist →
    ONE directive, a claim marker (`.curator/<ws>/claim-<today>-1`, the
    `.curator/` ignore line first), the capture-off counter reset;
  * claimed → silent; attempt 2 only after `curator.retry_minutes`; never 3;
  * two missed days → `[gowth-mem:curator-paused …]` once per calendar day;
  * deferred whenever another directive fired this Stop;
  * never in an SDK / `claude -p` session (promptSource, CLAUDE_CODE_ENTRYPOINT);
  * the signal floor — session-log turns, or with capture off the per-
    workspace counter incremented in the locked turn increment;
  * one curating host per workspace + failover after 2 days; curator.hosts;
  * curator.enabled "false" switches it off (string-false knob test);
  * the first Stop on a fresh vault stays silent;
  * a re-created session entry continues today's log numbering (no repeated
    `## turn N`).
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
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "hooks" / "scripts"
HOOK = SCRIPTS / "auto-journal.py"
SCRUB = ("AI_AGENT", "GOWTH_WORKSPACE", "CLAUDE_SUBAGENT", "GOWTH_MEM_OFF", "CLAUDE_CODE_ENTRYPOINT")
NEW_CC = "claude-code_2-1-283_harness"
TOKEN = "[gowth-mem:curator ws=default]"
PAUSED = "[gowth-mem:curator-paused ws=default]"


class _Hook(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="gowth_curtrig_"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.home = self.root / "vault"
        self.wsd = self.home / "workspaces" / "default"
        (self.wsd / "journal").mkdir(parents=True)
        (self.wsd / "workspace.json").write_text(json.dumps({"name": "default"}))
        (self.home / "config.json").write_text(json.dumps({"active_workspace": "default", "host_id": "mac"}))
        self.write_settings()
        self.tx = self.root / "transcript.jsonl"
        self.tx.write_text("")
        self.sid = "cur-" + uuid.uuid4().hex[:8]
        self.prompt_id = ""
        self.today = datetime.now().strftime("%Y-%m-%d")

    def write_settings(self, curator: dict | None = None, **extra) -> None:
        s = {"auto_journal": {"journal_every": 99, "auto_journal_enabled": True},
             "reflection": {"enabled": True, "turn_interval": 99, "min_review_turns": 1},
             "journal": {"auto_forget_enabled": False},
             "sync": {"auto_sync_on_stop": False},
             "native": {"enabled": False}}
        if curator is not None:
            s["curator"] = curator
        s.update(extra)
        (self.home / "settings.json").write_text(json.dumps(s))

    def _append(self, rec: dict) -> None:
        rec.setdefault("uuid", str(uuid.uuid4()))
        with self.tx.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    def human(self, text: str = "", **fields) -> None:
        self.prompt_id = str(uuid.uuid4())
        rec = {"type": "user", "promptId": self.prompt_id, "message": {"role": "user",
                                                                        "content": text or f"please work on item {uuid.uuid4().hex[:6]}"}}
        if not fields:
            rec["origin"] = {"kind": "human"}
        rec.update(fields)
        self._append(rec)
        self._append({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "done"},
            {"type": "tool_use", "name": "Edit", "input": {"file_path": "/repo/x.py"}}]}})

    def stop(self, extra_env: dict | None = None) -> str:
        """One Stop; returns the directive text ('' when silent)."""
        payload = {"session_id": self.sid, "hook_event_name": "Stop", "stop_hook_active": False,
                   "transcript_path": str(self.tx), "prompt_id": self.prompt_id,
                   "cwd": str(self.root)}
        env = {k: v for k, v in os.environ.items() if k not in SCRUB}
        env.update({"GOWTH_MEM_HOME": str(self.home), "CLAUDE_CONFIG_DIR": str(self.root / "cc"),
                    "AI_AGENT": NEW_CC})
        env.update(extra_env or {})
        r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), capture_output=True,
                           text=True, env=env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self._append({"type": "system", "subtype": "stop_hook_summary", "hookErrors": [],
                      "hookAdditionalContext": [], "preventedContinuation": False})
        d = json.loads(r.stdout) if r.stdout.strip() else {}
        hso = d.get("hookSpecificOutput") or {}
        self.assertNotIn("decision", d, "the curator directive must use the non-error channel")
        return hso.get("additionalContext") or ""

    def turn(self, text: str = "", **kw) -> str:
        self.human(text, **{k: v for k, v in kw.items() if k != "extra_env"})
        return self.stop(kw.get("extra_env"))

    def state(self) -> dict:
        p = self.home / "state.json"
        return json.loads(p.read_text()) if p.is_file() else {}

    def claim_path(self, n: int, day: str | None = None) -> Path:
        return self.home / ".curator" / "default" / f"claim-{day or self.today}-{n}"

    def write_worklog(self, day: str, host: str, run6: str = "abcdef") -> Path:
        d = self.wsd / "docs" / "worklog"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{day}-{host}-{run6}.md"
        p.write_text(f"---\ntype: worklog\nws: default\ndate: {day}\nhost: {host}\nrun: {run6}000000\n"
                     f"phase: 2\nprocessed: []\n---\n")
        return p


class TriggerTest(_Hook):
    def test_fresh_vault_first_stop_is_silent(self):
        self.assertEqual(self.turn(), "")
        self.assertFalse(self.claim_path(1).exists())

    def test_due_after_the_floor_then_claimed_and_silent(self):
        for i in range(4):
            self.assertEqual(self.turn(), "", f"turn {i + 1} is below the floor")
        directive = self.turn()
        self.assertTrue(directive.startswith(TOKEN), directive)
        self.assertIn('subagent_type "gowth-mem:curator"', directive)
        self.assertIn("You are the dispatched gowth-mem curator — never dispatch further subagents.", directive)
        self.assertIn(f"Vault root = {os.path.abspath(str(self.home))}.", directive)
        agent = directive.split("Execute the curator protocol in ", 1)[1].split(" for ws=", 1)[0]
        self.assertTrue(Path(agent).is_file() and Path(agent).is_absolute(), agent)
        scripts = directive.split("Scripts = ", 1)[1].split('."', 1)[0]
        self.assertTrue((Path(scripts) / "_curate.py").is_file(), scripts)
        self.assertIn("model sonnet", directive)
        self.assertLess(len(directive), 1500)
        self.assertTrue(self.claim_path(1).is_file())
        self.assertIn(".curator/", (self.home / ".gitignore").read_text().splitlines())
        self.assertEqual(self.state()["curator_turns"]["default"], 0, "the claim resets the counter")
        for _ in range(3):
            self.assertEqual(self.turn(), "", "claimed today → silent")
        self.assertFalse(self.claim_path(2).exists())

    def test_attempt_2_after_retry_minutes_and_never_a_third(self):
        for _ in range(5):
            self.turn()
        self.assertTrue(self.claim_path(1).is_file())
        old = time.time() - 121 * 60
        os.utime(self.claim_path(1), (old, old))
        self.assertTrue(self.turn().startswith(TOKEN))
        self.assertTrue(self.claim_path(2).is_file())
        os.utime(self.claim_path(2), (old, old))
        self.assertEqual(self.turn(), "")
        self.assertFalse(self.claim_path(3).exists())

    def test_two_missed_days_pause_once_per_day(self):
        d = self.home / ".curator" / "default"
        d.mkdir(parents=True)
        for back in (1, 2):
            day = (date.today() - timedelta(days=back)).isoformat()
            (d / f"claim-{day}-1").write_text("x")
        notice = self.turn()
        self.assertTrue(notice.startswith(PAUSED), notice)
        self.assertIn("/mem-ops curate now", notice)
        st = self.state()
        self.assertEqual(st["curator_misses"]["default"], 2)
        self.assertEqual(st["curator_paused_day"]["default"], self.today)
        for _ in range(5):
            self.assertEqual(self.turn(), "", "the paused notice is once per day; no claim while paused")
        self.assertFalse(self.claim_path(1).exists())

    def test_a_worklog_resets_misses(self):
        d = self.home / ".curator" / "default"
        d.mkdir(parents=True)
        days = [(date.today() - timedelta(days=b)).isoformat() for b in (3, 2)]
        for day in days:
            (d / f"claim-{day}-1").write_text("x")
        self.write_worklog((date.today() - timedelta(days=1)).isoformat(), "mac")
        outs = [self.turn() for _ in range(5)]
        self.assertEqual(outs[:4], [""] * 4)
        self.assertTrue(outs[4].startswith(TOKEN), outs[4])
        self.assertEqual(self.state()["curator_misses"]["default"], 0)

    def test_deferred_when_another_directive_fires(self):
        self.write_settings(reflection={"enabled": True, "turn_interval": 5, "min_review_turns": 1})
        outs = [self.turn() for _ in range(5)]
        self.assertIn("[gowth-mem:self-review ws=default]", outs[4])
        self.assertNotIn("[gowth-mem:curator", outs[4], "deferred, not appended")
        self.assertFalse(self.claim_path(1).exists(), "a deferred curator does not claim")
        nxt = self.turn()
        self.assertTrue(nxt.startswith(TOKEN), nxt)
        self.assertNotIn("self-review", nxt)

    def test_sdk_sessions_never_trigger(self):
        for _ in range(6):
            self.assertEqual(self.turn(promptSource="sdk"), "")
        self.assertFalse(self.claim_path(1).exists())
        self.assertGreaterEqual(self.state()["session"][self.sid]["total_turns"], 6, "sdk turns still count")
        out = [self.turn(extra_env={"CLAUDE_CODE_ENTRYPOINT": "sdk-ts"}) for _ in range(2)]
        self.assertEqual(out, ["", ""])
        self.assertFalse(self.claim_path(1).exists())
        self.assertTrue(self.turn().startswith(TOKEN), "an interactive turn is due")

    def test_capture_off_counts_turns_per_workspace(self):
        self.write_settings(reflection={"enabled": False, "turn_interval": 99, "min_review_turns": 1,
                                        "capture_enabled": False})
        for i in range(4):
            self.assertEqual(self.turn(), "")
            self.assertEqual(self.state()["curator_turns"]["default"], i + 1)
        self.assertFalse((self.wsd / "journal" / "sessions").exists(), "capture is off")
        self.assertTrue(self.turn().startswith(TOKEN))
        self.assertEqual(self.state()["curator_turns"]["default"], 0)

    def test_pipeline_turns_never_reach_the_floor(self):
        for i in range(6):
            self.assertEqual(self.turn(f"STAGE=VERIFY\nVerify the mention {i} before the operator decides."), "")
        self.assertFalse(self.claim_path(1).exists())

    def test_one_curating_host_per_workspace_and_failover(self):
        yday = (date.today() - timedelta(days=1)).isoformat()
        p = self.write_worklog(yday, "laptop")
        for _ in range(6):
            self.assertEqual(self.turn(), "", "laptop curated yesterday")
        p.rename(p.with_name(f"{(date.today() - timedelta(days=2)).isoformat()}-laptop-abcdef.md"))
        self.assertTrue(self.turn().startswith(TOKEN), "failover after 2 days")

    def test_curator_hosts(self):
        self.write_settings(curator={"hosts": ["laptop"]})
        for _ in range(6):
            self.assertEqual(self.turn(), "")
        self.write_settings(curator={"hosts": ["laptop", "Mac.local"]})
        self.assertTrue(self.turn().startswith(TOKEN))

    def test_enabled_string_false_is_off(self):
        self.write_settings(curator={"enabled": "false"})
        for _ in range(6):
            self.assertEqual(self.turn(), "")
        self.assertFalse((self.home / ".curator").exists())

    def test_settings_shape_the_directive(self):
        self.write_settings(curator={"min_new_turns": 2, "model": "opus"})
        self.assertEqual(self.turn(), "")
        out = self.turn()
        self.assertTrue(out.startswith(TOKEN))
        self.assertIn("model opus", out)


class TurnNumberSeedTest(_Hook):
    def _log(self) -> Path:
        return self.wsd / "journal" / "sessions" / f"{self.today}-{self.sid[:8]}.md"

    def _seed_log(self, n: int) -> None:
        p = self._log()
        p.parent.mkdir(parents=True, exist_ok=True)
        blocks = "".join(f"\n## turn {i} — 09:0{i}\n**User:** earlier {i}\n**Claude:** ok\n**Actions:** \n"
                         for i in range(1, n + 1))
        p.write_text(f"# Session log — {self.today} — {self.sid[:8]}\n\n_x_\n" + blocks)

    def _numbers(self) -> list:
        import re
        return [int(m) for m in re.findall(r"^## turn (\d+) ", self._log().read_text(), re.M)]

    def test_recreated_entry_continues_the_numbering(self):
        self._seed_log(3)
        self.turn("first prompt after the session entry was pruned")
        self.turn("second prompt")
        self.assertEqual(self._numbers(), [1, 2, 3, 4, 5])
        self.assertEqual(self.state()["session"][self.sid]["total_turns"], 5)

    def test_entry_created_by_recall_telemetry_is_seeded_too(self):
        self._seed_log(2)
        (self.home / "state.json").write_text(json.dumps({"version": 2, "files": {}, "session": {
            self.sid: {"turn_count": 0, "recall": {"injected": 1, "entries": 1, "chars": 10, "ids": [1]}}}}))
        self.turn("a prompt that recall saw first")
        self.assertEqual(self._numbers(), [1, 2, 3])

    def test_existing_counter_is_kept(self):
        self._seed_log(2)
        (self.home / "state.json").write_text(json.dumps({"version": 2, "files": {}, "session": {
            self.sid: {"turn_count": 2, "total_turns": 2, "review_count": 2}}}))
        self.turn("third")
        self.assertEqual(self._numbers(), [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
