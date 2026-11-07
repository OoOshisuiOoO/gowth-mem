"""v4.9 §1.1 — GOWTH_MEM_OFF switches every gowth-mem hook off.

A bot pipeline that drives Claude Code (mention-agent: `claude -p` stages and a
tmux session per task) had no way to keep gowth-mem out of its rounds short of
disabling ALL hooks — its VERIFY prompts were counted as turns, captured into
the synced session logs, recalled against, dumped at PreCompact, and a
cadence directive once replaced a stage's BRIEF with "auto-journal: …".

Contract pinned here, through the REAL commands in hooks/hooks.json:
  * with GOWTH_MEM_OFF=1|true|yes|on (any case) every command exits 0, prints
    nothing on stdout or stderr, drains its stdin (a 200 KB Stop payload is
    read to the end — the host never writes into a closed pipe), and leaves
    the vault byte- and mtime-identical (no log line, no .locks/, no state);
  * the positive control (same vault, no env) produces output or writes —
    and still exits 0 although SYNC-CONFLICT.md makes the pull return 2
    (session-start.sh ends `wait; exit 0`, PostCompact ends `|| true`);
  * bash `case` and `_home.hooks_off` agree on every spelling;
  * explicit CLIs keep working; doctor.sh and `bootstrap-load.py --report`
    say that hooks are disabled.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "hooks" / "scripts"
HOOKS_JSON = REPO / "hooks" / "hooks.json"
sys.path.insert(0, str(SCRIPTS))

import _home  # type: ignore  # noqa: E402

NOTICE = "GOWTH_MEM_OFF is set — hooks are disabled"
SCRUB = ("AI_AGENT", "GOWTH_WORKSPACE", "CLAUDE_SUBAGENT", "GOWTH_MEM_OFF",
         "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_PLUGIN_ROOT", "GOWTH_MEM_DEBUG",
         "GOWTH_MEM_NO_AUTOHEAL", "GOWTH_MEM_GIT_TOKEN")
STOP_PAYLOAD_BYTES = 200 * 1024
PROMPT = "why did we pick pelican-router for the ingress gateway, and what was the rationale?"


def hook_commands() -> list:
    """(event, shell command) for every command hook in hooks.json, with
    ${CLAUDE_PLUGIN_ROOT} substituted the way Claude Code does."""
    data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    out = []
    for event, groups in data["hooks"].items():
        for group in groups:
            for hook in group.get("hooks", []):
                if hook.get("type") == "command":
                    out.append((event, hook["command"].replace("${CLAUDE_PLUGIN_ROOT}", str(REPO))))
    return out


def snapshot(root: Path) -> dict:
    """sha1 + mtime of every file and the set of directories under `root`."""
    snap: dict = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for d in dirnames:
            snap[os.path.relpath(os.path.join(dirpath, d), root) + "/"] = "dir"
        for f in filenames:
            p = Path(dirpath, f)
            try:
                st = p.stat()
                snap[str(p.relative_to(root))] = (hashlib.sha1(p.read_bytes()).hexdigest(), st.st_mtime_ns)
            except OSError:
                snap[str(p.relative_to(root))] = "vanished"
    return snap


def run_command(command: str, payload: bytes, env: dict, cwd: Path, timeout: float = 120.0):
    """Run one hook command through /bin/sh like the host does. Returns
    (rc, stdout, stderr, fully_drained). stdin is written from this thread so
    a hook that exits WITHOUT reading a payload larger than the pipe buffer is
    caught as a BrokenPipeError (communicate() would swallow it)."""
    proc = subprocess.Popen(["/bin/sh", "-c", command], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, cwd=str(cwd))
    sinks: dict = {}

    def reader(name, stream):
        try:
            sinks[name] = stream.read()
        finally:
            stream.close()

    threads = [threading.Thread(target=reader, args=("out", proc.stdout)),
               threading.Thread(target=reader, args=("err", proc.stderr))]
    for t in threads:
        t.start()
    drained = True
    try:
        proc.stdin.write(payload)
        proc.stdin.close()
    except BrokenPipeError:
        drained = False
        try:
            proc.stdin.close()
        except Exception:
            pass
    rc = proc.wait(timeout=timeout)
    for t in threads:
        t.join(timeout)
    return (rc, sinks.get("out", b"").decode("utf-8", "replace"),
            sinks.get("err", b"").decode("utf-8", "replace"), drained)


class _SeededVault(unittest.TestCase):
    """A vault where every hook has work to do: SYNC-CONFLICT.md, an index,
    cadences at 1, a workspace with entries, a transcript with human turns."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="gowth_off_"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.vault = self.root / "vault"
        self.home = self.root / "home"
        self.cfg = self.root / "claude-config"
        self.proj = self.root / "proj"
        self.bin = self.root / "bin"
        for d in (self.home, self.cfg, self.proj, self.bin):
            d.mkdir(parents=True)
        # every `python3` in hooks.json and the wrappers runs THIS interpreter,
        # every `bash` the system one (3.2 on macOS — the wrappers' `case` must
        # stay bash-3.2-safe even where a newer bash comes first on PATH)
        (self.bin / "python3").symlink_to(sys.executable)
        if Path("/bin/bash").is_file():
            (self.bin / "bash").symlink_to("/bin/bash")
        self.seed()
        self.sid = "off-" + uuid.uuid4().hex[:8]
        self.transcript = self.root / "transcript.jsonl"
        recs = []
        for i, text in enumerate(("rename the parser module", "now add the retry test",
                                  "summarize what changed")):
            recs.append({"type": "user", "uuid": f"u{i}", "promptId": f"p{i}",
                         "origin": {"kind": "human"}, "message": {"role": "user", "content": text}})
            recs.append({"type": "assistant", "uuid": f"a{i}", "message": {"role": "assistant", "content": [
                {"type": "text", "text": f"done step {i}"},
                {"type": "tool_use", "name": "Edit", "input": {"file_path": "/repo/parser.py"}}]}})
        self.transcript.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")

    def seed(self) -> None:
        wsd = self.vault / "workspaces" / "demo"
        (wsd / "journal").mkdir(parents=True)
        (wsd / "docs").mkdir()
        (wsd / "t").mkdir()
        (wsd / "workspace.json").write_text('{"name": "demo"}')
        (wsd / "docs" / "handoff.md").write_text("# Handoff\n\n- host:mac 2026-11-01 [doing] ingress cutover\n")
        (wsd / "t" / "00-README.md").write_text("---\nslug: t\ntitle: T\n---\n# T\n")
        (wsd / "t" / "2026-09-10-a.md").write_text(
            "---\nslug: t-a\n---\n## [decision] Use pelican-router for the ingress gateway\n\n"
            "Because pelican-router terminates gRPC at the edge. Rationale: fewer hops.\n")
        (self.vault / "config.json").write_text(json.dumps({"active_workspace": "demo"}))
        (self.vault / "settings.json").write_text(json.dumps({
            "layout_version": 3,
            "auto_journal": {"journal_every": 1, "auto_journal_enabled": True},
            "reflection": {"enabled": True, "turn_interval": 1, "min_review_turns": 1},
            "journal": {"auto_forget_enabled": False},
            "retrieval": {"daily_full_reindex": False},
            "sync": {"auto_sync_on_stop": False},
            "recall": {"on_prompt_score_threshold": 0.0},
        }))
        (self.vault / "SYNC-CONFLICT.md").write_text("# SYNC-CONFLICT\n\n- workspaces/demo/docs/handoff.md\n")
        r = subprocess.run([sys.executable, str(SCRIPTS / "_index.py")], capture_output=True, text=True,
                           env=self.env(off=None), timeout=120)
        assert r.returncode == 0 and (self.vault / "index.db").is_file(), r.stderr
        self.backdate()

    def backdate(self) -> None:
        """PreCompact's "recently flushed" heuristic (any .md touched in the last
        5 min) must not mistake the seeding — or the previous hook's writes —
        for a flush: the positive control has to see a dump."""
        old = time.time() - 3600
        for dirpath, _, filenames in os.walk(self.vault):
            for f in filenames:
                try:
                    os.utime(os.path.join(dirpath, f), (old, old))
                except OSError:
                    pass

    def env(self, off) -> dict:
        env = {k: v for k, v in os.environ.items() if k not in SCRUB}
        env.update({"HOME": str(self.home), "CLAUDE_CONFIG_DIR": str(self.cfg),
                    "GOWTH_MEM_HOME": str(self.vault),
                    "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}"})
        if off is not None:
            env["GOWTH_MEM_OFF"] = off
        return env

    def payload(self, event: str) -> bytes:
        base = {"session_id": self.sid, "transcript_path": str(self.transcript),
                "cwd": str(self.proj), "hook_event_name": event}
        if event == "SessionStart":
            base["source"] = "startup"
        elif event in ("PreCompact", "PostCompact"):
            base["trigger"] = "manual"
        elif event == "UserPromptSubmit":
            base["prompt"] = PROMPT
        elif event == "Stop":
            base["stop_hook_active"] = False
            base["last_assistant_message"] = "x" * STOP_PAYLOAD_BYTES
        data = json.dumps(base).encode("utf-8")
        if event == "Stop":
            self.assertGreaterEqual(len(data), STOP_PAYLOAD_BYTES)
        return data

    def settle(self, max_wait: float = 10.0) -> dict:
        """Wait until detached children stop writing (stable for 0.6 s)."""
        deadline = time.time() + max_wait
        prev = snapshot(self.vault)
        while time.time() < deadline:
            time.sleep(0.6)
            cur = snapshot(self.vault)
            if cur == prev:
                return cur
            prev = cur
        return prev


