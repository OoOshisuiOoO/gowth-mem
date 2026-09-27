#!/usr/bin/env python3
"""v4.7.3 — the cadence counters count REAL user turns, not Stop events.

THE DEFECT THIS PINS
--------------------
Claude Code fires `Stop` at the end of every continuation, not only at the end
of a human prompt's response:

  * after ANY Stop hook blocks / feeds back (ours included) — the model is
    re-invoked and Stops again with `stop_hook_active: true`;
  * after every background-agent `<task-notification>` (the memory teammate
    and the judge gowth-mem itself dispatches), `teammate-message`, loop tick…

auto-journal.py bumped `turn_count`/`review_count`/`total_turns` on every one
of them. Measured on a live machine (15 long sessions, 30 days): 474 Stops
counted as turns for 346 real human prompts — x1.37. All 55 hook-feedback
continuations were gowth-mem's OWN, so every cadence it fired shortened the
wait for the next one, and the session log recorded "Stop hook feedback: …" and
`<task-notification>` XML as the user's prompt for the judge to score.

The identity of a turn is the transcript record where the human spoke:
`origin.kind == "human"` (Claude Code 2.1.x), keyed by its `uuid` — never its
text ("tiếp", "ok", "làm đi" legitimately repeat). Transcripts that carry no
record identity (legacy / hand-built) keep per-Stop counting, minus
`stop_hook_active` continuations.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "hooks" / "scripts"
HOOK = SCRIPTS_DIR / "auto-journal.py"

NEW_CC = "claude-code_2-1-283_harness"  # has the Stop additionalContext channel


def _load(name: str):
    sys.path.insert(0, str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _directive(d: dict) -> str:
    """Text Claude is told to act on, from EITHER Stop channel ('' if none)."""
    if d.get("decision") == "block":
        return d.get("reason") or ""
    hso = d.get("hookSpecificOutput")
    if isinstance(hso, dict) and hso.get("hookEventName") == "Stop":
        return hso.get("additionalContext") or ""
    return ""


class Transcript:
    """Builds transcripts in the record shapes Claude Code 2.1.283 writes
    (verified against live ~/.claude/projects JSONL): human prompts carry
    `origin: {"kind": "human"}` + a fresh `promptId`; Stop-hook feedback is an
    `isMeta` user record REUSING the prompt's promptId; task notifications are
    `origin: {"kind": "task-notification"}` with their own promptId."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.write_text("", encoding="utf-8")
        self.prompt_id = ""
        # promptId of the record that started the chain the next Stop ends —
        # Claude Code sends it as the Stop input's `prompt_id`.
        self.chain_prompt_id = ""

    def _append(self, rec: dict) -> dict:
        rec.setdefault("uuid", str(uuid.uuid4()))
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        return rec

    def human(self, text: str, reply: str = "done", tool: str = "Edit",
              flushed: bool = True) -> dict:
        """flushed=False: the Stop-time reality for a tool-less answer — Claude
        Code writes the final assistant record AFTER the Stop hook ran
        (verified with a transcript snapshot taken inside a real Stop hook)."""
        self.prompt_id = str(uuid.uuid4())
        self.chain_prompt_id = self.prompt_id
        rec = self._append({"type": "user", "promptId": self.prompt_id,
                            "origin": {"kind": "human"},
                            "message": {"role": "user", "content": text}})
        if flushed:
            self.assistant(reply, tool)
        return rec

    def assistant(self, text: str, tool: str | None = None) -> None:
        content: list = [{"type": "text", "text": text}]
        if tool:
            content.append({"type": "tool_use", "name": tool,
                            "input": {"file_path": f"/repo/{tool.lower()}.py"}})
        self._append({"type": "assistant", "message": {"role": "assistant",
                                                       "content": content}})

    def hook_feedback(self, text: str) -> None:
        self._append({"type": "user", "isMeta": True, "promptId": self.prompt_id,
                      "message": {"role": "user",
                                  "content": f"Stop hook feedback:\n{text}"}})
        self.assistant("dispatched the background agent", "Agent")

    def notification(self) -> None:
        self.chain_prompt_id = str(uuid.uuid4())
        self._append({"type": "user", "promptId": self.chain_prompt_id,
                      "origin": {"kind": "task-notification"},
                      "message": {"role": "user", "content": (
                          "<task-notification>\n<task-id>bg123</task-id>\n"
                          "<status>completed</status>\n<summary>judge done</summary>\n"
                          "</task-notification>")}})
        self.assistant("Judge summary: P3/R3/C3")

    def teammate_message(self) -> None:
        self.chain_prompt_id = str(uuid.uuid4())
        self._append({"type": "user", "promptId": self.chain_prompt_id,
                      "message": {"role": "user", "content": (
                          "Another Claude session sent a message:\n"
                          '<teammate-message teammate_id="mem-teammate">idle</teammate-message>')}})
        self.assistant("noted")

    def skill_expansion(self) -> None:
        self._append({"type": "user", "isMeta": True, "promptId": self.prompt_id,
                      "message": {"role": "user", "content": [{"type": "text", "text": (
                          "Base directory for this skill: /x/skills/y\n\n# Some Skill\n...")}]}})

    def huge_tool_output(self, kb: int = 600) -> None:
        """A tool result bigger than capture's 512 KB tail window — pushes the
        human prompt out of the tail (heavy real sessions do this routinely)."""
        self._append({"type": "user", "promptId": self.prompt_id,
                      "message": {"role": "user", "content": [
                          {"type": "tool_result", "tool_use_id": "t1", "content": "x" * (kb * 1024)}]}})
        self.assistant("processed the big output")

    def compact_summary(self) -> None:
        self._append({"type": "user", "isCompactSummary": True, "isVisibleInTranscriptOnly": True,
                      "message": {"role": "user", "content": (
                          "This session is being continued from a previous conversation that "
                          "ran out of context. The summary below covers the earlier portion.")}})
        self.assistant("continuing after compaction", "Read")

    def headless_prompt(self, text: str, prompt_source: str | None = None,
                        flushed: bool = True) -> dict:
        """`claude -p` prompts are recorded WITHOUT origin (1,395 live samples);
        2.1.181+ tags them `promptSource: "sdk"`."""
        self.prompt_id = str(uuid.uuid4())
        self.chain_prompt_id = self.prompt_id
        rec = {"type": "user", "promptId": self.prompt_id,
               "message": {"role": "user", "content": text}}
        if prompt_source:
            rec["promptSource"] = prompt_source
        rec = self._append(rec)
        if flushed:
            self.assistant("done")
        return rec

    def typed_ahead(self, text: str, reply: str = "handled the new ask", tool: str = "Bash") -> None:
        """A prompt typed while the model works: an ATTACHMENT, not a user
        record (123 live samples) — origin lives on the attachment."""
        self._append({"type": "attachment", "attachment": {
            "type": "queued_command", "prompt": text, "commandMode": "prompt",
            "origin": {"kind": "human"}}})
        self.assistant(reply, tool)

    def queued_notification(self) -> None:
        self._append({"type": "attachment", "attachment": {
            "type": "queued_command", "prompt": "<task-notification>x</task-notification>",
            "commandMode": "task-notification", "origin": {"kind": "task-notification"}}})

    def bash(self, cmd: str, output: str, model_replies: bool) -> None:
        """`!cmd` bash mode: an origin-less input record + an output record; the
        model sometimes replies (3 of 5 live samples)."""
        self._append({"type": "user", "message": {"role": "user",
                                                  "content": f"<bash-input>{cmd}</bash-input>"}})
        self._append({"type": "user", "message": {"role": "user", "content": (
            f"<bash-stdout>{output}</bash-stdout><bash-stderr></bash-stderr>")}})
        if model_replies:
            self.assistant("looked at the command output", "Read")

    def local_command(self, name: str) -> None:
        """`/model`-style local commands: origin-less, never answered."""
        self._append({"type": "user", "message": {"role": "user", "content": (
            f"<command-name>/{name}</command-name>\n<command-message>{name}</command-message>")}})
        self._append({"type": "user", "message": {"role": "user", "content": (
            "<local-command-stdout>Set model</local-command-stdout>")}})

    def origin_prompt(self, kind: str, text: str, answered: bool = True) -> None:
        self.chain_prompt_id = str(uuid.uuid4())
        self._append({"type": "user", "promptId": self.chain_prompt_id, "origin": {"kind": kind},
                      "message": {"role": "user", "content": text}})
        if answered:
            self.assistant("answered")

    def human_origin_with_prefix(self, text: str) -> None:
        self.prompt_id = str(uuid.uuid4())
        self._append({"type": "user", "promptId": self.prompt_id, "origin": {"kind": "human"},
                      "message": {"role": "user", "content": text}})
        self.assistant("explained it")


