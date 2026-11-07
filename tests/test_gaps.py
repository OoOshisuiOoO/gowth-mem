"""v4.9 §3.1-§3.2: the gap ledger — `<ws>/docs/questions.md`, written only by `_gaps.py`.

Driven through the REAL entry point (`python3 _gaps.py …` subprocesses) for every
status of the CLI contract, plus the pure parser for its totality claims:
closed-wins, first-of-repeats, shape-not-heading, unknown lines kept in place,
normalised re-render that is stable after one pass (fuzzed). Growth probes cover
the one new regex (`\\w{4,}` similarity tokens) and every parser/cleaner on
hostile 10k/20k/40k-char shapes.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "hooks" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _gaps  # noqa: E402  (pure functions only at import time)

SCRUB = ("AI_AGENT", "GOWTH_WORKSPACE", "CLAUDE_SUBAGENT", "GOWTH_MEM_OFF", "CLAUDE_CODE_ENTRYPOINT")

SPEC_OPEN = ("- Q-1a2b3c4d · research · 2026-11-07 · Which port does the egret proxy listen on? "
             "— why: deploy/egret.yaml has no port key — from: curator · tried 2026-11-09×2")
SPEC_CLOSED = ("- Q-9f8e7d6c · ask · 2026-11-02→2026-11-07 · Do we deploy on fridays? → no, never "
               "— evidence: user — reason: answered")
Q1 = "Which port does the egret proxy listen on in staging?"
Q2 = "Should the ingress gateway move off pelican-router this quarter?"


def _doc(*body: str) -> str:
    return "\n".join(["# Open questions — demo", "", *_gaps.HEADER, "", *body]) + "\n"


class _Vault(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gowth_gaps_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.vault = self.tmp / "vault"
        ws = self.vault / "workspaces" / "demo"
        (ws / "docs").mkdir(parents=True)
        (ws / "workspace.json").write_text('{"name": "demo"}')
        self.settings({})
        (self.vault / "config.json").write_text(json.dumps({"active_workspace": "demo"}))
        self.qfile = ws / "docs" / "questions.md"
        patcher = mock.patch.dict(os.environ, {"GOWTH_MEM_HOME": str(self.vault)})
        patcher.start()
        self.addCleanup(patcher.stop)
        for k in SCRUB:
            os.environ.pop(k, None)
        self.env = dict(os.environ)
        self.today = date.today().isoformat()

    def settings(self, extra: dict) -> None:
        (self.vault / "settings.json").write_text(json.dumps({"layout_version": 3, **extra}))

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        r = subprocess.run([sys.executable, str(SCRIPTS / "_gaps.py"), *args],
                           capture_output=True, text=True, env=self.env, timeout=60)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def status(self, *args: str) -> tuple:
        out = self.run_cli(*args).stdout.strip()
        qid, tab, st = out.partition("\t")
        self.assertTrue(tab, f"no status line: {out!r}")
        return qid, st

    def snap(self) -> tuple:
        if not self.qfile.exists():
            return (None, None)
        return (self.qfile.read_bytes(), os.stat(self.qfile).st_mtime_ns)


# ── pure parser ───────────────────────────────────────────────────────────

class ParseTest(unittest.TestCase):
    def test_spec_lines_parse_to_every_field(self):
        d = _gaps.parse(_doc("## Open", SPEC_OPEN, "## Closed", SPEC_CLOSED))
        self.assertEqual(len(d["open"]), 1)
        self.assertEqual(len(d["closed"]), 1)
        o, c = d["open"][0], d["closed"][0]
        self.assertEqual(o, {"id": "Q-1a2b3c4d", "kind": "research", "opened": "2026-11-07", "closed": "",
                             "question": "Which port does the egret proxy listen on?",
                             "why": "deploy/egret.yaml has no port key", "source": "curator",
                             "answer": "", "evidence": "", "reason": "", "tries": 2,
                             "last_tried": "2026-11-09"})
        self.assertEqual(c, {"id": "Q-9f8e7d6c", "kind": "ask", "opened": "2026-11-02", "closed": "2026-11-07",
                             "question": "Do we deploy on fridays?", "why": "", "source": "",
                             "answer": "no, never", "evidence": "user", "reason": "answered",
                             "tries": 0, "last_tried": ""})
        self.assertEqual(_gaps.render_line(o), SPEC_OPEN)
        self.assertEqual(_gaps.render_line(c), SPEC_CLOSED)

    def test_closed_wins_and_repeats_collapse_and_shape_decides(self):
        """The layouts a `merge=union` leaves behind: the same id open (tried on
        one machine) and closed (on the other); a NEW open line that ended up
        below `## Closed`; repeated lines; a duplicated heading."""
        text = _doc(
            "## Open",
            "- Q-aaaaaaa1 · research · 2026-11-01 · Is A true? — from: session · tried 2026-11-05×1",
            "## Closed",
            "- Q-aaaaaaa1 · research · 2026-11-01→2026-11-06 · Is A true? → yes — evidence: https://e.x/a — reason: answered",
            "- Q-bbbbbbb2 · ask · 2026-11-06 · Is B wanted? — from: session",
            "- Q-bbbbbbb2 · ask · 2026-11-06 · Is B wanted? — from: session · tried 2026-11-07×1",
            "- Q-aaaaaaa1 · research · 2026-11-01→2026-11-08 · Is A true? → no — evidence: https://e.x/b — reason: answered",
            "## Closed",
        )
        d = _gaps.parse(text)
        self.assertEqual([q["id"] for q in d["open"]], ["Q-bbbbbbb2"], "the new open line must stay open")
        self.assertEqual(d["open"][0]["tries"], 0, "repeated open lines collapse to the FIRST")
        self.assertEqual([q["id"] for q in d["closed"]], ["Q-aaaaaaa1"], "closed wins over the open repeat")
        self.assertEqual(d["closed"][0]["answer"], "yes", "repeated closed lines collapse to the first")
        out = _gaps._load(text).render("demo")
        self.assertEqual(out.count("## Closed"), 1)
        self.assertLess(out.index("Q-bbbbbbb2"), out.index("## Closed"), "re-rendered under ## Open")

    def test_unknown_lines_are_kept_verbatim_in_place(self):
        text = _doc(
            "A note the user typed above the sections.",
            "## Open",
            "- Q-aaaaaaa1 · ask · 2026-11-01 · First question here?",
            "  (user remark about the first question)",
            "- Q-bbbbbbb2 · ask · 2026-11-02 · Second question here?",
            "## Closed",
            "## Notes",
            "free text kept below the closed section",
        )
        out = _gaps._load(text).render("demo")
        lines = out.splitlines()
        self.assertIn("A note the user typed above the sections.", lines)
        self.assertLess(lines.index("A note the user typed above the sections."), lines.index("## Open"))
        i1 = next(i for i, ln in enumerate(lines) if "Q-aaaaaaa1" in ln)
        self.assertEqual(lines[i1 + 1], "  (user remark about the first question)")
        self.assertIn("Q-bbbbbbb2", lines[i1 + 2])
        self.assertEqual(lines[-2:], ["## Notes", "free text kept below the closed section"])
        self.assertEqual(_gaps._load(out).render("demo"), out, "normalised render is a fixed point")

    def test_malformed_q_lines_are_unknown_lines_not_questions(self):
        for bad in ("- Q-zzzzzzzz · ask · 2026-11-01 · not hex",
                    "- Q-1a2b3c4d · maybe · 2026-11-01 · bad kind",
                    "- Q-1a2b3c4d · ask · 2026-13 · bad date",
                    "- Q-1a2b3c4d · ask · 2026-11-01",
                    "- [ref] an entry is not a question"):
            text = _doc("## Open", bad, "## Closed")
            self.assertEqual(_gaps.parse(text), {"open": [], "closed": []}, bad)
            self.assertIn(bad, _gaps._load(text).render("demo"), "kept verbatim")

    def test_ids_follow_the_spec_rule(self):
        want = "Q-" + hashlib.sha1("which port does egret use".encode()).hexdigest()[:8]
        self.assertEqual(_gaps.qid_for("  Which PORT   does egret use?! "), want)
        self.assertEqual(_gaps.qid_for("which port does egret use."), want)
        self.assertNotEqual(_gaps.qid_for("which port does egret use", 2), want)

    def test_cleaned_fields_never_split_as_separators(self):
        cases = {
            "question": ["looks done — tick it?", "— leading dash question here", "A → B and · C here?",
                         "ends with a dash —", "x · tried 2026-11-01×3 inside"],
            "why": ["a — why: b — from: user", "— lead", "tail —", "c · tried 2026-11-01×1"],
        }
        for q in cases["question"]:
            for w in cases["why"]:
                qq, ww = _gaps._clean(q, question=True), _gaps._clean(w)
                for s in (qq, ww):
                    self.assertNotIn(" — ", s)
                    self.assertNotIn(" · ", s)
                self.assertNotIn(" → ", qq)
                item = dict(_gaps._blank(), id="Q-1a2b3c4d", kind="ask", opened="2026-11-07",
                            question=qq, why=ww, source="user", tries=1, last_tried="2026-11-08")
                back = _gaps.parse(_doc("## Open", _gaps.render_line(item)))["open"]
                self.assertEqual(back, [item], (q, w))

    def test_parse_is_total_and_one_normalisation_is_a_fixed_point(self):
        frags = ["- Q-", "Q-1a2b3c4d", "Q-9f8e7d6c", " · ", " — ", " → ", "→", "—", "·", "why: ",
                 "from: ", "evidence: ", "reason: ", " · tried ", "2026-11-07", "2026-11-07→2026-11-08",
                 "×", "2", "0", "research", "ask", "## Open", "## Closed", "# Open questions", "> ",
                 "text", "", " ", "\t", "?", ": ", "- [", " ", "\x0c", "ß"]
        rng = random.Random(4901)
        for _ in range(3000):
            lines = []
            for _ in range(rng.randint(0, 14)):
                if rng.random() < 0.35:
                    lines.append("- Q-1a2b3c4d · " + rng.choice(["ask", "research"]) + " · "
                                 + rng.choice(["2026-11-07", "2026-11-07→2026-11-08"]) + " · "
                                 + "".join(rng.choice(frags) for _ in range(rng.randint(0, 9))))
                else:
                    lines.append("".join(rng.choice(frags) for _ in range(rng.randint(0, 8))))
            text = "\n".join(lines)
            parsed = _gaps.parse(text)
            once = _gaps._load(text).render("demo")
            self.assertEqual(_gaps.parse(once), parsed, repr(text))
            self.assertEqual(_gaps._load(once).render("demo"), once, repr(text))

    def test_parse_accepts_non_text(self):
        self.assertEqual(_gaps.parse(None), {"open": [], "closed": []})
        self.assertEqual(_gaps.parse(""), {"open": [], "closed": []})

    def test_strong_evidence(self):
        for ok in ("workspaces/demo/egret/2026-11-07-port.md", "docs/ref.md:12", "see hooks/scripts/_gaps.py",
                   "https://docs.example.com/egret#ports", "commit 3f2a9c81", "repo@3f2a9c81d0e4", "ref.md",
                   "see docs/ref.md.", "(https://docs.example.com/x)."):
            self.assertTrue(_gaps.strong_evidence(ok), ok)
        for weak in ("user", "the user said so", "v1.2", "deadbeef", "", "e.g. yes", "ops@example.com"):
            self.assertFalse(_gaps.strong_evidence(weak), weak)


# ── CLI: the real entry point ─────────────────────────────────────────────

class CliTest(_Vault):
    def test_add_creates_the_file_in_the_spec_shape(self):
        self.assertFalse(self.qfile.exists(), "lazy create")
        qid, st = self.status("add", "--kind", "research", Q1, "--why", "deploy/egret.yaml has no port key",
                              "--from", "curator")
        self.assertEqual(st, "added")
        self.assertEqual(qid, _gaps.qid_for(Q1))
        want = _doc("## Open",
                    f"- {qid} · research · {self.today} · {Q1} — why: deploy/egret.yaml has no port key "
                    f"— from: curator",
                    "## Closed")
        self.assertEqual(self.qfile.read_text(), want)
        self.assertNotIn("\n- [", self.qfile.read_text())

    def test_duplicate_is_a_no_op_three_passes(self):
        qid, _ = self.status("add", "--kind", "research", Q1)
        before = self.snap()
        for variant in (Q1, Q1.upper(), "  " + Q1.rstrip("?") + " ?! "):
            self.assertEqual(self.status("add", "--kind", "research", variant), (qid, "duplicate"))
            self.assertEqual(self.snap(), before, "a duplicate must not rewrite the ledger")

    def test_similar_needs_force(self):
        first, _ = self.status("add", "--kind", "research", Q1)
        qid, st = self.status("add", "--kind", "research", Q1.replace("staging", "production"))
        self.assertEqual(st, f"similar:{first}")
        self.assertEqual(len(_gaps.parse(self.qfile.read_text())["open"]), 1)
        qid2, st2 = self.status("add", "--kind", "research", Q1.replace("staging", "production"), "--force")
        self.assertEqual((qid2, st2), (qid, "added"))
        self.assertEqual(len(_gaps.parse(self.qfile.read_text())["open"]), 2)

    def test_rejections_write_nothing(self):
        self.status("add", "--kind", "ask", Q2)
        before = self.snap()
        secret = "glpat-" + "A1b2C3d4E5f6G7h8I9j0"
        cases = [
            (("add", "--kind", "ask", "too short?"), "rejected:too_short"),
            (("add", "--kind", "ask", "x" * 301), "rejected:too_long"),
            (("add", "--kind", "ask", "Why did [gowth-mem:curator ws=demo] fire twice today?"), "rejected:control_token"),
            (("add", "--kind", "ask", "Why did [GOWTH-MEM:self-review] fire twice today?"), "rejected:control_token"),
            (("add", "--kind", "ask", f"Is {secret} still the deploy token?"), "rejected:secret_leak"),
            (("add", "--kind", "ask", Q1, "--why", f"token {secret}"), "rejected:secret_leak"),
            (("add", "--kind", "maybe", Q1), "rejected:bad_kind"),
            (("add", Q1), "rejected:bad_kind"),
            (("add", "--kind", "ask", Q1, "--from", "robot"), "rejected:bad_from"),
            (("--ws", "../escape", "add", "--kind", "ask", Q1), "rejected:bad_ws"),
            (("--ws", "nosuchws", "add", "--kind", "ask", Q1), "rejected:no_workspace"),
        ]
        for args, want in cases:
            self.assertEqual(self.status(*args)[1], want, args)
            self.assertEqual(self.snap(), before, args)
        self.assertFalse((self.vault / "workspaces" / "nosuchws").exists(), "a typo'd --ws minted a workspace")
        self.assertFalse((self.vault / "workspaces" / ".." / "escape").exists())

    def test_english_only_follows_the_gate_setting(self):
        vi = "Khi nào chúng ta triển khai bản phát hành mới cho khách hàng?"
        self.settings({"gate": {"english_only": True}})
        self.assertEqual(self.status("add", "--kind", "ask", vi)[1], "rejected:not_english")
        self.assertFalse(self.qfile.exists())
        self.settings({"gate": {"english_only": "false"}})        # a JSON string "false" is false
        self.assertEqual(self.status("add", "--kind", "ask", vi)[1], "added")
        self.assertIn("triển khai", self.qfile.read_text())

    def test_research_answer_needs_a_path_url_or_commit(self):
        qid, _ = self.status("add", "--kind", "research", Q1)
        before = self.snap()
        self.assertEqual(self.status("answer", qid, "8443"), (qid, "rejected:no_evidence"))
        self.assertEqual(self.status("answer", qid, "8443", "--evidence", "user"), (qid, "rejected:weak_evidence"))
        self.assertEqual(self.snap(), before)
        ev = "workspaces/demo/egret/2026-11-07-port.md"
        self.assertEqual(self.status("answer", qid, "8443 (TLS)", "--evidence", ev), (qid, "answered"))
        d = _gaps.parse(self.qfile.read_text())
        self.assertEqual(d["open"], [])
        self.assertEqual((d["closed"][0]["answer"], d["closed"][0]["evidence"], d["closed"][0]["reason"],
                          d["closed"][0]["closed"]), ("8443 (TLS)", ev, "answered", self.today))
        self.assertIn(f"- {qid} · research · {self.today}→{self.today} · {Q1} → 8443 (TLS) — evidence: {ev} "
                      f"— reason: answered", self.qfile.read_text())
        self.assertEqual(self.status("answer", qid, "again", "--evidence", ev), (qid, "rejected:already_closed"))
        self.assertEqual(self.status("answer", "Q-00000000", "x", "--evidence", ev), ("Q-00000000", "not_found"))

    def test_ask_answer_takes_the_user_as_evidence(self):
        qid, _ = self.status("add", "--kind", "ask", Q2)
        self.assertEqual(self.status("answer", qid.lower().replace("q-", "q-"), "not this quarter",
                                     "--evidence", "user"), (qid, "answered"))
        self.assertEqual(_gaps.parse(self.qfile.read_text())["closed"][0]["answer"], "not this quarter")

    def test_close_and_reasons(self):
        qid, _ = self.status("add", "--kind", "ask", Q2)
        self.assertEqual(self.status("close", qid, "--evidence", "x", "--reason", "nope"), (qid, "rejected:bad_reason"))
        self.assertEqual(self.status("close", qid, "--reason", "obsolete"), (qid, "rejected:no_evidence"))
        self.assertEqual(self.status("close", qid, "--evidence", "the gateway was retired", "--reason", "obsolete"),
                         (qid, "closed"))
        c = _gaps.parse(self.qfile.read_text())["closed"][0]
        self.assertEqual((c["reason"], c["evidence"], c["answer"]), ("obsolete", "the gateway was retired", ""))
        self.assertEqual(self.status("close", "Q-12345678", "--evidence", "x", "--reason", "obsolete"),
                         ("Q-12345678", "not_found"))

    def test_tried_counts_on_the_line(self):
        qid, _ = self.status("add", "--kind", "research", Q1)
        self.assertEqual(self.status("tried", qid), (qid, "rejected:no_note"))
        self.assertEqual(self.status("tried", qid, "--note", "no staging access"), (qid, "tried:1"))
        self.assertEqual(self.status("tried", qid, "--note", "still none"), (qid, "tried:2"))
        line = next(ln for ln in self.qfile.read_text().splitlines() if qid in ln)
        self.assertTrue(line.endswith(f" · tried {self.today}×2"), line)
        q = _gaps.parse(self.qfile.read_text())["open"][0]
        self.assertEqual((q["tries"], q["last_tried"]), (2, self.today))
        self.assertNotIn("no staging access", self.qfile.read_text(), "the note belongs in the worklog")

    def test_escalation_reopens_the_question_under_a_new_id(self):
        qid, _ = self.status("add", "--kind", "research", Q1)
        self.assertEqual(self.status("close", qid, "--evidence", "curator: 3 attempts", "--reason", "escalated")[1],
                         "closed")
        self.assertEqual(self.status("add", "--kind", "ask", Q1), (qid, "duplicate"),
                         "re-asking a closed question needs --force")
        new, st = self.status("add", "--kind", "ask", Q1, "--why", "research failed 3x", "--force")
        self.assertEqual(st, "added")
        self.assertNotEqual(new, qid)
        self.assertEqual(new, _gaps.qid_for(Q1, 2))
        d = _gaps.parse(self.qfile.read_text())
        self.assertEqual([(q["id"], q["kind"]) for q in d["open"]], [(new, "ask")])
        self.assertEqual([q["id"] for q in d["closed"]], [qid])

    def test_positional_kind_form(self):
        qid, st = self.status("add", "research", Q1)
        self.assertEqual((qid, st), (_gaps.qid_for(Q1), "added"))
        self.assertEqual(_gaps.parse(self.qfile.read_text())["open"][0]["kind"], "research")

    def test_list_text_json_kind_closed(self):
        self.assertIn("no open questions in demo", self.run_cli("list").stdout)
        a, _ = self.status("add", "--kind", "research", Q1)
        b, _ = self.status("add", "--kind", "ask", Q2)
        out = self.run_cli("list").stdout.splitlines()
        self.assertEqual([ln.split(" · ")[0] for ln in out], [a, b], "file order")
        js = json.loads(self.run_cli("list", "--json", "--kind", "ask").stdout)
        self.assertEqual([q["id"] for q in js], [b])
        self.assertEqual(set(js[0]), {"id", "kind", "opened", "closed", "question", "why", "source", "answer",
                                      "evidence", "reason", "tries", "last_tried"})
        self.status("answer", b, "yes", "--evidence", "user")
        self.assertEqual([q["id"] for q in json.loads(self.run_cli("list", "--closed", "--json").stdout)], [b])
        self.assertEqual([q["id"] for q in _gaps.open_items("demo")], [a])

    def test_lock_timeout_is_a_status_not_a_hang(self):
        from _lock import file_lock  # type: ignore
        with file_lock("gaps-demo", timeout=5):
            t0 = time.time()
            self.assertEqual(self.status("add", "--kind", "ask", Q2)[1], "rejected:lock_timeout")
            self.assertLess(time.time() - t0, 30)
        self.assertFalse(self.qfile.exists())
        self.assertEqual(self.status("add", "--kind", "ask", Q2)[1], "added")

    def test_hand_edited_file_is_normalised_on_the_next_write(self):
        self.qfile.write_text(
            "# Open questions — demo\n\n\n> stale header line\n\nmy own note\n\n## Open\n\n"
            "- Q-aaaaaaa1 · ask · 2026-11-01 · Is A wanted by the team?\n"
            "- Q-aaaaaaa1 · ask · 2026-11-01 · Is A wanted by the team? · tried 2026-11-02×1\n\n## Closed\n")
        self.status("add", "--kind", "ask", Q2)
        text = self.qfile.read_text()
        self.assertEqual(text.count("Q-aaaaaaa1"), 1)
        self.assertNotIn("stale header line", text)
        self.assertIn("\nmy own note\n", text)
        self.assertNotIn("\n\n\n", text)
        self.assertEqual(_gaps._load(text).render("demo"), text)

    def test_symlinked_ledger_is_refused(self):
        outside = self.tmp / "outside.md"
        outside.write_text("not the vault's\n")
        self.qfile.symlink_to(outside)
        self.assertEqual(self.status("add", "--kind", "ask", Q2)[1], "rejected:symlink")
        self.assertEqual(outside.read_text(), "not the vault's\n")
        self.assertTrue(self.qfile.is_symlink())

    def test_ws_after_the_subcommand(self):
        other = self.vault / "workspaces" / "other" / "docs"
        other.mkdir(parents=True)
        self.assertEqual(self.status("add", "--ws", "other", "--kind", "ask", Q2)[1], "added")
        self.assertTrue((other / "questions.md").is_file())
        self.assertFalse(self.qfile.exists())


# ── growth probes (CLAUDE.md 4-test rule) ─────────────────────────────────

def _best(fn, arg, reps: int = 3) -> float:
    best = float("inf")
    for _ in range(reps):
        t0 = time.perf_counter()
        fn(arg)
        best = min(best, time.perf_counter() - t0)
    return best


SHAPES = {
    "word-run": lambda n: "abcd" * (n // 4),
    "separator-flood": lambda n: " — why: " * (n // 8),
    "tried-flood": lambda n: " · tried 2026-11-07×1" * (n // 21),
    "arrow-flood": lambda n: " → " * (n // 3),
    "qline-prefix-flood": lambda n: "- Q-1a2b3c4d · ask · 2026-11-07 · " * (n // 34),
    "whitespace-run": lambda n: " \t" * (n // 2) + "x",
    "short-lines": lambda n: "- Q-\n" * (n // 5),
    "glyph-spaces": lambda n: " — · → " * (n // 7),
}


class GrowthTest(unittest.TestCase):
    def _linear(self, name: str, fn) -> None:
        for shape, make in SHAPES.items():
            times = [_best(fn, make(n)) for n in (10_000, 20_000, 40_000)]
            self.assertLess(times[2], 1.0, f"{name}/{shape}: {times}")
            if times[2] > 0.01:
                self.assertLess(times[2], 6 * max(times[0], 1e-4), f"{name}/{shape} not linear: {times}")

    def test_parser_cleaner_and_token_regex_are_linear(self):
        self._linear("tokens", _gaps._tokens)
        self._linear("parse", _gaps.parse)
        self._linear("open-line", lambda s: _gaps._parse_line("- Q-1a2b3c4d · ask · 2026-11-07 · " + s))
        self._linear("closed-line", lambda s: _gaps._parse_line("- Q-1a2b3c4d · ask · 2026-11-07→2026-11-08 · " + s))
        self._linear("clean", lambda s: _gaps._clean(s, question=True))
        self._linear("evidence", _gaps.strong_evidence)
        self._linear("qid", _gaps.qid_for)
        self._linear("ws", _gaps.valid_ws)

    def test_similarity_over_a_hostile_ledger_through_add(self):
        """The add path runs the token regex over every open question; a
        hand-edited 40k-char line must not make it super-linear."""
        tmp = Path(tempfile.mkdtemp(prefix="gowth_gaps_growth_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        with mock.patch.dict(os.environ, {"GOWTH_MEM_HOME": str(tmp)}):
            for k in SCRUB:
                os.environ.pop(k, None)
            (tmp / "workspaces" / "demo" / "docs").mkdir(parents=True)
            (tmp / "settings.json").write_text("{}")
            q = tmp / "workspaces" / "demo" / "docs" / "questions.md"
            times = []
            for n in (10_000, 20_000, 40_000):
                q.write_text(_doc("## Open", "- Q-1a2b3c4d · ask · 2026-11-07 · " + "abcd efgh " * (n // 10),
                                  "## Closed"))
                t0 = time.perf_counter()
                qid, st = _gaps.add("demo", "ask", Q2, settings={})
                times.append(time.perf_counter() - t0)
                self.assertEqual(st, "added")
            self.assertLess(times[2], 2.0, times)


# ── two machines: the union merge driver + the total parse ────────────────

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@x", "GIT_CONFIG_NOSYSTEM": "1"}


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "init.defaultBranch=main", "-c", "core.hooksPath=/dev/null", *args],
                          cwd=str(cwd), capture_output=True, text=True,
                          env={**os.environ, **GIT_ENV}, check=check)


class UnionMergeTest(unittest.TestCase):
    """Spec §3.1: `workspaces/*/docs/questions.md merge=union` (delivered by
    `_canon.py`) lets two machines append at once without SYNC-CONFLICT.md;
    `parse()` folds what the union leaves (closed wins, repeats collapse)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gowth_gaps_union_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        bare = self.tmp / "bare.git"
        _git(self.tmp, "init", "-q", "--bare", "-b", "main", str(bare))
        self.a, self.b = self.tmp / "A", self.tmp / "B"
        _git(self.tmp, "clone", "-q", str(bare), str(self.a))
        (self.a / "workspaces" / "demo" / "docs").mkdir(parents=True)
        (self.a / "workspaces" / "demo" / "workspace.json").write_text('{"name": "demo"}')
        (self.a / "settings.json").write_text('{"layout_version": 3}')
        (self.a / ".gitignore").write_text(".locks/\nlogs/\nindex.db\nconfig.json\n")
        import _canon  # type: ignore
        self.assertEqual(dict(_canon.ensure(self.a, root=self.tmp / "no-plugin"))[".gitattributes"], "installed")
        self.q0 = self.add(self.a, "ask", "Is the numbat survey still running this year?")
        self.commit(self.a, "base")
        _git(self.a, "push", "-q", "origin", "main")
        _git(self.tmp, "clone", "-q", str(bare), str(self.b))

    def cli(self, home: Path, *args: str) -> str:
        env = {k: v for k, v in os.environ.items() if k not in SCRUB}
        env.update({"GOWTH_MEM_HOME": str(home), "GOWTH_WORKSPACE": "demo"})
        r = subprocess.run([sys.executable, str(SCRIPTS / "_gaps.py"), *args], capture_output=True,
                           text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def add(self, home: Path, kind: str, q: str) -> str:
        qid, _, st = self.cli(home, "add", "--kind", kind, q).partition("\t")
        self.assertEqual(st, "added")
        return qid

    def commit(self, home: Path, msg: str) -> None:
        _git(home, "add", "-A")
        _git(home, "commit", "-q", "-m", msg)

    def ledger(self, home: Path) -> str:
        return (home / "workspaces" / "demo" / "docs" / "questions.md").read_text()

    def test_concurrent_adds_merge_without_a_conflict(self):
        q1 = self.add(self.a, "research", "Which port does the egret proxy listen on in staging?")
        self.commit(self.a, "A adds")
        _git(self.a, "push", "-q", "origin", "main")
        q2 = self.add(self.b, "ask", "Should the ingress gateway move off pelican-router?")
        self.commit(self.b, "B adds")
        r = _git(self.b, "pull", "--rebase", "origin", "main", check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("CONFLICT", r.stdout + r.stderr)
        text = self.ledger(self.b)
        self.assertNotIn("<<<<<<<", text)
        self.assertEqual({q["id"] for q in _gaps.parse(text)["open"]}, {self.q0, q1, q2})

    def test_without_the_union_line_the_same_adds_conflict(self):
        """Positive control: the attribute is what makes the case above clean."""
        (self.a / ".gitattributes").unlink()
        self.commit(self.a, "drop the attribute")
        _git(self.a, "push", "-q", "origin", "main")
        _git(self.b, "pull", "-q", "--rebase", "origin", "main")
        self.add(self.a, "research", "Which port does the egret proxy listen on in staging?")
        self.commit(self.a, "A adds")
        _git(self.a, "push", "-q", "origin", "main")
        self.add(self.b, "ask", "Should the ingress gateway move off pelican-router?")
        self.commit(self.b, "B adds")
        r = _git(self.b, "pull", "--rebase", "origin", "main", check=False)
        self.assertIn("CONFLICT", r.stdout + r.stderr)
        _git(self.b, "rebase", "--abort", check=False)

    def test_tried_on_one_machine_closed_on_the_other_folds_to_closed(self):
        self.assertEqual(self.cli(self.a, "tried", self.q0, "--note", "no access"), f"{self.q0}\ttried:1")
        self.commit(self.a, "A tried")
        _git(self.a, "push", "-q", "origin", "main")
        self.assertEqual(self.cli(self.b, "close", self.q0, "--evidence", "survey ended", "--reason", "obsolete"),
                         f"{self.q0}\tclosed")
        self.commit(self.b, "B closed")
        r = _git(self.b, "pull", "--rebase", "origin", "main", check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        merged = self.ledger(self.b)
        self.assertEqual(merged.count(self.q0), 2, "the union keeps both lines")
        d = _gaps.parse(merged)
        self.assertEqual(d["open"], [])
        self.assertEqual([q["id"] for q in d["closed"]], [self.q0], "closed wins")
        # the next write re-renders the file normalised
        self.add(self.b, "ask", "Who owns the wombat dataset after the survey?")
        self.assertEqual(self.ledger(self.b).count(self.q0), 1)


# ── per-prompt recall never serves the ledger; /mem-recall still finds it ──

class RecallExclusionTest(_Vault):
    def test_questions_md_is_excluded_from_per_prompt_recall_only(self):
        ws = self.vault / "workspaces" / "demo"
        (ws / "t").mkdir()
        (ws / "t" / "00-README.md").write_text("---\nslug: t\ntitle: T\n---\n# T\n")
        (ws / "t" / "2026-09-10-a.md").write_text(
            "---\nslug: t-a\n---\n## [decision] Use pelican-router for the ingress gateway\n\n"
            "Because pelican-router terminates gRPC at the edge. Rationale: fewer hops.\n")
        qid, st = self.status("add", "--kind", "research",
                              "Why did we choose pelican-router for the ingress gateway rationale?")
        self.assertEqual(st, "added")
        r = subprocess.run([sys.executable, str(SCRIPTS / "_index.py")], capture_output=True, text=True,
                           env=self.env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        import _recall_prompt  # type: ignore
        import _query  # type: ignore
        settings = {"layout_version": 3, "recall": {"on_prompt_score_threshold": 0.0}}
        prompt = "why did we choose pelican-router for the ingress gateway, and what was the rationale?"
        hits = _recall_prompt.select(prompt, "demo", settings, set())
        self.assertTrue(hits, "positive control: the topic entry is served")
        self.assertTrue(all(not h["path"].endswith("docs/questions.md") for h in hits), hits)
        # /mem-recall (no per-prompt excludes) still finds the ledger
        found = _query.query_ex("demo", "", "pelican-router ingress gateway rationale", limit=20)
        self.assertIsNone(found["error"])
        rows = found["hits"]
        self.assertTrue(any(str(row.get("path", "")).endswith("docs/questions.md") for row in rows), rows)


if __name__ == "__main__":
    unittest.main()