class HooksJsonOffTest(_SeededVault):
    def test_every_hook_command_is_silent_and_writes_nothing(self):
        commands = hook_commands()
        events = {e for e, _ in commands}
        self.assertTrue({"SessionStart", "PreCompact", "PostCompact", "UserPromptSubmit", "Stop"} <= events,
                        f"hooks.json lost an event: {sorted(events)}")
        before = snapshot(self.vault)
        self.assertIn("SYNC-CONFLICT.md", before)
        self.assertIn("index.db", before)
        for event, command in commands:
            rc, out, err, drained = run_command(command, self.payload(event), self.env("1"), self.proj)
            self.assertEqual(rc, 0, f"{event}: {command}\n{err}")
            self.assertEqual(out, "", f"{event} printed on stdout: {out[:300]}")
            self.assertEqual(err, "", f"{event} printed on stderr: {err[:300]}")
            self.assertTrue(drained, f"{event} exited without reading its stdin: {command}")
        time.sleep(1.0)  # anything detached would have started writing by now
        self.assertEqual(snapshot(self.vault), before, "GOWTH_MEM_OFF=1 must not touch the vault")

    def test_positive_control_without_the_env(self):
        """Same vault, no opt-out: each command does something — and exits 0
        although SYNC-CONFLICT.md makes the background pull return 2."""
        for event, command in hook_commands():
            self.backdate()
            before = snapshot(self.vault)
            rc, out, err, drained = run_command(command, self.payload(event), self.env(None), self.proj)
            self.assertEqual(rc, 0, f"{event}: {command} exited {rc}\n{err[:500]}")
            self.assertTrue(drained, f"{event} did not read its stdin")
            changed = snapshot(self.vault) != before
            self.assertTrue(out.strip() or err.strip() or changed,
                            f"{event}: positive control did nothing — the OFF test would be vacuous")
            if event == "SessionStart":
                self.assertIn("gowth-mem:bootstrap", out)
            if event == "Stop":
                self.assertIn("[gowth-mem:", out, "cadences at 1 → a directive")
            if event == "PostCompact":
                self.assertIn("SYNC-CONFLICT.md present", err, "the sync refused (rc 2) and `|| true` hid it")
        self.settle()

    def test_session_start_exits_0_while_the_pull_reports_a_conflict(self):
        """`wait $SYNC_PID` used to be the script's status: 2 with a pending
        SYNC-CONFLICT.md, 1 for an offline pull."""
        r = subprocess.run([sys.executable, str(SCRIPTS / "auto-sync.py"), "--pull-only", "--quiet"],
                           capture_output=True, text=True, env=self.env(None), timeout=60)
        self.assertEqual(r.returncode, 2, "precondition: the pull itself reports the conflict")
        rc, out, _, _ = run_command(f'bash "{SCRIPTS / "session-start.sh"}"',
                                    self.payload("SessionStart"), self.env(None), self.proj)
        self.assertEqual(rc, 0)
        self.assertIn("gowth-mem:bootstrap", out)
        self.settle()