class HookHarness(unittest.TestCase):
    AI_AGENT: str | None = NEW_CC

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        ws = self.home / "workspaces" / "default"
        (ws / "journal").mkdir(parents=True)
        (ws / "workspace.json").write_text(json.dumps({"name": "default"}))
        (self.home / "config.json").write_text(json.dumps({"active_workspace": "default"}))
        self.settings()
        self.tx = Transcript(self.home / "transcript.jsonl")
        self.sid = "turncount-" + uuid.uuid4().hex[:6]

    def settings(self, journal_every: int = 99, turn_interval: int = 99,
                 min_review_turns: int = 1) -> None:
        (self.home / "settings.json").write_text(json.dumps({
            "auto_journal": {"journal_every": journal_every, "auto_journal_enabled": True},
            "reflection": {"enabled": True, "turn_interval": turn_interval,
                           "min_review_turns": min_review_turns},
            "journal": {"auto_forget_enabled": False},
        }))

    def stop(self, stop_hook_active=False, transcript: bool = True,
             prompt_id: str | None = "__chain__") -> dict:
        payload: dict = {"session_id": self.sid, "hook_event_name": "Stop",
                         "stop_hook_active": stop_hook_active}
        pid = self.tx.chain_prompt_id if prompt_id == "__chain__" else prompt_id
        if pid:
            payload["prompt_id"] = pid
        if transcript:
            payload["transcript_path"] = str(self.tx.path)
        env = {**os.environ, "GOWTH_MEM_HOME": str(self.home),
               # isolate the backlog scan from the real ~/.claude/projects
               "CLAUDE_CONFIG_DIR": str(self.home / "claude-config")}
        # runner env must not leak in (a GOWTH_WORKSPACE=trade shell broke 8 tests)
        for k in ("AI_AGENT", "GOWTH_WORKSPACE", "CLAUDE_SUBAGENT"):
            env.pop(k, None)
        if self.AI_AGENT:
            env["AI_AGENT"] = self.AI_AGENT
        r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, f"hook must exit 0; stderr: {r.stderr}")
        self.assertNotIn("Traceback", r.stderr)
        out = r.stdout.strip()
        d = json.loads(out) if out else {}
        if transcript:
            # Claude Code writes the Stop's summary AFTER its hooks ran — the
            # boundary between one turn's records and the next.
            # Mirror the transcript: a legacy (uuid-less) one stays uuid-less.
            ctx = _directive(d)
            legacy = not any(isinstance(json.loads(line).get("uuid"), str)
                             for line in self.tx.path.read_text(encoding="utf-8").splitlines()
                             if line.strip())
            self.tx._append({"type": "system", "subtype": "stop_hook_summary",
                             "hookErrors": [ctx] if d.get("decision") == "block" else [],
                             "hookAdditionalContext": [ctx] if "hookSpecificOutput" in d and ctx else [],
                             "preventedContinuation": False,
                             **({"uuid": None} if legacy else {})})
        return d

    def state(self) -> dict:
        sp = self.home / "state.json"
        if not sp.is_file():
            return {}
        return json.loads(sp.read_text()).get("session", {}).get(self.sid, {})

    def log(self) -> str:
        day = datetime.now().strftime("%Y-%m-%d")
        p = (self.home / "workspaces" / "default" / "journal" / "sessions"
             / f"{day}-{self.sid[:8]}.md")
        return p.read_text(encoding="utf-8") if p.is_file() else ""

    def turn_blocks(self) -> int:
        return sum(1 for line in self.log().splitlines() if line.startswith("## turn "))


