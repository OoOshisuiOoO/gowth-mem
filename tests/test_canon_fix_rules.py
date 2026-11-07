"""v4.9 review fixes gaps-docs#5 and #6: the rules canon
(`templates/dot-gowth-mem/shared/memory-rules.md`) prescribes only forms the
code accepts.

  * #6: every §9 template entry, and the §3 `Correction:` form for each of the
    nine entry types carrying the parts §3 says a correction keeps, pass the
    REAL gate CLI (`_gate.py --check`, strict). Positive control: the bare form
    with neutral filler is REJECTED for [goal] / [hypothesis] / [tool], so the
    required parts — not chance wording in a claim or a `Corrects:` path — are
    what make it pass, and §3 must keep saying so. A confirmed or refuted
    hypothesis is recorded as a [ref] correction with Source:.
  * #5: the gap-answer evidence that canon §2 and the ledger header name is what
    `_gaps.strong_evidence` accepts, and the bare file names the canon says are
    refused are refused — through `_gaps.py answer` as well.

The forms are read from the canon file itself: editing the canon into a form
the gate rejects fails this test.
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
CANON = ROOT / "templates" / "dot-gowth-mem" / "shared" / "memory-rules.md"
sys.path.insert(0, str(SCRIPTS))

import _gaps  # noqa: E402

SCRUB = ("AI_AGENT", "GOWTH_WORKSPACE", "CLAUDE_SUBAGENT", "GOWTH_MEM_OFF", "CLAUDE_CODE_ENTRYPOINT",
         "CLAUDE_PLUGIN_ROOT")
TYPES = ("decision", "exp", "ref", "tool", "reflection", "skill-ref", "secret-ref", "goal", "hypothesis")

# Neutral filler: no word any per-type gate rule keys on (status / done when /
# target / verify / test / check / a version / backticks / `@`), so a REJECT of
# the bare form proves the filler cannot satisfy a rule by accident.
FILL = {
    "<old claim>": "the export retries three times",
    "<new claim>": "the export retries five times",
    "<date>": "2026-11-07",
    "<vault-relative path>": "workspaces/demo/export-policy/2026-10-01-retries.md",
}
SOURCE = "Source: user"

# What canon §3/§4 say each type's correction keeps. The phrases must appear in
# the §3 correction bullet; each suffix is one way to keep them, appended to the
# bare form (every suffix must pass the gate).
KEEPS = {
    "goal": (("`Status:`", "`Done when:`"), (" Status: active. Done when: the export runs every night",)),
    "tool": (("version", "exact syntax in backticks"), (" (git 2.43)", " Run `export --retries 5`.")),
    "hypothesis": (("`Verify:`",), (" Verify: run the export twice on a scratch vault",)),
    "decision": ((), (" because the upstream queue drains slowly",)),
    "exp": ((), (" Next time read the export policy first.",)),
}
REJECTED_BARE = {"goal": "goal_without_status", "hypothesis": "hypothesis_without_verify",
                 "tool": "tool_without_version_or_syntax"}


def _section(text: str, number: int) -> str:
    m = re.search(rf"^## {number}\. .*?(?=^## \d+\. |\Z)", text, re.M | re.S)
    assert m, f"canon has no section {number}"
    return m.group(0)


def _bullet(section: str, starts: str) -> str:
    """One `- …` bullet with its indented continuation lines, joined."""
    lines = section.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("- " + starts):
            out = [line[2:]]
            for nxt in lines[i + 1:]:
                if not nxt.startswith("  "):
                    break
                out.append(nxt.strip())
            return " ".join(out)
    raise AssertionError(f"no bullet starting {starts!r}")


class _Canon(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = CANON.read_text(encoding="utf-8")
        cls.tmp = Path(tempfile.mkdtemp(prefix="gowth_canon_rules_"))
        vault = cls.tmp / "vault"
        (vault / "workspaces" / "demo" / "docs").mkdir(parents=True)
        (vault / "workspaces" / "demo" / "workspace.json").write_text('{"name": "demo"}')
        (vault / "settings.json").write_text(json.dumps({"layout_version": 3, "gate": {"strict": True}}))
        (vault / "config.json").write_text(json.dumps({"active_workspace": "demo"}))
        cls.env = {k: v for k, v in os.environ.items() if k not in SCRUB}
        cls.env.update({"GOWTH_MEM_HOME": str(vault), "HOME": str(cls.tmp / "home"),
                        "CLAUDE_CONFIG_DIR": str(cls.tmp / "cfg"), "GOWTH_MEM_NO_AUTOHEAL": "1",
                        "PYTHONDONTWRITEBYTECODE": "1"})

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def gate(self, entry: str) -> str:
        r = subprocess.run([sys.executable, str(SCRIPTS / "_gate.py"), "--check", entry], capture_output=True,
                           text=True, env=self.env, timeout=60)
        self.assertNotIn("Traceback", r.stderr, r.stderr)
        out = r.stdout.strip()
        self.assertEqual(r.returncode, 0 if out == "ACCEPT" else 1, (entry, out, r.stderr))
        return out


class TemplatesPassTheGateTest(_Canon):
    def test_every_section_9_entry_is_accepted(self):
        sec = _section(self.text, 9)
        m = re.search(r"Entries:\n```\n(.*?)\n```", sec, re.S)
        self.assertIsNotNone(m, "§9 has an Entries: block")
        entries = [ln for ln in m.group(1).splitlines() if ln.strip()]
        self.assertGreaterEqual(len(entries), 5)
        tags = set()
        for entry in entries:
            self.assertEqual(self.gate(entry), "ACCEPT", entry)
            tags.add(re.match(r"\[([a-z-]+)\]", entry).group(1))
        self.assertLessEqual({"decision", "ref", "tool", "hypothesis", "goal"}, tags)

    def test_the_section_3_correction_form_is_accepted_for_all_nine_types(self):
        bullet = _bullet(_section(self.text, 3), "In memory, never edit")
        forms = re.findall(r"`(Correction: [^`]+)`", bullet)
        self.assertEqual(len(forms), 1, bullet)
        form = forms[0]
        for placeholder in FILL:
            self.assertIn(placeholder, form)
        self.assertIn("Source: …", form)
        bare = form
        for placeholder, value in FILL.items():
            bare = bare.replace(placeholder, value)
        bare = bare.replace("Source: …", SOURCE)
        self.assertNotIn("<", bare)
        self.assertNotIn("…", bare)
        self.assertIn("the type's required parts (§4) still apply", bullet.replace("’", "'").lower(),
                      "§3 must say a correction keeps its type's required parts (§4)")

        for t in TYPES:
            phrases, suffixes = KEEPS.get(t, ((), ("",)))
            for phrase in phrases:
                self.assertIn(phrase, bullet, f"§3 no longer tells a [{t}] correction to keep {phrase}")
            for suffix in suffixes:
                entry = f"[{t}] {bare}{suffix}"
                self.assertEqual(self.gate(entry), "ACCEPT", entry)
            if t in REJECTED_BARE:
                # positive control: without the parts §3 names, the gate refuses it
                verdict = self.gate(f"[{t}] {bare}")
                self.assertTrue(verdict.startswith(f"REJECT: {REJECTED_BARE[t]} "), (t, verdict))

    def test_a_settled_hypothesis_is_a_ref_correction(self):
        bullet = _bullet(_section(self.text, 3), "In memory, never edit")
        self.assertIn("[ref]", bullet)
        self.assertIn("`Source:`", bullet)
        self.assertIn("not as a new `[hypothesis]`", bullet)
        form = re.findall(r"`(Correction: [^`]+)`", bullet)[0]
        entry = ("[ref] " + form.replace("<old claim>", "the export drops unclosed tags")
                 .replace("<new claim>", "refuted: it keeps them")
                 .replace("<date>", "2026-11-07")
                 .replace("<vault-relative path>", "workspaces/demo/export-policy/2026-10-02-tags.md")
                 .replace("Source: …", "Source: https://example.com/export-run-1107"))
        self.assertEqual(self.gate(entry), "ACCEPT", entry)


class GapEvidenceWordingTest(_Canon):
    PLACEHOLDER = '--evidence "<vault path of that [ref] entry | URL | commit>"'

    def test_canon_and_ledger_header_name_the_accepted_evidence(self):
        bullet = _bullet(_section(self.text, 2), "Closing a `research` question")
        self.assertIn(self.PLACEHOLDER, bullet)
        self.assertIn(self.PLACEHOLDER, " ".join(_gaps.HEADER))
        for doc in (bullet, " ".join(_gaps.HEADER)):
            self.assertNotIn('--evidence "<source>"', doc)
        # each named form, as an agent would fill it in, is strong evidence
        for ok in ("workspaces/demo/egret/2026-11-07-port.md", "https://sqlite.org/fts5.html",
                   "commit 3f2a9c81d0e4"):
            self.assertTrue(_gaps.strong_evidence(ok), ok)
        # the bare file names / file:line the canon says are refused are refused
        refused = re.search(r"A bare file\s+name or file:line \(([^)]*)\) is refused", bullet)
        self.assertIsNotNone(refused, bullet)
        names = re.findall(r"`([^`]+)`", refused.group(1))
        self.assertGreaterEqual(len(names), 2)
        for name in names + ["user"]:
            self.assertFalse(_gaps.strong_evidence(name), name)

    def test_the_cli_follows_the_wording(self):
        def cli(*args: str) -> str:
            r = subprocess.run([sys.executable, str(SCRIPTS / "_gaps.py"), *args], capture_output=True, text=True,
                               env=self.env, timeout=60)
            self.assertNotIn("Traceback", r.stderr, r.stderr)
            self.assertEqual(r.returncode, 0, r.stderr)
            return r.stdout.strip().partition("\t")[2]

        q = "What is the raw journal TTL in this vault?"
        self.assertEqual(cli("add", "research", q), "added")
        qid = _gaps.qid_for(q)
        ledger = Path(self.env["GOWTH_MEM_HOME"]) / "workspaces" / "demo" / "docs" / "questions.md"
        self.assertIn(self.PLACEHOLDER, ledger.read_text(), "the written header names the accepted forms")
        before = ledger.read_bytes()
        for weak in ("settings.json journal.raw_ttl_days", "_gaps.py:425", "user"):
            self.assertEqual(cli("answer", qid, "7 days", "--evidence", weak), "rejected:weak_evidence", weak)
            self.assertEqual(ledger.read_bytes(), before)
        self.assertEqual(cli("answer", qid, "7 days", "--evidence",
                             "workspaces/demo/journal-policy/2026-11-07-ttl.md"), "answered")


if __name__ == "__main__":
    unittest.main()