class PythonEntrypointsOffTest(_SeededVault):
    """The direct Python entrypoints honour the env on their own (a wrapper is
    not the only way in: hooks.json calls auto-journal.py / auto-sync.py
    directly, and the Stop hook spawns auto-sync.py detached)."""

    ENTRYPOINTS = (
        ("auto-journal.py", (), "Stop"),
        ("auto-sync.py", ("--pull-only", "--quiet"), "SessionStart"),
        ("auto-sync.py", ("--commit-only", "--quiet"), "PreCompact"),
        ("auto-sync.py", ("--pull-rebase-push", "--quiet"), "PostCompact"),
        ("bootstrap-load.py", (), "SessionStart"),
        ("_recall_prompt.py", (), "UserPromptSubmit"),
        ("conflict-detect.py", (), "UserPromptSubmit"),
        ("precompact-flush.py", (), "PreCompact"),
    )

    def test_each_entrypoint_drains_and_exits_silently(self):
        before = snapshot(self.vault)
        for spelling in ("1", "true", "YES", "On"):
            for script, args, event in self.ENTRYPOINTS:
                payload = self.payload(event)
                if event != "Stop":  # every entrypoint drains a big event too
                    payload = payload + b" " * STOP_PAYLOAD_BYTES
                command = " ".join([f'"{sys.executable}"', f'"{SCRIPTS / script}"', *args])
                rc, out, err, drained = run_command(command, payload, self.env(spelling), self.proj)
                self.assertEqual((rc, out, err), (0, "", ""), f"{script} {args} with GOWTH_MEM_OFF={spelling}")
                self.assertTrue(drained, f"{script} did not drain stdin")
        time.sleep(0.5)
        self.assertEqual(snapshot(self.vault), before)

    def test_falsy_values_keep_the_hooks_on(self):
        for spelling in ("0", "false", "no", "off", ""):
            r = subprocess.run([sys.executable, str(SCRIPTS / "conflict-detect.py")],
                               input=json.dumps({"prompt": PROMPT}), capture_output=True, text=True,
                               env=self.env(spelling), timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("SYNC-CONFLICT pending", r.stdout, f"GOWTH_MEM_OFF={spelling!r} must not disable")


class BashPythonParityTest(_SeededVault):
    """The bash `case` in the wrappers and `_home.hooks_off` decide alike."""

    VALUES = ("1", "true", "TRUE", "True", "tRuE", "yes", "YES", "on", "ON", "On",
              "0", "false", "no", "off", "", " 1", "1 ", "on\n", "2", "tru", "yess", "y", "t",
              "ＯＮ", "enabled")

    def test_wrapper_and_helper_agree(self):
        for value in self.VALUES:
            expected_off = _home.hooks_off({"GOWTH_MEM_OFF": value})
            rc, out, err, _ = run_command(f'bash "{SCRIPTS / "conflict-detect.sh"}"',
                                          json.dumps({"prompt": PROMPT}).encode(), self.env(value), self.proj)
            self.assertEqual(rc, 0, err)
            bash_off = out == ""
            self.assertEqual(bash_off, expected_off, f"GOWTH_MEM_OFF={value!r}: bash {bash_off}, python {expected_off}")
        # positive signal: both sides really said yes AND no somewhere
        self.assertTrue(_home.hooks_off({"GOWTH_MEM_OFF": "on"}))
        self.assertFalse(_home.hooks_off({"GOWTH_MEM_OFF": "off"}))


class HooksOffHelperTest(unittest.TestCase):
    def test_truthy_set(self):
        for v in ("1", "true", "TRUE", "yes", "Yes", "on", "ON"):
            self.assertTrue(_home.hooks_off({"GOWTH_MEM_OFF": v}), v)
        for v in ("0", "false", "no", "off", "", " 1", "1 ", "2", "truthy", "ＯＮ"):
            self.assertFalse(_home.hooks_off({"GOWTH_MEM_OFF": v}), repr(v))
        self.assertFalse(_home.hooks_off({}))
        self.assertFalse(_home.hooks_off({"GOWTH_MEM_OFF": None}))
        self.assertFalse(_home.hooks_off({"GOWTH_MEM_OFF": 1}))

    def test_defaults_to_os_environ(self):
        old = os.environ.get("GOWTH_MEM_OFF")
        try:
            os.environ["GOWTH_MEM_OFF"] = "yes"
            self.assertTrue(_home.hooks_off())
            os.environ["GOWTH_MEM_OFF"] = "no"
            self.assertFalse(_home.hooks_off())
        finally:
            if old is None:
                os.environ.pop("GOWTH_MEM_OFF", None)
            else:
                os.environ["GOWTH_MEM_OFF"] = old


class ExplicitCliAndReportsTest(_SeededVault):
    def test_explicit_cli_still_works(self):
        (self.vault / "index.db").unlink()
        r = subprocess.run([sys.executable, str(SCRIPTS / "_index.py")], capture_output=True, text=True,
                           env=self.env("1"), timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.vault / "index.db").is_file(), "hooks only — _index.py must still build")

    def test_bootstrap_report_says_hooks_are_disabled(self):
        r = subprocess.run([sys.executable, str(SCRIPTS / "bootstrap-load.py"), "--report"], input="",
                           capture_output=True, text=True, env=self.env("1"), timeout=120, cwd=str(self.proj))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(NOTICE, r.stdout)
        self.assertIn("bootstrap (v4.8) workspace=demo", r.stdout, "the report itself still runs")
        r = subprocess.run([sys.executable, str(SCRIPTS / "bootstrap-load.py"), "--report"], input="",
                           capture_output=True, text=True, env=self.env(None), timeout=120, cwd=str(self.proj))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn(NOTICE, r.stdout)
        self.assertIn("bootstrap (v4.8) workspace=demo", r.stdout)

    def test_doctor_says_hooks_are_disabled(self):
        doctor = REPO / "bin" / "doctor.sh"
        r = subprocess.run(["bash", str(doctor), "--dry-run"], capture_output=True, text=True,
                           env=self.env("TRUE"), timeout=60)
        self.assertIn(NOTICE, r.stderr)
        self.assertEqual(r.stdout, "", "doctor keeps stdout empty (hook-safe)")
        r = subprocess.run(["bash", str(doctor), "--dry-run"], capture_output=True, text=True,
                           env=self.env(None), timeout=60)
        self.assertNotIn(NOTICE, r.stderr)
        self.assertIn("gowth-mem home", r.stderr, "positive signal: the doctor ran its checks")
        r = subprocess.run(["bash", str(doctor), "--dry-run", "--quiet"], capture_output=True, text=True,
                           env=self.env("1"), timeout=60)
        self.assertNotIn(NOTICE, r.stderr, "--quiet stays silent unless a heal is applied")


if __name__ == "__main__":
    unittest.main()