class TestContinuationsAreNotTurns(HookHarness):
    def test_stop_hook_active_does_not_count(self):
        self.tx.human("rename the parser module")
        self.stop()
        self.assertEqual(self.state().get("total_turns"), 1)
        self.tx.hook_feedback("[other-plugin] keep going")
        d = self.stop(stop_hook_active=True)
        self.assertEqual(self.state().get("total_turns"), 1,
                         "a Stop-hook continuation is not a user turn")
        self.assertEqual(self.turn_blocks(), 1, "a continuation must not add a turn block")
        self.assertEqual(_directive(d), "", "a continuation must never carry a directive")

    def test_our_own_directive_does_not_feed_the_next_cadence(self):
        """turn_interval=1 is the sharpest case: counting the continuation that
        OUR directive causes re-fires the review on it — a block loop that only
        Claude Code's 8-consecutive-block cap ends."""
        self.settings(turn_interval=1, min_review_turns=1)
        self.tx.human("review the retry logic")
        d = self.stop()
        self.assertIn("[gowth-mem:self-review ws=", _directive(d))
        self.tx.hook_feedback(_directive(d))
        d = self.stop(stop_hook_active=True)
        self.assertEqual(_directive(d), "", "our own continuation must not re-fire the review")
        self.assertEqual(self.state().get("review_count"), 0)

    def test_stop_hook_active_string_is_coerced(self):
        """The flag only decides on transcripts WITHOUT record identity."""
        self.tx.path.write_text(json.dumps(
            {"type": "user", "message": {"content": "legacy prompt"}}) + "\n")
        self.stop()
        self.stop(stop_hook_active="true")
        self.assertEqual(self.state().get("total_turns"), 1)

    def test_prompt_typed_during_a_continuation_counts(self):
        """Reviewer finding (live @2505): two typed-ahead asks + 32 tool calls
        ran inside OUR dispatch continuation — an unconditional
        stop_hook_active skip dropped them. With record identity the flag is
        redundant: a continuation without new human input compares equal."""
        self.tx.human("p1")
        self.stop()
        self.tx.hook_feedback("[gowth-mem:auto-journal ws=default] DELEGATE …")
        self.tx.typed_ahead("also rotate that leaked key")
        self.stop(stop_hook_active=True)
        self.assertEqual(self.state().get("total_turns"), 2)
        self.assertIn("**User:** also rotate that leaked key", self.log())


