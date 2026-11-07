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
              flushed: bool = True, prompt_source: str | None = None) -> dict:
        """flushed=False: the Stop-time reality for a tool-less answer — Claude
        Code writes the final assistant record AFTER the Stop hook ran
        (verified with a transcript snapshot taken inside a real Stop hook).
        prompt_source: the 2.1.181+ `promptSource` stamp — `typed` on keyboard
        input AND on a tmux paste (live mention-agent rounds)."""
        self.prompt_id = str(uuid.uuid4())
        self.chain_prompt_id = self.prompt_id
        rec: dict = {"type": "user", "promptId": self.prompt_id,
                     "origin": {"kind": "human"},
                     "message": {"role": "user", "content": text}}
        if prompt_source:
            rec["promptSource"] = prompt_source
        rec = self._append(rec)
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
             prompt_id: str | None = "__chain__", last_message: str | None = None,
             extra: dict | None = None) -> dict:
        payload: dict = {"session_id": self.sid, "hook_event_name": "Stop",
                         "stop_hook_active": stop_hook_active}
        if last_message is not None:
            payload["last_assistant_message"] = last_message
        pid = self.tx.chain_prompt_id if prompt_id == "__chain__" else prompt_id
        if pid:
            payload["prompt_id"] = pid
        if transcript:
            payload["transcript_path"] = str(self.tx.path)
        payload.update(extra or {})
        env = {**os.environ, "GOWTH_MEM_HOME": str(self.home),
               # isolate the backlog scan from the real ~/.claude/projects
               "CLAUDE_CONFIG_DIR": str(self.home / "claude-config")}
        # runner env must not leak in (a GOWTH_WORKSPACE=trade shell broke 8 tests;
        # v4.9: a GOWTH_MEM_OFF bot shell would silence every hook)
        for k in ("AI_AGENT", "GOWTH_WORKSPACE", "CLAUDE_SUBAGENT", "GOWTH_MEM_OFF",
                  "CLAUDE_CODE_ENTRYPOINT"):
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
        self.assertIn("**User:** !kubectl get secret db -o yaml", log)
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
        real turn — only KNOWN machine sources are excluded.

        v4.9: the fixture used to read `STAGE=VERIFY check the rollout` — the
        shape of a bot pipeline's stage prompt (TestPipelinePrompts). A person's
        headless prompt is neutral text."""
        self.tx.notification()           # an origin-bearing record elsewhere in the tail
        self.tx.headless_prompt("verify the canary rollout in staging")
        self.stop()
        self.assertEqual(self.state().get("total_turns"), 1)
        self.assertIn("**User:** verify the canary rollout in staging", self.log())


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
        """`promptSource: sdk` (claude -p) is a person's prompt — the contract
        stands; v4.9 moved the fixture off the pipeline `STAGE=` shape."""
        self.tx.headless_prompt("check the canary rollout", prompt_source="sdk", flushed=False)
        self.stop(prompt_id=None)
        self.assertEqual(self.state().get("total_turns"), 1)
        self.assertIn("**User:** check the canary rollout", self.log())

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


class TestCaptureCompleteness(HookHarness):
    """v4.7.4: 141 of 706 live captured turns (20%) had an EMPTY Claude: line —
    the final answer is written after the Stop hook, so a tool-less turn had no
    visible assistant text. The Stop input carries it as last_assistant_message."""

    def line(self, label: str) -> str:
        for ln in self.log().splitlines():
            if ln.startswith(f"**{label}:**"):
                return ln[len(label) + 5:].strip()
        return ""

    def test_final_answer_fills_the_claude_line(self):
        self.tx.human("what is the capital of France?", flushed=False)
        self.stop(last_message="Paris.")
        self.assertEqual(self.line("Claude"), "Paris.")

    def test_final_answer_follows_earlier_text(self):
        self.tx.human("fix the parser", reply="Let me look at the parser first.", tool="Read")
        self.stop(last_message="Fixed: the tokenizer skipped empty lines.")
        self.assertEqual(self.line("Claude"),
                         "Let me look at the parser first. Fixed: the tokenizer skipped empty lines.")

    def test_final_answer_not_duplicated_once_flushed(self):
        self.tx.human("summarize", reply="Summary: all green.", tool=None)
        self.stop(last_message="Summary: all green.")
        self.assertEqual(self.line("Claude"), "Summary: all green.")

    def test_final_answer_is_sanitized_and_capped(self):
        self.tx.human("show the token", flushed=False)
        self.stop(last_message="token glpat-ABCDEFGHIJKLMNOPQRST ok " + "x" * 400)
        claude = self.line("Claude")
        self.assertLessEqual(len(claude), 300)
        self.assertTrue(claude.startswith("token [REDACTED:gitlab-pat] ok"), claude)

    def test_secret_straddling_the_cap_is_redacted_not_truncated(self):
        """Review v4.7.4: capping BEFORE sanitizing cut a token at char 300 into
        a fragment the sanitizer no longer recognises ('glpat-ABC')."""
        self.tx.human("dump it", flushed=False)
        self.stop(last_message="a" * 290 + " glpat-ABCDEFGHIJKLMNOPQRST tail")
        self.assertNotIn("glpat", self.line("Claude"))



    def test_prompt_secret_straddling_the_cap_is_redacted(self):
        self.tx.human("x" * 1990 + " glpat-ABCDEFGHIJKLMNOPQRST and more")
        self.stop()
        self.assertNotIn("glpat", self.line("User"))

    def test_tool_argument_secret_is_redacted_not_truncated(self):
        """Review round 2 LOW: _tool_arg cut a Bash command at 60 chars
        BEFORE sanitizing — a token straddling it reached the synced log."""
        self.tx.human("deploy", flushed=False)
        self.tx._append({"type": "assistant", "message": {"content": [{
            "type": "tool_use", "name": "Bash",
            # 47 chars of prefix: the 60-char cut lands INSIDE the token
            "input": {"command": "export TOKEN_VALUE_FOR_THE_DEPLOY_PIPELINE_NOW=glpat-ABCDEFGHIJKLMNOPQRST && run"}}]}})
        self.stop()
        self.assertNotIn("glpat", self.line("Actions"))

    def test_megabyte_paste_does_not_stall_the_stop(self):
        """Review round 2 MEDIUM: uncapped fields reached _privacy regexes that
        are super-linear on adversarial input (200 KB of 'a.a.a' took 72 s)."""
        import time
        # 200 KB each: inside the 512 KB tail (a bigger line is dropped unparsed
        # and would make this test vacuous) — ~72 s per field before the fix.
        self.tx.human("a." * 100_000, flushed=False)
        t = time.perf_counter()
        self.stop(last_message="b." * 100_000)
        self.assertLess(time.perf_counter() - t, 5.0)
        self.assertEqual(self.state().get("total_turns"), 1)
        self.assertTrue(self.line("User").startswith("a.a.a"), "the prompt WAS parsed and captured")

    def test_final_answer_survives_long_narration(self):
        """Review v4.7.4: with >300 chars of narration the appended final
        answer was still cut away by the head cap."""
        self.tx.human("fix the parser", reply="Let me investigate. " * 25, tool="Read")
        self.stop(last_message="Fixed: the tokenizer skipped empty lines.")
        claude = self.line("Claude")
        self.assertLessEqual(len(claude), 300)
        self.assertTrue(claude.startswith("Let me investigate."), claude)
        self.assertIn("Fixed: the tokenizer skipped empty lines.", claude)

    def test_slash_command_rendered_readably(self):
        self.tx.headless_prompt("<command-name>/goal</command-name>\n            "
                                "<command-message>goal</command-message>\n            "
                                "<command-args>ship the release</command-args>")
        self.stop()
        self.assertEqual(self.line("User"), "/goal ship the release")

    def test_legacy_big_transcript_does_not_join_the_whole_file(self):
        """Round-3 LOW: > 512 KB, no uuids, no summaries (hand-built only) —
        the beyond-tail ask scan joined every ask and repeated the prompt."""
        big = {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t", "content": "x" * (600 * 1024)}]}}
        reply = {"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}}
        self.tx.path.write_text("\n".join(json.dumps(r) for r in (
            {"type": "user", "origin": {"kind": "human"}, "message": {"content": "legacy first ask"}},
            reply, big,
            {"type": "user", "origin": {"kind": "human"}, "message": {"content": "legacy second ask"}},
            reply,
        )) + "\n")
        self.stop()
        self.assertEqual(self.line("User"), "legacy second ask")


# v4.9 — real pipeline prompt shapes (mention-agent stages and a session
# summarizer, read from live transcripts; names and chat lines anonymised).
PASTED_TASK = ("### TASK 469044d829 R13 ###\nSTAGE=EXECUTE\nOperator APPROVED. Execute the PLAN from the "
               "VERIFY stage (same session) — plus the operator's\nextra instructions if present "
               "(extra overrides PLAN).\n\n- SKILL-FIRST: load the matching skill")
SDK_VERIFY = ("STAGE=VERIFY\nVerify the mention below BEFORE the operator decides anything. READ-ONLY.\n\n"
              "1. Who is asking, which service does it belong to (team-map §3 ownership)?")
SDK_REVERIFY = ("STAGE=VERIFY\nThe requester answered the clarifying question:\n[05/10 11:48] someone: prod\n\n"
                "Re-verify with the new information. Output BRIEF/EVIDENCE/PLAN/RISK/VERDICT.")
REFLECT = "STAGE=REFLECT — task done. GOAL: join every source → extract lessons → push gowth-mem."
TEST_STAGE = "STAGE=TEST (operator check, not a mention). Without any tool, from startup context only: quote N1"
DAILY = ("### DAILY KNOWLEDGE REFRESH + SELF-IMPROVE (incremental · token-lean) ###\n"
         "STAGE=SELF-IMPROVE. Window: FROM 2026-10-26T03:25:49Z TO NOW.")
SUMMARIZER = ("Below is a conversation log from a Claude Code coding session.\nCreate a summary to help the "
              "next session quickly understand the context.\n\n## Prioritize including\n- Design decisions")


def _wrapped(body: str, pid: str = "a75c") -> str:
    """Review hooks#1: how Claude Code (2.1.278–2.1.301 transcripts, behind a
    server-side flag) records a BRACKETED paste — tmux `paste-buffer -p`, the
    way mention-agent sends its EXECUTE/REFLECT rounds (198 live rounds,
    Sep–Nov 2026, origin.kind human, promptSource typed): two newlines, the
    opener + newline, the body, a newline and the closer; the id is exactly 4
    lowercase hex chars (the 2.1.301 binary's own id check)."""
    return f'\n\n<pasted_content id="{pid}">\n{body}\n</pasted_content id="{pid}">\n'


WRAPPED_TASK = _wrapped(PASTED_TASK, "a75c")      # the EXECUTE round: `### TASK … ###` + STAGE=EXECUTE
WRAPPED_REFLECT = _wrapped(REFLECT, "1c90")
WRAPPED_EXECUTE = _wrapped("STAGE=EXECUTE\nOperator APPROVED. Execute the PLAN from the VERIFY stage.", "2d41")
WRAPPED_SUMMARIZER = _wrapped(SUMMARIZER, "09ef")
WRAPPED_SHAPES = (WRAPPED_TASK, WRAPPED_REFLECT, WRAPPED_EXECUTE, WRAPPED_SUMMARIZER)
PIPELINE_SHAPES = (PASTED_TASK, SDK_VERIFY, SDK_REVERIFY, REFLECT, TEST_STAGE, DAILY, SUMMARIZER) + WRAPPED_SHAPES
HUMAN_LOOKALIKES = (
    "STAGE=PROD make deploy for the api service",          # an env assignment typed by a person
    "STAGE=prod\nrun the deploy",                            # lowercase name
    "stage=VERIFY\nverify the mention",                      # lowercase key
    "please look at why the bot sent this:\nSTAGE=VERIFY\nVerify the mention below",  # mid-text quote
    "### Task: fix the login ###\nthen deploy",              # not TASK
    "### TASKS for today ###\n- a",                          # TASK must be a whole word
    "### TASK 1 ### and more words on the same line",        # the marker line must end there
    "STAGE=V\nname too short",
    "STAGE=" + "A" * 40 + "\nname too long",
    "Below is a conversation log",                           # not the whole prefix
    "below is a conversation log from a Claude Code coding session",
    "x" * 600 + PASTED_TASK,                                 # never searched past the start
)
# Review hooks#1 — still human after the unwrap: every lookalike pasted, a
# person's ordinary paste, words typed BEFORE a pasted round (a person quoting
# it), ids Claude Code never writes, a second opener nested in the first, and a
# marker that does not start the pasted body.
JSON_LOG_LINE = ('{"level":"error","ts":"2026-11-07T09:14:03Z","msg":"STAGE=VERIFY step failed",'
                 '"pod":"api-7f9c","detail":"### TASK 1 ### missing"}')
WRAPPED_HUMAN = tuple(_wrapped(t, "b8c1") for t in HUMAN_LOOKALIKES) + (
    _wrapped(JSON_LOG_LINE, "e3a0"),
    "please look at why the bot sent this round:\n" + WRAPPED_TASK,
    "check this" + WRAPPED_REFLECT,
    _wrapped(PASTED_TASK, "zzzz"),                           # not hex
    _wrapped(PASTED_TASK, "A75C"),                           # Claude Code writes lowercase hex only
    _wrapped(PASTED_TASK, "a75c1"),                          # 5 chars
    _wrapped(PASTED_TASK, "a75"),                            # 3 chars
    '<pasted_content id="a75c" >\n' + PASTED_TASK,           # not the exact opener
    "<pasted_content>\n" + PASTED_TASK,
    _wrapped(WRAPPED_TASK, "b8c1"),                          # only ONE leading opener is skipped
    '<pasted_content id="a75c">' + "x" * 600 + "\n" + PASTED_TASK,   # marker 600 chars into the body
    _wrapped("x" * 600 + "\n" + REFLECT, "c4d2"),
)


def _collapsed(text: str) -> str:
    """What a session log stores: _capture._oneline."""
    import re as _re
    return _re.sub(r"\s+", " ", text).strip()


class TestPipelinePrompts(HookHarness):
    """v4.9 §1.2: bot/pipeline-marked prompts are machine records — never a
    turn, never captured, never answered with a cadence directive — through
    the real Stop hook. 16/86 devops session logs opened with
    `**User:** STAGE=VERIFY …` before this."""

    def test_pasted_task_prompt_is_not_counted_logged_or_nagged(self):
        """A tmux paste carries origin.kind=human — the marker must win."""
        self.settings(journal_every=1, turn_interval=1, min_review_turns=1)
        self.tx.human(PASTED_TASK, reply="executing the plan", tool="Bash")
        d = self.stop()
        self.assertEqual(_directive(d), "", "no cadence directive may interrupt a bot round")
        self.assertIsNone(self.state().get("total_turns"))
        self.assertEqual(self.log(), "", "a pipeline round is never captured")

    def test_sdk_stage_prompts_are_not_counted(self):
        for text in (SDK_VERIFY, SDK_REVERIFY, REFLECT, TEST_STAGE, SUMMARIZER):
            self.tx.headless_prompt(text, prompt_source="sdk", flushed=False)
            self.stop(prompt_id=None)
        self.assertIsNone(self.state().get("total_turns"))
        self.assertEqual(self.log(), "")

    def test_daily_pipeline_prompt_is_not_counted(self):
        self.tx.human(DAILY, reply="refreshing knowledge", tool="Read")
        self.stop()
        self.assertIsNone(self.state().get("total_turns"))

    def test_human_lookalikes_still_count(self):
        """`STAGE=PROD make deploy …` and quoted markers are a person's words."""
        for i, text in enumerate(HUMAN_LOOKALIKES[:7], start=1):
            self.tx.human(text)
            self.stop()
            self.assertEqual(self.state().get("total_turns"), i, text)
        log = self.log()
        self.assertIn("**User:** STAGE=PROD make deploy for the api service", log)
        self.assertIn("**User:** please look at why the bot sent this: STAGE=VERIFY Verify the mention below", log)

    def test_human_turns_around_pipeline_turns_count(self):
        self.tx.human("rotate the staging certificate")
        self.stop()
        self.tx.headless_prompt(SDK_VERIFY, prompt_source="sdk")
        self.stop()
        self.tx.human(PASTED_TASK, reply="executing", tool="Bash")
        self.stop()
        self.tx.human("now check the ingress logs")
        self.stop()
        self.assertEqual(self.state().get("total_turns"), 2)
        users = [l for l in self.log().splitlines() if l.startswith("**User:**")]
        self.assertEqual(users, ["**User:** rotate the staging certificate",
                                 "**User:** now check the ingress logs"])
        self.assertNotIn("STAGE=", self.log())
        self.assertNotIn("### TASK", self.log())

    def test_typed_ahead_pipeline_prompt_is_not_an_ask(self):
        self.tx.human("summarize the incident")
        self.tx.typed_ahead(PASTED_TASK)
        self.stop()
        self.assertEqual(self.state().get("total_turns"), 1)
        users = [l for l in self.log().splitlines() if l.startswith("**User:**")]
        self.assertEqual(users, ["**User:** summarize the incident"], "the pasted round never joins the ask")

    def test_settings_prefixes_are_honoured_and_junk_entries_ignored(self):
        settings = json.loads((self.home / "settings.json").read_text())
        settings["capture"] = {"pipeline_prefixes": ["[nightly-bot]", 42, None, "", "y" * 201, {"a": 1}]}
        (self.home / "settings.json").write_text(json.dumps(settings))
        self.tx.human("[nightly-bot] digest the alerts of the last 24h")
        self.stop()
        self.assertIsNone(self.state().get("total_turns"), "a configured prefix is a pipeline marker")
        self.tx.human("[nightly-botany] a person's prompt that merely shares letters")
        self.stop()
        self.tx.human("yyyy is not a prefix: the 201-char entry is ignored")
        self.stop()
        self.assertEqual(self.state().get("total_turns"), 2, "'' must not match everything; junk is ignored")

    def test_pipeline_stops_leave_the_log_byte_identical(self):
        """4-test rule (repeated write): after a human turn is logged, three
        Stops ending pipeline rounds leave the session log byte-identical."""
        self.tx.human("rename the parser module")
        self.stop()
        first = self.log()
        self.assertIn("**User:** rename the parser module", first)
        for text in (SDK_VERIFY, PASTED_TASK, SUMMARIZER):
            if text is PASTED_TASK:
                self.tx.human(text, reply="executing", tool="Bash")
            else:
                self.tx.headless_prompt(text, prompt_source="sdk")
            self.stop()
            self.assertEqual(self.log(), first)
        self.assertEqual(self.state().get("total_turns"), 1)

    def test_huge_pasted_pipeline_prompt_is_fast(self):
        """Growth through the real entry point: only 512 chars are examined."""
        import time
        self.tx.human(PASTED_TASK + "\n" + "z" * 200_000, flushed=False)
        t = time.perf_counter()
        self.stop()
        self.assertLess(time.perf_counter() - t, 5.0)
        self.assertIsNone(self.state().get("total_turns"))

    # ─── review hooks#1: Claude Code wraps a bracketed (tmux) paste ─────────

    def test_wrapped_rounds_are_not_counted_logged_or_nagged(self):
        """The live shape of every mention-agent round since October 2026 — a
        tmux paste recorded as `<pasted_content id=…>` + round, origin.kind
        human, promptSource typed. Before the fix each one was a turn, landed
        in the synced session log and fired both cadences at interval 1."""
        self.settings(journal_every=1, turn_interval=1, min_review_turns=1)
        for text in WRAPPED_SHAPES:
            self.tx.human(text, reply="executing the plan", tool="Bash", prompt_source="typed")
            d = self.stop()
            self.assertEqual(_directive(d), "", "no cadence directive may interrupt a bot round: " + text[:40])
            self.assertIsNone(self.state().get("total_turns"), text[:40])
            self.assertEqual(self.log(), "", "a wrapped round is never captured")
        # positive control: the session's next HUMAN turn counts, is logged and
        # fires the cadences — the silence above is not the harness
        self.tx.human("rotate the staging certificate", prompt_source="typed")
        d = self.stop()
        self.assertEqual(self.state().get("total_turns"), 1)
        self.assertNotEqual(_directive(d), "", "cadences at 1 fire on the first real turn")
        users = [l for l in self.log().splitlines() if l.startswith("**User:**")]
        self.assertEqual(users, ["**User:** rotate the staging certificate"])
        self.assertNotIn("pasted_content", self.log())

    def test_wrapped_round_typed_ahead_is_not_an_ask(self):
        """A round pasted while the model works is a queued_command attachment
        (origin.kind human, commandMode prompt), wrapped the same way."""
        self.settings(journal_every=1, turn_interval=1, min_review_turns=1)
        # it follows a bare TASK round (machine without the unwrap), so only the
        # attachment decides this Stop
        self.tx.human(PASTED_TASK, reply="executing", tool="Bash", prompt_source="typed")
        self.tx.typed_ahead(WRAPPED_REFLECT, reply="reflecting", tool="Read")
        d = self.stop()
        self.assertEqual(_directive(d), "")
        self.assertIsNone(self.state().get("total_turns"))
        self.assertEqual(self.log(), "")
        # a person's ask, then a wrapped round typed ahead: one turn, and the
        # round never joins the User: line
        self.tx.human("summarize the incident", prompt_source="typed")
        self.tx.typed_ahead(WRAPPED_TASK)
        d = self.stop()
        self.assertEqual(self.state().get("total_turns"), 1)
        self.assertNotEqual(_directive(d), "", "positive control: the person's turn fires the cadences")
        users = [l for l in self.log().splitlines() if l.startswith("**User:**")]
        self.assertEqual(users, ["**User:** summarize the incident"])
        for marker in ("pasted_content", "### TASK", "STAGE="):
            self.assertNotIn(marker, self.log())

    def test_wrapped_rounds_leave_the_log_byte_identical(self):
        """4-test rule (repeated write): after a human turn is logged, three
        Stops ending wrapped rounds — a typed record, a queued attachment and
        another typed record — leave the session log byte-identical."""
        self.tx.human("rename the parser module", prompt_source="typed")
        self.stop()
        first = self.log()
        self.assertIn("**User:** rename the parser module", first)
        rounds = ((WRAPPED_TASK, "record"), (WRAPPED_REFLECT, "queued"), (WRAPPED_EXECUTE, "record"))
        for text, how in rounds:
            if how == "queued":
                self.tx.typed_ahead(text, reply="reflecting", tool="Read")
            else:
                self.tx.human(text, reply="executing", tool="Bash", prompt_source="typed")
            self.stop()
            self.assertEqual(self.log(), first, f"{how}: {text[:40]!r}")
        self.assertEqual(self.state().get("total_turns"), 1)

    def test_wrapped_human_pastes_still_count(self):
        """HEAD's human cases replayed through the new unwrap: a pasted
        lookalike, a person's ordinary paste, words typed BEFORE a pasted
        round, an id Claude Code never writes, a second nested opener, a marker
        that does not start the pasted body — all turns, all logged."""
        cases = (_wrapped(HUMAN_LOOKALIKES[0], "b8c1"),            # `STAGE=PROD make deploy …`
                 _wrapped(JSON_LOG_LINE, "e3a0"),
                 "please look at why the bot sent this round:\n" + WRAPPED_TASK,
                 "is this safe?" + WRAPPED_REFLECT,                 # the opener within the first 64 chars
                 _wrapped(PASTED_TASK, "zzzz"),
                 _wrapped(WRAPPED_TASK, "b8c1"),
                 _wrapped("x" * 600 + "\n" + REFLECT, "c4d2"))
        for i, text in enumerate(cases, start=1):
            self.tx.human(text, prompt_source="typed")
            self.stop()
            self.assertEqual(self.state().get("total_turns"), i, text[:60])
        log = self.log()
        users = [l for l in log.splitlines() if l.startswith("**User:**")]
        self.assertEqual(len(users), len(cases), log)
        self.assertIn('**User:** <pasted_content id="e3a0"> {"level":"error"', log)
        self.assertIn("**User:** please look at why the bot sent this round: <pasted_content", log)
        self.assertIn('**User:** is this safe? <pasted_content id="1c90"> STAGE=REFLECT', log)

    def test_huge_wrapped_pipeline_prompt_is_fast(self):
        """Growth through the real entry point, wrapped: a 200 KB pasted round,
        and 200 KB of whitespace between the opener and the marker."""
        import time
        for text in (_wrapped(PASTED_TASK + "\n" + "z" * 200_000),
                     '<pasted_content id="a75c">' + " \n" * 100_000 + REFLECT):
            self.tx.human(text, flushed=False, prompt_source="typed")
            t = time.perf_counter()
            self.stop()
            self.assertLess(time.perf_counter() - t, 5.0)
            self.assertIsNone(self.state().get("total_turns"), text[:40])
        self.assertEqual(self.log(), "")


class TestPipelineMarkers(unittest.TestCase):
    """Unit level: _capture.is_pipeline_prompt / pipeline_prefixes /
    _classify_record (v4.9 §1.2)."""

    @classmethod
    def setUpClass(cls):
        cls.cap = _load("_capture")

    def test_real_pipeline_shapes(self):
        for text in PIPELINE_SHAPES:
            self.assertTrue(self.cap.is_pipeline_prompt(text), text[:40])
            self.assertTrue(self.cap.is_pipeline_prompt(text, logged=True), text[:40])
            # … and as a session log stores it (whitespace collapsed) — the
            # wrapped rounds included (review hooks#1)
            self.assertTrue(self.cap.is_pipeline_prompt(_collapsed(text), logged=True), _collapsed(text)[:40])
        self.assertTrue(self.cap.is_pipeline_prompt("\n\n   " + SDK_VERIFY), "leading whitespace is skipped")
        self.assertTrue(self.cap.is_pipeline_prompt(" " * 600 + "STAGE=VERIFY\nx"),
                        "the 512-char window starts after the leading whitespace")
        self.assertTrue(self.cap.is_pipeline_prompt("STAGE=VERIFY"), "the end of the prompt ends the name")
        self.assertTrue(self.cap.is_pipeline_prompt("STAGE=SELF-IMPROVE.\nwindow"))
        self.assertTrue(self.cap.is_pipeline_prompt("STAGE=EXECUTE\t– round 2"))

    def test_human_lookalikes(self):
        for text in HUMAN_LOOKALIKES:
            self.assertFalse(self.cap.is_pipeline_prompt(text), text[:60])
        for junk in (None, 42, b"STAGE=VERIFY\n", "", "   \n"):
            self.assertFalse(self.cap.is_pipeline_prompt(junk), repr(junk))

    def test_logged_mode_reads_collapsed_session_log_lines(self):
        """Session logs collapse whitespace: the newline after a marker became a
        space. logged=True (curator only) accepts that; turn counting never
        uses it."""
        for text in (SDK_VERIFY, SDK_REVERIFY, PASTED_TASK, DAILY):
            line = _collapsed(text)
            self.assertTrue(self.cap.is_pipeline_prompt(line, logged=True), line[:50])
            self.assertFalse(self.cap.is_pipeline_prompt(line), "normal mode needs the real newline: " + line[:50])
        for text in (REFLECT, TEST_STAGE, SUMMARIZER):
            self.assertTrue(self.cap.is_pipeline_prompt(_collapsed(text), logged=True))
        for text in ("### Task: fix the login ### then deploy", "please run STAGE=VERIFY now",
                     "STAGE=prod run the deploy", "STAGE=V name too short"):
            self.assertFalse(self.cap.is_pipeline_prompt(text, logged=True), text)
        # the documented ambiguity: a logged `STAGE=PROD make deploy` reads as a marker
        self.assertTrue(self.cap.is_pipeline_prompt("STAGE=PROD make deploy", logged=True))
        self.assertFalse(self.cap.is_pipeline_prompt("STAGE=PROD make deploy"))

    def test_settings_prefixes(self):
        s = {"capture": {"pipeline_prefixes": ["[bot]", "### JOB\nmode=nightly"]}}
        self.assertTrue(self.cap.is_pipeline_prompt("  [bot] summarize", s))
        self.assertFalse(self.cap.is_pipeline_prompt("[bot] summarize"), "no settings → built-ins only")
        self.assertFalse(self.cap.is_pipeline_prompt("a [bot] mid-text", s))
        self.assertTrue(self.cap.is_pipeline_prompt("### JOB\nmode=nightly digest", s))
        self.assertTrue(self.cap.is_pipeline_prompt("### JOB mode=nightly digest", s, logged=True),
                        "logged mode matches the whitespace-collapsed prefix")
        self.assertFalse(self.cap.is_pipeline_prompt("### JOB mode=nightly digest", s))

    def test_pipeline_prefixes_parsing(self):
        pp = self.cap.pipeline_prefixes
        self.assertEqual(pp(None), ())
        self.assertEqual(pp({}), ())
        self.assertEqual(pp({"capture": {"pipeline_prefixes": "[bot]"}}), (), "a string is not a list")
        self.assertEqual(pp({"capture": "x"}), ())
        self.assertEqual(pp({"capture": {"pipeline_prefixes": ["[a]", 1, None, "", "z" * 201, "z" * 200]}}),
                         ("[a]", "z" * 200))
        many = [f"[p{i}]" for i in range(30)]
        self.assertEqual(pp({"capture": {"pipeline_prefixes": many}}), tuple(many[:20]))

    def test_classification_puts_the_marker_before_the_origin(self):
        c = self.cap._classify_record
        pasted = {"type": "user", "uuid": "u", "origin": {"kind": "human"}, "promptSource": "typed",
                  "message": {"content": PASTED_TASK}}
        sdk = {"type": "user", "uuid": "s", "promptSource": "sdk", "message": {"content": SDK_VERIFY}}
        turn_origin = {"type": "user", "uuid": "t", "turnOrigin": "human",
                       "message": {"content": [{"type": "text", "text": DAILY}]}}
        queued = {"type": "attachment", "uuid": "q", "attachment": {
            "type": "queued_command", "prompt": PASTED_TASK, "commandMode": "prompt",
            "origin": {"kind": "human"}}}
        for rec in (pasted, sdk, turn_origin, queued):
            self.assertEqual(c(rec), "machine", rec.get("uuid"))
        human = {"type": "user", "uuid": "h", "origin": {"kind": "human"},
                 "message": {"content": "STAGE=PROD make deploy for the api"}}
        self.assertEqual(c(human), "human")
        bot = {"type": "user", "uuid": "b", "origin": {"kind": "human"}, "message": {"content": "[bot] run"}}
        self.assertEqual(c(bot), "human")
        self.assertEqual(c(bot, {"capture": {"pipeline_prefixes": ["[bot]"]}}), "machine")
        # find_human_prompt skips a pipeline record like any machine record
        reply = {"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}}
        recs = [human, reply, pasted, reply, sdk, reply]
        self.assertEqual(self.cap.find_human_prompt(recs), 0)
        self.assertEqual(self.cap.find_human_prompt([bot, reply], settings={"capture": {
            "pipeline_prefixes": ["[bot]"]}}), -1)

    def test_growth_is_flat(self):
        """4-test rule: every new marker regex on 10k/20k/40k-char adversarial
        inputs, both modes. Only 512 chars are examined, so time stays flat."""
        import time
        shapes = {
            "stage-name-flood": lambda n: "STAGE=" + "A" * n,
            "stage-token-flood": lambda n: ("STAGE=" * (n // 6 + 1))[:n],
            "task-unclosed-hashes": lambda n: "### TASK " + "#" * n,
            "daily-whitespace-run": lambda n: "### DAILY" + " " * n + "###",
            "task-repeated-separators": lambda n: ("### TASK ###" * (n // 12 + 1))[:n],
            "blanks-then-dash": lambda n: "STAGE=AB" + " " * n + "-",
            "leading-whitespace-then-marker": lambda n: " " * n + "STAGE=VERIFY\nx",
        }
        settings = {"capture": {"pipeline_prefixes": ["[bot]" + " " * 150, "### JOB\n" * 20]}}
        for logged in (False, True):
            for name, make in shapes.items():
                times, results = [], []
                for size in (10_000, 20_000, 40_000):
                    text = make(size)
                    best = None
                    for _ in range(5):
                        t0 = time.perf_counter()
                        r = self.cap.is_pipeline_prompt(text, settings, logged=logged)
                        dt = time.perf_counter() - t0
                        best = dt if best is None else min(best, dt)
                    times.append(best)
                    results.append(r)
                self.assertLess(times[2], 0.01, (name, logged, times))
                self.assertLessEqual(times[2], 2.5 * times[0] + 0.002, (name, logged, times))
                self.assertEqual(len(set(results)), 1, f"{name}: the verdict must not depend on the size")
                if name == "leading-whitespace-then-marker":
                    self.assertEqual(results, [True] * 3, "positive signal: the marker was really parsed")
                if name in ("stage-name-flood", "task-unclosed-hashes"):
                    self.assertEqual(results, [False] * 3, name)
                if name == "blanks-then-dash":
                    # the dash lies past the 512-char window; a logged line
                    # accepts the space right after the name
                    self.assertEqual(results, [logged] * 3, (name, logged))
        # and through the classifier, on a record (prompt_text + marker)
        rec = {"type": "user", "uuid": "x", "origin": {"kind": "human"},
               "message": {"content": PASTED_TASK + "q" * 40_000}}
        t0 = time.perf_counter()
        self.assertEqual(self.cap._classify_record(rec), "machine")
        self.assertLess(time.perf_counter() - t0, 0.05)

    # ─── review hooks#1: one leading `<pasted_content id="xxxx">` opener ───

    def test_wrapped_rounds_unwrap_one_paste_opener(self):
        for text in WRAPPED_SHAPES:
            self.assertTrue(text.startswith('\n\n<pasted_content id="'), "the fixture is really wrapped")
            self.assertTrue(self.cap.is_pipeline_prompt(text), text[:50])
            self.assertTrue(self.cap.is_pipeline_prompt(text, logged=True), text[:50])
        op = '<pasted_content id="a75c">'
        self.assertTrue(self.cap.is_pipeline_prompt(op + "### TASK 1 ###\nx"), "no newline after the opener")
        self.assertTrue(self.cap.is_pipeline_prompt(" \t\n" + op + " \n" * 400 + "STAGE=VERIFY\nx"),
                        "the 512-char window starts after the whitespace behind the opener too")
        for pid in ("0000", "ffff", "9a0b"):
            self.assertTrue(self.cap.is_pipeline_prompt(_wrapped(SDK_VERIFY, pid)), pid)
        for text in (op, op + "  \n ", "\n\n" + op + "\n", "\n\n" + op + "\n</pasted_content id=\"a75c\">\n"):
            self.assertFalse(self.cap.is_pipeline_prompt(text), repr(text))
            self.assertFalse(self.cap.is_pipeline_prompt(text, logged=True), repr(text))

    def test_wrapped_human_lookalikes(self):
        """HEAD's refusal cases (HUMAN_LOOKALIKES) and the paste-specific ones
        stay human through the unwrap."""
        self.assertGreater(len(WRAPPED_HUMAN), len(HUMAN_LOOKALIKES))
        for text in WRAPPED_HUMAN:
            self.assertFalse(self.cap.is_pipeline_prompt(text), text[:80])

    def test_wrapped_logged_lines(self):
        """A session log stores a wrapped round whitespace-collapsed (the live
        pre-fix logs: `**User:** <pasted_content id="a75c"> ### TASK … ###
        STAGE=EXECUTE …`): logged=True reads it, normal mode still needs the
        real newline after a `### TASK` line or a bare STAGE name."""
        for text in WRAPPED_SHAPES:
            line = _collapsed(text)
            self.assertTrue(line.startswith('<pasted_content id="') and "\n" not in line, line[:40])
            self.assertTrue(self.cap.is_pipeline_prompt(line, logged=True), line[:60])
        for text in (WRAPPED_TASK, WRAPPED_EXECUTE):
            self.assertFalse(self.cap.is_pipeline_prompt(_collapsed(text)), _collapsed(text)[:60])
        for text in (WRAPPED_REFLECT, WRAPPED_SUMMARIZER):
            self.assertTrue(self.cap.is_pipeline_prompt(_collapsed(text)), "a dash / the summarizer survive collapsing")
        for text in ("please look at the bot round: " + _collapsed(WRAPPED_TASK),
                     _collapsed(_wrapped(PASTED_TASK, "zzzz")),
                     _collapsed(_wrapped(WRAPPED_TASK, "b8c1")),
                     _collapsed(_wrapped(JSON_LOG_LINE, "e3a0"))):
            self.assertFalse(self.cap.is_pipeline_prompt(text, logged=True), text[:60])

    def test_settings_prefixes_see_the_unwrapped_head(self):
        s = {"capture": {"pipeline_prefixes": ["[bot]", "### JOB\nmode=nightly"]}}
        self.assertTrue(self.cap.is_pipeline_prompt(_wrapped("[bot] summarize the alerts"), s))
        self.assertFalse(self.cap.is_pipeline_prompt(_wrapped("[bot] summarize the alerts")),
                         "no settings → built-ins only")
        self.assertTrue(self.cap.is_pipeline_prompt(_wrapped("### JOB\nmode=nightly digest"), s))
        line = _collapsed(_wrapped("### JOB\nmode=nightly digest"))
        self.assertTrue(self.cap.is_pipeline_prompt(line, s, logged=True))
        self.assertFalse(self.cap.is_pipeline_prompt(line, s))
        self.assertFalse(self.cap.is_pipeline_prompt("ok " + _wrapped("[bot] x"), s), "typed words first")
        self.assertFalse(self.cap.is_pipeline_prompt(_wrapped(_wrapped("[bot] x"), "b8c1"), s), "one opener only")

    def test_wrapped_records_classify_as_machine(self):
        c = self.cap._classify_record
        typed = {"type": "user", "uuid": "w1", "origin": {"kind": "human"}, "promptSource": "typed",
                 "message": {"content": WRAPPED_TASK}}
        parts = {"type": "user", "uuid": "w2", "origin": {"kind": "human"}, "promptSource": "typed",
                 "message": {"content": [{"type": "text", "text": WRAPPED_REFLECT}]}}
        queued = {"type": "attachment", "uuid": "w3", "attachment": {
            "type": "queued_command", "prompt": WRAPPED_EXECUTE, "commandMode": "prompt",
            "origin": {"kind": "human"}}}
        for rec in (typed, parts, queued):
            self.assertEqual(c(rec), "machine", rec["uuid"])
        person = {"type": "user", "uuid": "p", "origin": {"kind": "human"}, "promptSource": "typed",
                  "message": {"content": _wrapped(JSON_LOG_LINE, "e3a0")}}
        quoting = {"type": "attachment", "uuid": "q", "attachment": {
            "type": "queued_command", "prompt": "is this round safe?\n" + WRAPPED_TASK,
            "commandMode": "prompt", "origin": {"kind": "human"}}}
        self.assertEqual(c(person), "human")
        self.assertEqual(c(quoting), "human")
        reply = {"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}}
        recs = [person, reply, typed, reply, queued, reply, parts, reply]
        self.assertEqual(self.cap.find_human_prompt(recs), 0, "the wrapped rounds are skipped")

    def test_paste_opener_growth_is_flat(self):
        """4-test rule for _PASTE_OPEN_RE: 10k/20k/40k-char adversarial
        pastes, both modes, with settings prefixes in play. The opener match is
        anchored, fixed-length and bounded to 64 chars; the whitespace skip is
        one linear lstrip()."""
        import time
        op = '<pasted_content id="a75c">'
        shapes = {  # name: (make, (normal, logged) verdict)
            "whitespace-run-after-opener": (lambda n: op + " " * n + "### TASK 1 ###\nx", (True, True)),
            "newline-runs-around-opener": (lambda n: "\n" * (n // 2) + op + "\n" * (n // 2) + REFLECT,
                                           (True, True)),
            "repeated-openers": (lambda n: (op * (n // len(op) + 1))[:n], (False, False)),
            "repeated-openers-then-task": (lambda n: op * (n // len(op) + 1) + "### TASK 1 ###\nx", (False, False)),
            "unclosed-opener-flood": (lambda n: ('<pasted_content id="' * (n // 20 + 1))[:n], (False, False)),
            "id-flood": (lambda n: '<pasted_content id="' + "a" * n + '">\n### TASK 1 ###\n', (False, False)),
            "opener-then-unclosed-task-flood": (lambda n: op + "\n" + ("### TASK " * (n // 9 + 1))[:n],
                                                (False, True)),
            "opener-then-task-line-flood": (lambda n: op + "\n" + ("### TASK 1 ###\n" * (n // 15 + 1))[:n],
                                            (True, True)),
        }
        settings = {"capture": {"pipeline_prefixes": ["[bot]" + " " * 150, "### JOB\n" * 20]}}
        for logged in (False, True):
            for name, (make, verdict) in shapes.items():
                times, results = [], []
                for size in (10_000, 20_000, 40_000):
                    text = make(size)
                    self.assertGreaterEqual(len(text), size)
                    best = None
                    for _ in range(5):
                        t0 = time.perf_counter()
                        r = self.cap.is_pipeline_prompt(text, settings, logged=logged)
                        dt = time.perf_counter() - t0
                        best = dt if best is None else min(best, dt)
                    times.append(best)
                    results.append(r)
                self.assertLess(times[2], 0.01, (name, logged, times))
                self.assertLessEqual(times[2], 2.5 * times[0] + 0.002, (name, logged, times))
                self.assertEqual(results, [verdict[logged]] * 3, (name, logged))
        rec = {"type": "attachment", "uuid": "x", "attachment": {
            "type": "queued_command", "prompt": _wrapped(PASTED_TASK + "q" * 40_000), "commandMode": "prompt",
            "origin": {"kind": "human"}}}
        t0 = time.perf_counter()
        self.assertEqual(self.cap._classify_record(rec), "machine")
        self.assertLess(time.perf_counter() - t0, 0.05)


class TestDisplayPrompt(unittest.TestCase):
    """Unit level: _capture.display_prompt — readable, verbatim unless the WHOLE
    text is Claude Code markup, and linear-time on hostile pastes."""

    @classmethod
    def setUpClass(cls):
        cls.cap = _load("_capture")

    def render(self, text):
        return self.cap.display_prompt({"type": "user", "message": {"content": text}})

    def test_commands_and_bash(self):
        self.assertEqual(self.render("<command-message>goal</command-message>\n"
                                     "<command-name>/goal</command-name>\n"
                                     "<command-args>ship it</command-args>"), "/goal ship it")
        self.assertEqual(self.render("<command-name>/compact</command-name>"), "/compact")
        self.assertEqual(self.render("<bash-input>kubectl get pods</bash-input>"), "!kubectl get pods")
        self.assertEqual(self.render("<bash-input>echo '</bash-input>'</bash-input>"),
                         "!echo '</bash-input>'", "a quoted closing tag is part of the command")

    def test_anything_else_stays_verbatim(self):
        for text in ("why does <command-name>/goal</command-name> fail?",
                     "<command-name>/a</command-name><command-args>x</command-args>"
                     "<command-name>/b</command-name>",  # two blocks: never collapse to '/a x'
                     "echo hi </bash-input>",  # the startswith guard
                     "<command-name>/goal</command-name><command-args>x</command-args>\nwhy is this wrong?",
                     "<command-name> </command-name>",
                     "<bash-input>ls</bash-input> and then?",
                     "plain ask"):
            self.assertEqual(self.render(text), text, text)

    def test_hostile_paste_is_linear_time(self):
        """Review v4.7.4 MEDIUM: an unclosed <command-name> + 4k spaces took 31 s
        (cubic backtracking) — hooks.json sets no timeout, so the Stop froze."""
        import time
        for text in ("<command-name>" + " " * 200_000,
                     "<command-message>x</command-message><command-args>" + " " * 200_000,
                     "<bash-input>" + " " * 200_000,
                     "<command-name>/goal</command-name>" + " \n" * 100_000 + "tail",
                     # round-2 review: MANY unclosed starts made .sub() quadratic (20k: 19.5 s)
                     "<command-name>" * 20_000,
                     "<command-name>/a</command-name>" + "<command-args>" * 20_000):
            t = time.perf_counter()
            self.render(text)
            self.assertLess(time.perf_counter() - t, 0.5, text[:30])


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
        # v4.9: neutral text (`STAGE=VERIFY go` is a pipeline marker now)
        headless = {"type": "user", "uuid": "p", "message": {"content": "verify the rollout and report"}}
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