class TestNonHumanReinvocationsAreNotTurns(HookHarness):
    def test_task_notification_relay_does_not_count(self):
        self.tx.human("run the migration dry-run")
        self.stop()
        self.tx.notification()
        self.stop()
        self.assertEqual(self.state().get("total_turns"), 1,
                         "a background agent finishing is not a user turn")
        self.assertEqual(self.turn_blocks(), 1)
        self.assertNotIn("<task-notification", self.log(),
                         "notification XML must never be recorded as the user's prompt")

    def test_teammate_message_does_not_count(self):
        self.tx.human("coordinate with the teammate")
        self.stop()
        self.tx.teammate_message()
        self.stop()
        self.assertEqual(self.state().get("total_turns"), 1)

    def test_repeated_stop_on_same_prompt_counts_once(self):
        self.tx.human("check the cluster")
        for _ in range(3):
            self.stop()
        self.assertEqual(self.state().get("total_turns"), 1)


class TestHumanPromptsAreTurns(HookHarness):
    def test_each_new_prompt_counts(self):
        for text in ("first ask", "second ask", "third ask"):
            self.tx.human(text)
            self.stop()
        self.assertEqual(self.state().get("total_turns"), 3)
        self.assertEqual(self.turn_blocks(), 3)

    def test_identical_text_is_still_a_new_turn(self):
        """Identity is the record, never the text: users do say "tiếp" twice."""
        self.tx.human("tiếp")
        self.stop()
        self.tx.human("tiếp")
        self.stop()
        self.assertEqual(self.state().get("total_turns"), 2)


class TestPromptOutsideTheTail(HookHarness):
    """Found by replaying a live 63-Stop session: when a turn's tool output
    exceeds the 512 KB tail, the human prompt is no longer visible. The
    decision must come from the NEWEST prompt-like record, not from 'the
    newest human record we can still see'."""

    def test_notification_after_a_huge_turn_is_not_a_turn(self):
        self.tx.human("dump the whole cluster state")
        self.tx.huge_tool_output()
        self.stop()                      # the real end of the turn → counted
        self.assertEqual(self.state().get("total_turns"), 1)
        self.tx.notification()
        self.stop()                      # prompt out of the tail, newest record = notification
        self.assertEqual(self.state().get("total_turns"), 1,
                         "a notification relay is not a turn even when the prompt left the tail")
        self.tx.human("now summarize it")
        self.stop()
        self.assertEqual(self.state().get("total_turns"), 2)

    def test_huge_turn_is_still_captured_with_its_prompt(self):
        self.tx.human("dump the whole cluster state")
        self.tx.huge_tool_output()
        self.stop()
        self.assertEqual(self.turn_blocks(), 1)
        self.assertIn("**User:** dump the whole cluster state", self.log())


class TestMidTurnMachineMessages(HookHarness):
    """Found by auditing every Stop of a live session against ground truth: a
    teammate message delivered WHILE the model worked on a human prompt is the
    newest prompt-like record at the Stop — the turn must still count."""

    def test_teammate_message_mid_turn_does_not_swallow_the_turn(self):
        self.tx.human("drop the otel database safely")
        self.tx.teammate_message()
        self.stop()
        self.assertEqual(self.state().get("total_turns"), 1)
        self.assertIn("**User:** drop the otel database safely", self.log())

    def test_notification_mid_turn_does_not_swallow_the_turn(self):
        self.tx.human("p1")
        self.stop()
        self.tx.human("p2")
        self.tx.notification()
        self.stop()
        self.assertEqual(self.state().get("total_turns"), 2)


class TestReviewerFindings(HookHarness):
    """Adversarial-review round 1 (all verified against live transcripts)."""

    def test_typed_ahead_prompt_mid_turn_counts_once(self):
        self.tx.human("p1")
        self.stop()
        self.tx.typed_ahead("p2 typed while you worked")
        self.stop()
        self.assertEqual(self.state().get("total_turns"), 2)
        self.stop()  # a later relay Stop: nothing new asked
        self.assertEqual(self.state().get("total_turns"), 2)

    def test_queued_task_notification_is_machine(self):
        self.tx.human("p1")
        self.stop()
        self.tx.queued_notification()
        self.tx.assistant("relayed")
        self.stop()
        self.assertEqual(self.state().get("total_turns"), 1)

    def test_bash_mode_the_model_answered_counts_and_logs_the_command(self):
        self.tx.human("p1")
        self.stop()
        self.tx.bash("kubectl get secret db -o yaml", "password: hunter2-plaintext",
                     model_replies=True)
        self.stop()
        self.assertEqual(self.state().get("total_turns"), 2)
        log = self.log()
        self.assertIn("**User:** <bash-input>kubectl get secret db -o yaml</bash-input>", log)
        self.assertNotIn("hunter2", log, "bash OUTPUT must never be recorded as the user's words")

    def test_bash_mode_without_a_reply_is_not_a_turn(self):
        self.tx.human("p1")
        self.stop()
        self.tx.bash("ls", "a b", model_replies=False)
        self.tx.notification()
        self.stop()
        self.assertEqual(self.state().get("total_turns"), 1)

    def test_local_command_then_relay_is_not_a_turn(self):
        self.tx.human("p1")
        self.stop()
        self.tx.local_command("model")
        self.tx.notification()
        self.stop()
        self.assertEqual(self.state().get("total_turns"), 1)
        self.assertNotIn("<command-name>", self.log())

    def test_teammate_only_session_never_counts(self):
        """29 live agent-team teammate sessions (26 ran this hook) have only
        <teammate-message> prompts. Falling back to per-Stop counting there
        fired cadences with no capturable log → inline journal in the
        teammate + a misleading 'capture_enabled is false' notice."""
        self.settings(journal_every=2, turn_interval=2, min_review_turns=1)
        for _ in range(4):
            self.tx.teammate_message()
            d = self.stop()
            self.assertEqual(_directive(d), "", "a teammate session must never get a directive")
        self.assertIsNone(self.state().get("total_turns"))
        self.assertEqual(self.log(), "")

    def test_human_origin_is_authoritative_over_prefixes(self):
        """Users now SEE the gold 'Stop hook feedback: …' label and paste it."""
        self.tx.human_origin_with_prefix("Stop hook feedback: [gowth-mem:self-review] why?")
        self.stop()
        self.assertEqual(self.state().get("total_turns"), 1)
        self.assertIn("**User:** Stop hook feedback: [gowth-mem:self-review] why?", self.log())


class TestTransparentAndAmbiguousRecords(HookHarness):
    def test_compaction_summary_is_not_the_prompt(self):
        self.tx.human("migrate the monitoring cluster")
        self.tx.compact_summary()
        self.stop()
        self.assertEqual(self.state().get("total_turns"), 1)
        self.assertIn("**User:** migrate the monitoring cluster", self.log())
        self.assertNotIn("This session is being continued", self.log())

    def test_headless_prompt_without_origin_still_counts(self):
        """origin-less is ambiguous (claude -p, /goal): never silently drop a
        real turn — only KNOWN machine sources are excluded."""
        self.tx.notification()           # an origin-bearing record elsewhere in the tail
        self.tx.headless_prompt("STAGE=VERIFY check the rollout")
        self.stop()
        self.assertEqual(self.state().get("total_turns"), 1)
        self.assertIn("**User:** STAGE=VERIFY check the rollout", self.log())


class TestCadencesFireOnRealTurns(HookHarness):
    def test_review_waits_for_real_turns(self):
        self.settings(turn_interval=3, min_review_turns=1)
        fired_at = []
        steps = [
            ("P1", lambda: self.tx.human("p1"), False),
            ("notif", self.tx.notification, False),
            ("P2", lambda: self.tx.human("p2"), False),
            ("cont", lambda: self.tx.hook_feedback("[x] go on"), True),
            ("notif", self.tx.notification, False),
            ("P3", lambda: self.tx.human("p3"), False),
        ]
        for label, act, active in steps:
            act()
            if "[gowth-mem:self-review ws=" in _directive(self.stop(stop_hook_active=active)):
                fired_at.append(label)
        self.assertEqual(fired_at, ["P3"],
                         "the review must fire on the 3rd REAL turn (was: the 3rd Stop, at P2)")

    def test_journal_ignores_continuations_and_notifications(self):
        self.settings(journal_every=2)
        self.tx.human("p1")
        self.assertEqual(_directive(self.stop()), "")
        self.tx.hook_feedback("[x] go on")
        self.assertEqual(_directive(self.stop(stop_hook_active=True)), "")
        self.tx.notification()
        self.assertEqual(_directive(self.stop()), "", "a notification relay is not turn 2")
        self.tx.human("p2")
        self.assertIn("[gowth-mem:auto-journal ws=", _directive(self.stop()))


class TestCaptureRecordsTheHumanPrompt(HookHarness):
    def test_user_line_is_the_prompt_not_a_skill_expansion(self):
        """A skill invoked mid-turn writes an isMeta user record AFTER the
        prompt — it was captured as the user's prompt."""
        self.tx.human("fix the flaky parser test", reply="Fixed it")
        self.tx.skill_expansion()
        self.tx.assistant("ran the skill", "Bash")
        self.stop()
        log = self.log()
        self.assertIn("**User:** fix the flaky parser test", log)
        self.assertNotIn("Base directory for this skill", log)
        self.assertIn("Edit(", log, "actions from BEFORE the skill expansion belong to the turn")
        self.assertIn("Bash", log, "actions from after it too")


class TestLegacyTranscriptsKeepPerStopCounting(HookHarness):
    """No record identity (hand-built / pre-origin transcripts, every fixture
    in test_review_trigger.py) → per-Stop counting, as before v4.7.3."""

    def test_no_uuid_counts_every_stop(self):
        self.tx.path.write_text(json.dumps(
            {"type": "user", "message": {"content": "legacy prompt"}}) + "\n")
        for _ in range(3):
            self.stop()
        self.assertEqual(self.state().get("total_turns"), 3)

    def test_no_uuid_still_skips_continuations(self):
        self.tx.path.write_text(json.dumps(
            {"type": "user", "message": {"content": "legacy prompt"}}) + "\n")
        self.stop()
        self.stop(stop_hook_active=True)
        self.assertEqual(self.state().get("total_turns"), 1)

    def test_no_transcript_counts_every_stop(self):
        self.stop(transcript=False)
        self.stop(transcript=False)
        self.assertEqual(self.state().get("total_turns"), 2)


class TestStopTimeFlush(HookHarness):
    """Found by the real-binary E2E: at Stop time the FINAL assistant record is
    not on disk yet, so an 'answered' rule cannot confirm the current prompt
    when it carries no origin. Claude Code's own signals fill the gap."""

    def test_human_prompt_needs_no_flushed_reply(self):
        self.tx.human("rename the module", flushed=False)
        self.stop()
        self.assertEqual(self.state().get("total_turns"), 1)
        self.assertIn("**User:** rename the module", self.log())

    def test_headless_prompt_source_sdk_counts_before_the_reply_lands(self):
        self.tx.headless_prompt("STAGE=VERIFY check it", prompt_source="sdk", flushed=False)
        self.stop(prompt_id=None)
        self.assertEqual(self.state().get("total_turns"), 1)
        self.assertIn("**User:** STAGE=VERIFY check it", self.log())

    def test_origin_less_prompt_confirmed_by_the_stop_prompt_id(self):
        self.tx.headless_prompt("<command-name>/goal</command-name> ship it", flushed=False)
        self.stop()  # prompt_id == that record's promptId, as Claude Code sends it
        self.assertEqual(self.state().get("total_turns"), 1)

    def test_origin_less_unflushed_prompt_without_a_signal_is_not_counted(self):
        self.tx.headless_prompt("<command-name>/model</command-name>", flushed=False)
        self.stop(prompt_id="some-other-prompt")
        self.assertIsNone(self.state().get("total_turns"))

    def test_prompt_source_system_is_machine(self):
        self.tx.human("p1")
        self.stop()
        self.tx.chain_prompt_id = str(uuid.uuid4())
        self.tx._append({"type": "user", "promptId": self.tx.chain_prompt_id,
                         "promptSource": "system",
                         "message": {"role": "user", "content": "background job finished"}})
        self.tx.assistant("relayed")
        self.stop()
        self.assertEqual(self.state().get("total_turns"), 1)


class TestReviewRound2(HookHarness):
    """Adversarial-review round 2 (verified against live transcripts + the binary)."""

    def test_typed_ahead_turn_keeps_the_main_ask(self):
        """92 live turns (13%): the typed-ahead attachment became the only
        User: line and Actions started after it — the original request and
        the work before the follow-up vanished from the judge's log."""
        self.tx.human("read and review the coroot setup", reply="reviewing", tool="Read")
        self.tx.typed_ahead("also check the node group", tool="Bash")
        self.stop()
        self.assertEqual(self.state().get("total_turns"), 1)
        log = self.log()
        self.assertIn("**User:** read and review the coroot setup ⟶ also check the node group", log)
        self.assertIn("Read(read.py)", log, "work done BEFORE the follow-up belongs to the turn")
        self.assertIn("Bash", log)

    def test_typed_ahead_after_heavy_output_keeps_the_main_ask(self):
        """The live shape (2262b532): >512 KB of tool output separates the
        request from the follow-up, so the request is outside the tail."""
        self.tx.human("read and review the coroot setup", reply="reviewing", tool="Read")
        self.tx.huge_tool_output()
        self.tx.typed_ahead("also check the node group", tool="Bash")
        self.stop()
        self.assertEqual(self.state().get("total_turns"), 1)
        self.assertIn("**User:** read and review the coroot setup ⟶ also check the node group",
                      self.log())

    def test_asks_from_a_previous_turn_are_not_repeated_across_heavy_output(self):
        self.tx.human("p1")
        self.stop()
        self.tx.huge_tool_output()
        self.tx.typed_ahead("p2 after a heavy stretch")
        self.stop()
        users = [l for l in self.log().splitlines() if l.startswith("**User:**")]
        self.assertEqual(users, ["**User:** p1", "**User:** p2 after a heavy stretch"])

    def test_asks_from_a_previous_turn_are_not_repeated(self):
        self.tx.human("p1")
        self.stop()
        self.tx.hook_feedback("[gowth-mem:auto-journal ws=default] DELEGATE …")
        self.tx.typed_ahead("p2 during the continuation")
        self.stop(stop_hook_active=True)
        users = [l for l in self.log().splitlines() if l.startswith("**User:**")]
        self.assertEqual(users, ["**User:** p1", "**User:** p2 during the continuation"])

    def test_no_uuid_prompt_under_identity_cannot_loop(self):
        """Latent: a prompt record without uuid/promptId has no key — with the
        stop_hook_active skip gone under identity, every continuation counted
        and re-fired the review (only Claude Code's 8-block cap ended it)."""
        self.settings(turn_interval=1, min_review_turns=1)
        self.tx.path.write_text("\n".join(json.dumps(r) for r in (
            {"type": "system", "subtype": "init", "uuid": "sys-1"},
            {"type": "user", "origin": {"kind": "human"}, "message": {"content": "keyless ask"}},
            {"type": "assistant", "uuid": "a-1", "message": {"content": [{"type": "text", "text": "ok"}]}},
        )) + "\n")
        self.assertIn("[gowth-mem:self-review", _directive(self.stop(prompt_id=None)))
        for _ in range(3):
            self.assertEqual(_directive(self.stop(stop_hook_active=True, prompt_id=None)), "",
                             "a continuation must never re-fire the cadence")

    def test_channel_messages_count_when_answered(self):
        """Claude Code frames channel/slack-ping as non-user, but they relay
        what a person typed elsewhere (a Telegram channel plugin)."""
        self.tx.origin_prompt("channel", "<channel source=\"telegram\">deploy status?</channel>")
        self.stop()
        self.assertEqual(self.state().get("total_turns"), 1)

    def test_machine_kinds_never_count(self):
        for kind in ("peer", "auto-continuation", "coordinator", "plugin", "observer", "unclassified"):
            self.tx.origin_prompt(kind, f"{kind} message")
            self.stop()
        self.assertIsNone(self.state().get("total_turns"), "machine-origin sessions never count")


class TestHumanPromptFinder(unittest.TestCase):
    """Unit level: _capture.find_human_prompt / prompt_key."""

    @classmethod
    def setUpClass(cls):
        cls.cap = _load("_capture")

    def test_origin_mode_skips_non_human_records(self):
        recs = [
            {"type": "user", "uuid": "u1", "origin": {"kind": "human"},
             "message": {"content": "real ask"}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}},
            {"type": "user", "uuid": "u2", "origin": {"kind": "task-notification"},
             "message": {"content": "<task-notification>x</task-notification>"}},
            {"type": "user", "uuid": "u3", "isMeta": True,
             "message": {"content": "Stop hook feedback:\n[x]"}},
            {"type": "user", "uuid": "u4",
             "message": {"content": "Another Claude session sent a message: hi"}},
            {"type": "user", "uuid": "u5", "origin": {"kind": "human"},
             "message": {"content": [{"type": "tool_result", "content": "out"}]}},
        ]
        self.assertEqual(self.cap.find_human_prompt(recs), 0)
        self.assertEqual(self.cap.prompt_key(recs[0]), "u1")

    def test_legacy_mode_skips_meta_and_synthetic_prefixes(self):
        reply = {"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}}
        recs = [
            {"type": "user", "message": {"content": "first"}}, reply,
            {"type": "user", "message": {"content": "second"}}, reply,
            {"type": "user", "isMeta": True, "message": {"content": "Base directory for this skill"}},
            {"type": "user", "message": {"content": "<task-notification>\n<task-id>1</task-id>"}},
            reply,
            {"type": "user", "message": {"content": "Stop hook feedback:\n[x]"}},
            {"type": "user", "message": {"content": "[Request interrupted by user]"}},
        ]
        self.assertEqual(self.cap.find_human_prompt(recs), 2)

    def test_origin_less_record_counts_only_if_answered(self):
        reply = {"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}}
        human = {"type": "user", "uuid": "h", "origin": {"kind": "human"},
                 "message": {"content": "ask"}}
        goal = {"type": "user", "message": {"content": "<command-name>/goal</command-name>"}}
        model = {"type": "user", "message": {"content": "<command-name>/model</command-name>"}}
        notif = {"type": "user", "origin": {"kind": "task-notification"},
                 "message": {"content": "<task-notification>x</task-notification>"}}
        f = self.cap.find_human_prompt
        self.assertEqual(f([human, reply, goal, reply]), 2, "/goal is answered → a real ask")
        self.assertEqual(f([human, reply, model]), 0, "/model is never answered → not an ask")
        self.assertEqual(f([human, reply, model, notif, reply]), 0,
                         "a reply to a LATER machine prompt does not answer /model")

    def test_queued_command_attachments(self):
        typed = {"type": "attachment", "uuid": "q1", "attachment": {
            "type": "queued_command", "prompt": [{"type": "text", "text": "typed ahead"}],
            "commandMode": "prompt", "origin": {"kind": "human"}}}
        qnotif = {"type": "attachment", "uuid": "q2", "attachment": {
            "type": "queued_command", "prompt": "<task-notification/>",
            "commandMode": "task-notification", "origin": {"kind": "task-notification"}}}
        human = {"type": "user", "uuid": "h", "origin": {"kind": "human"},
                 "message": {"content": "ask"}}
        f = self.cap.find_human_prompt
        self.assertEqual(f([human, typed, qnotif]), 1)
        self.assertEqual(self.cap.prompt_text(typed), "typed ahead")
        self.assertEqual(self.cap.prompt_key(typed), "q1")

    def test_none_found(self):
        self.assertEqual(self.cap.find_human_prompt([]), -1)
        self.assertEqual(self.cap.find_human_prompt(
            [{"type": "user", "origin": {"kind": "task-notification"},
              "message": {"content": "<task-notification/>"}}]), -1)

    def test_machine_and_transparent_records_are_never_the_prompt(self):
        human = {"type": "user", "uuid": "h", "origin": {"kind": "human"},
                 "message": {"content": "ask"}}
        notif = {"type": "user", "uuid": "n", "origin": {"kind": "task-notification"},
                 "message": {"content": "<task-notification>x</task-notification>"}}
        future = {"type": "user", "uuid": "c", "origin": {"kind": "cron"},
                  "message": {"content": "scheduled prompt"}}
        compact = {"type": "user", "isCompactSummary": True,
                   "message": {"content": "This session is being continued from a previous "
                                          "conversation that ran out of context."}}
        interrupt = {"type": "user", "message": {"content": "[Request interrupted by user]"}}
        teammate = {"type": "user", "message": {"content": "<teammate-message id=1>hi</teammate-message>"}}
        tool = {"type": "user", "message": {"content": [{"type": "tool_result", "content": "o"}]}}
        meta = {"type": "user", "isMeta": True, "message": {"content": "Stop hook feedback:\n[x]"}}
        headless = {"type": "user", "uuid": "p", "message": {"content": "STAGE=VERIFY go"}}
        # `!cmd` bash mode never invokes the model — its records are not asks
        # (live audit: a relay Stop counted <bash-stdout> as a new turn, 3x).
        bash_in = {"type": "user", "uuid": "b1", "message": {"content": "<bash-input>ls</bash-input>"}}
        bash_out = {"type": "user", "uuid": "b2", "message": {
            "content": "<bash-stdout>a b</bash-stdout><bash-stderr></bash-stderr>"}}
        f = self.cap.find_human_prompt
        self.assertEqual(f([human, notif, future, teammate, compact, interrupt, tool, meta,
                            bash_in, bash_out]), 0,
                         "every non-human origin kind, teammate text, compaction, interrupt, "
                         "tool results, meta records and bash-mode output are skipped")
        reply = {"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}}
        self.assertEqual(f([human, notif, headless, reply]), 2,
                         "answered origin-less prompts (claude -p, /goal) are real asks")
        self.assertEqual(f([notif, compact, tool, meta]), -1)

    def test_scan_back_finds_a_prompt_beyond_the_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "t.jsonl"
            lines = [
                {"type": "user", "uuid": "old", "origin": {"kind": "human"},
                 "message": {"content": "older ask"}},
                {"type": "user", "uuid": "want", "origin": {"kind": "human"},
                 "message": {"content": "the ask"}},
            ]
            big = {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t", "content": "x" * (700 * 1024)}]}}
            notif = {"type": "user", "uuid": "n", "origin": {"kind": "task-notification"},
                     "message": {"content": "<task-notification>x</task-notification>"}}
            p.write_text("\n".join(json.dumps(r) for r in lines + [big, big, notif]) + "\n")
            self.assertEqual(self.cap.find_human_prompt(self.cap.read_transcript_tail(str(p))), -1,
                             "precondition: the prompt is out of the 512 KB tail")
            rec = self.cap.scan_back_for_human(str(p))
            self.assertEqual((rec or {}).get("uuid"), "want")
            self.assertIsNone(self.cap.scan_back_for_human(str(p), max_bytes=1024 * 1024),
                              "the scan is bounded")
            self.assertIsNone(self.cap.scan_back_for_human("/nonexistent/x.jsonl"))

    def test_prompt_key_prefers_uuid_then_prompt_id_never_text(self):
        self.assertEqual(self.cap.prompt_key({"uuid": "a", "promptId": "b"}), "a")
        self.assertEqual(self.cap.prompt_key({"promptId": "b"}), "b")
        self.assertIsNone(self.cap.prompt_key({"message": {"content": "tiếp"}}))
        self.assertIsNone(self.cap.prompt_key({"uuid": "  "}))
        self.assertIsNone(self.cap.prompt_key({"uuid": 42}))
        self.assertIsNone(self.cap.prompt_key(None))

    def test_read_transcript_tail_never_raises(self):
        self.assertEqual(self.cap.read_transcript_tail(""), [])
        self.assertEqual(self.cap.read_transcript_tail("/nonexistent/x.jsonl"), [])
        self.assertEqual(self.cap.read_transcript_tail(42), [])


if __name__ == "__main__":
    unittest.main()
