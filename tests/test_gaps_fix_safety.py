"""v4.9 review fixes gaps-docs#3 (ledger half) and #4, plus the docs-is-a-file
crash: the gap ledger's writer fails closed.

  * a LONE `<private>` or `</private>` tag in any field, in any spelling
    `_privacy` pairs (`<prıvate>`, `<prİvate>`), is `rejected:private_tag`.
    Before: one half per field passed `sanitize()`, then `safe_write`'s
    whole-file re-sanitize paired them across lines and deleted every question
    between them (and the whole `## Closed` history on the answer path) while
    the CLI printed `added`. The check runs LAST, so every input HEAD refused
    keeps HEAD's status (a whole block in one field is still `secret_leak`);
  * the re-rendered ledger is sanitized BEFORE the write: a pair that only the
    re-render brings together (a ledger written before this fix, a union merge,
    an answer that moves a line below another) or a secret already in the file
    is `rejected:secret_leak` with a stderr line naming the file, and nothing
    is written;
  * an existing ledger that cannot be read as UTF-8 (chmod 000, a 0xE9 byte) or
    whose path cannot be inspected (a 000 workspace dir) is `rejected:unreadable`
    with its bytes unchanged — it was read as EMPTY and replaced by a
    one-question ledger, or written back with U+FFFD; a missing ledger is still
    created; `list` says "ledger unreadable" (`ledger_unreadable(ws)`);
  * `<ws>/docs` as a regular file or a dangling link is `rejected:docs_not_a_dir`
    (was a FileExistsError traceback, rc 1); any other OS error on the write is
    `rejected:write_failed`; a link swapped in after the checks is refused under
    the lock;
  * HEAD's refusal cases replay through the new `_target` / `_field_rule` with
    HEAD's statuses;
  * the reworded header (gaps-docs#5) heals an old ledger on its next write and
    re-renders byte-identically after that.

Every behaviour runs the REAL CLI (`python3 _gaps.py …`) except the lock-window
race, which patches `_target`. Growth probes cover `_privacy.has_private_tag`
and the render check on hostile ledgers.
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
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "hooks" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _gaps  # noqa: E402
import _privacy  # noqa: E402

SCRUB = ("AI_AGENT", "GOWTH_WORKSPACE", "CLAUDE_SUBAGENT", "GOWTH_MEM_OFF", "CLAUDE_CODE_ENTRYPOINT",
         "CLAUDE_PLUGIN_ROOT")
ROOT_USER = not hasattr(os, "geteuid") or os.geteuid() == 0

OPENER_Q = "Does capture drop a <private> tag that has no closing tag?"
CLOSER_Q = "Is the closing </private> tag matched case-insensitively?"
Q2 = "Which broker account should the paper trades settle into?"
Q3 = "What is the GC average daily range in trending regimes?"
Q4 = "Who owns the wombat dataset after the survey ends?"
CLOSED_A = ("Do we deploy on fridays at all?", "no, never")
CLOSED_B = ("Which port does the egret proxy listen on?", "8443")
REF_PATH = "workspaces/demo/egret/2026-11-03-port.md"
SECRET = "glpat-" + "A1b2C3d4E5f6G7h8I9j0"
RENDER_NOTE = "nothing written — the privacy filter would change"

# every spelling `_privacy`'s IGNORECASE patterns pair (a casefold() test misses the
# dotless / dotted-capital i)
SPELLINGS = ("<private>", "</private>", "<PRIVATE>", "<prıvate>", "<prİvate>", "</PRİVATE>", "</prıvate>")

# the ledger header as 91ff1ac wrote it (gaps-docs#5 rewords the last line)
OLD_HEADER = (
    "> Gaps in this workspace's memory. `research` = an agent can find the answer (vault, then primary "
    "docs/code/web) and",
    "> must save it as a [ref] with Source: before closing. `ask` = only the user knows. Managed by `_gaps.py`",
    "> (/mem-ops gap); answer with `/mem-ops gap answer <id> \"<answer>\" --evidence \"<source>\"`.",
)


def _line_open(q: str, kind: str = "research", day: str = "2026-11-07") -> str:
    return f"- {_gaps.qid_for(q)} · {kind} · {day} · {q} — from: session"


def _line_closed(q: str, ans: str, ev: str, kind: str = "ask") -> str:
    return f"- {_gaps.qid_for(q)} · {kind} · 2026-11-01→2026-11-02 · {q} → {ans} — evidence: {ev} — reason: answered"


def _doc(*body: str, header: tuple = _gaps.HEADER) -> str:
    return "\n".join(["# Open questions — demo", "", *header, "", *body]) + "\n"


class _Vault(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gowth_gaps_fix_"))
        self.addCleanup(self._cleanup)
        self.vault = self.tmp / "vault"
        self.ws = self.vault / "workspaces" / "demo"
        (self.ws / "docs").mkdir(parents=True)
        (self.ws / "workspace.json").write_text('{"name": "demo"}')
        self.settings({})
        (self.vault / "config.json").write_text(json.dumps({"active_workspace": "demo"}))
        self.qfile = self.ws / "docs" / "questions.md"
        patcher = mock.patch.dict(os.environ, {"GOWTH_MEM_HOME": str(self.vault)})
        patcher.start()
        self.addCleanup(patcher.stop)
        for k in SCRUB:
            os.environ.pop(k, None)
        self.env = dict(os.environ)
        self.env.update({"HOME": str(self.tmp / "home"), "CLAUDE_CONFIG_DIR": str(self.tmp / "cfg"),
                         "GOWTH_MEM_NO_AUTOHEAL": "1", "PYTHONDONTWRITEBYTECODE": "1"})

    def settings(self, extra: dict) -> None:
        (self.vault / "settings.json").write_text(json.dumps({"layout_version": 3, **extra}))

    def _cleanup(self):
        for root, dirs, files in os.walk(self.tmp):          # undo any chmod before removing
            for name in dirs + files:
                p = os.path.join(root, name)
                if not os.path.islink(p):
                    try:
                        os.chmod(p, 0o700)
                    except OSError:
                        pass
        shutil.rmtree(self.tmp, True)

    def run_cli(self, *args: str, env: "dict | None" = None) -> subprocess.CompletedProcess:
        r = subprocess.run([sys.executable, str(SCRIPTS / "_gaps.py"), *args], capture_output=True, text=True,
                           env=env or self.env, timeout=60)
        self.assertNotIn("Traceback", r.stderr, r.stderr)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def status(self, *args: str, env: "dict | None" = None) -> tuple:
        return self._split(self.run_cli(*args, env=env))

    def _split(self, r: subprocess.CompletedProcess) -> tuple:
        out = r.stdout.strip()
        qid, tab, st = out.partition("\t")
        self.assertTrue(tab, f"no status line: {out!r}")
        return qid, st

    def snap(self, p: "Path | None" = None) -> tuple:
        p = p or self.qfile
        if not p.exists():
            return (None, None)
        return (p.read_bytes(), os.stat(p).st_mtime_ns)

    def listed(self, *extra: str) -> list:
        return [q["id"] for q in json.loads(self.run_cli("list", "--json", *extra).stdout)]


# ── gaps-docs#3: a <private> tag in one field and </private> in another ─────

class PrivateTagTest(_Vault):
    def test_four_adds_keep_every_other_question(self):
        """The review's repro, through the CLI only: before the fix the 4th add
        printed `added` while the file kept ONE redacted line."""
        self.assertEqual(self.status("add", "research", OPENER_Q)[1], "rejected:private_tag")
        self.assertFalse(self.qfile.exists(), "a refused add must not create the ledger")
        q2, st2 = self.status("add", "ask", Q2)
        q3, st3 = self.status("add", "research", Q3)
        self.assertEqual((st2, st3), ("added", "added"))
        before = self.snap()
        for _ in range(3):                                   # repeated: byte-identical
            self.assertEqual(self.status("add", "research", CLOSER_Q)[1], "rejected:private_tag")
            self.assertEqual(self.snap(), before)
        self.assertEqual(self.listed(), [q2, q3])
        text = self.qfile.read_text()
        self.assertIn(Q2, text)
        self.assertIn(Q3, text)
        self.assertNotIn("REDACTED", text)

    def test_ledger_written_before_the_fix_keeps_its_questions(self):
        """A ledger that already holds the unclosed opener (as the pre-fix CLI
        stored it): new adds still work, the closer is refused, nothing is lost."""
        self.qfile.write_text(_doc("## Open", _line_open(OPENER_Q), "## Closed"))
        q2, st2 = self.status("add", "ask", Q2)
        q3, st3 = self.status("add", "research", Q3)
        self.assertEqual((st2, st3), ("added", "added"))
        before = self.snap()
        self.assertEqual(self.status("add", "research", CLOSER_Q)[1], "rejected:private_tag")
        self.assertEqual(self.snap(), before)
        self.assertEqual(self.listed(), [_gaps.qid_for(OPENER_Q), q2, q3])
        self.assertIn(OPENER_Q, self.qfile.read_text(), "the stored opener line is kept verbatim")

    def test_answer_path_keeps_the_closed_history(self):
        """Review repro 2: a `</private>` in an answer erased every open line after
        the opener, the `## Closed` heading and all closed history."""
        self.qfile.write_text(_doc(
            "## Open", _line_open(OPENER_Q), _line_open(Q2, kind="ask"),
            "## Closed", _line_closed(*CLOSED_A, ev="user"),
            _line_closed(*CLOSED_B, ev=REF_PATH, kind="research")))
        q2 = _gaps.qid_for(Q2)
        before = self.snap()
        for answer in ("Use the vendor whose docs say </private> blocks are stripped",
                       "Use the vendor whose docs say </prıvate> blocks are stripped"):
            for _ in range(3):
                self.assertEqual(self.status("answer", q2, answer, "--evidence", "user"), (q2, "rejected:private_tag"))
                self.assertEqual(self.snap(), before)
        self.assertEqual(self.status("answer", q2, "the main account", "--evidence", "user </private>"),
                         (q2, "rejected:private_tag"))
        self.assertEqual(self.snap(), before)
        # a clean answer still closes it, and the history is intact
        self.assertEqual(self.status("answer", q2, "the main account", "--evidence", "user"), (q2, "answered"))
        text = self.qfile.read_text()
        self.assertEqual(text.count("## Closed"), 1)
        self.assertEqual(self.listed("--closed"),
                         [_gaps.qid_for(CLOSED_A[0]), _gaps.qid_for(CLOSED_B[0]), q2])
        self.assertEqual(self.listed(), [_gaps.qid_for(OPENER_Q)])
        self.assertNotIn("REDACTED", text)

    def test_a_pair_the_rerender_creates_is_refused_not_written(self):
        """The writer's own check: a ledger stored before this fix holds a closer
        ABOVE an opener (safe: sanitize pairs an opener with a LATER closer).
        Answering the closer's question moves it below the opener — the render
        would be stripped from the opener to the closer, `## Closed` included."""
        seed = _doc("## Open", _line_open(CLOSER_Q, kind="ask"), _line_open(OPENER_Q), "## Closed",
                    _line_closed(*CLOSED_A, ev="user"))
        self.assertEqual(_privacy.sanitize(seed)[1], 0, "control: the stored ledger alone is clean")
        self.qfile.write_text(seed)
        qa = _gaps.qid_for(CLOSER_Q)
        before = self.snap()
        for _ in range(3):
            r = self.run_cli("answer", qa, "yes, in any case", "--evidence", "user")
            self.assertEqual(self._split(r), (qa, "rejected:secret_leak"))
            self.assertIn(RENDER_NOTE, r.stderr)
            self.assertIn(str(self.qfile), r.stderr, "the note names the file to fix")
            self.assertNotIn("INFO: _privacy.sanitize redacted", r.stderr, "safe_write never ran")
            self.assertEqual(self.snap(), before)
        # positive control: the render this refusal kept off the disk would have
        # lost the opener's question, the ## Closed heading and the closed history
        doc = _gaps._load(seed)
        i = doc.find("open", qa)
        q = doc.open.pop(i)[1]
        q.update(closed="2026-11-07", answer="yes, in any case", evidence="user", reason="answered")
        doc.closed.append(("q", q))
        stripped, n = _privacy.sanitize(doc.render("demo"))
        self.assertGreater(n, 0)
        self.assertNotIn("## Closed", stripped)
        self.assertNotIn(CLOSED_A[0], stripped)
        # a write that creates no pair still goes through, without the note
        qb = _gaps.qid_for(OPENER_Q)
        r = self.run_cli("tried", qb, "--note", "no staging access")
        self.assertEqual(self._split(r), (qb, "tried:1"))
        self.assertNotIn(RENDER_NOTE, r.stderr)
        self.assertIn(CLOSED_A[0], self.qfile.read_text())
        self.assertEqual(self.listed(), [qa, qb])

    def test_a_secret_already_in_the_file_blocks_the_write_instead_of_being_rewritten(self):
        self.qfile.write_text(_doc("## Open", _line_open(Q2, kind="ask"),
                                   f"  (deploy token {SECRET} — pasted by hand)", "## Closed"))
        before = self.snap()
        for _ in range(3):
            r = self.run_cli("add", "research", Q3)
            self.assertEqual(self._split(r)[1], "rejected:secret_leak")
            self.assertIn(RENDER_NOTE, r.stderr)
            self.assertEqual(self.snap(), before)
        self.assertEqual(self.listed(), [_gaps.qid_for(Q2)])

    def test_a_filter_failure_on_the_render_writes_nothing(self):
        """Library level (the failure is injected): the fields pass, then the
        whole-render sanitize reports a bypass (-1) — fail closed."""
        q2, _ = self.status("add", "ask", Q2)
        before = self.snap()
        real = _privacy.sanitize

        def flaky(text):
            return (text, -1) if "## Open" in str(text) else real(text)

        with mock.patch.object(_privacy, "sanitize", side_effect=flaky) as patched:
            self.assertEqual(_gaps.add("demo", "research", Q3, settings={})[1], "rejected:sanitize_failed")
            self.assertTrue(any("## Open" in str(c.args[0]) for c in patched.call_args_list), "the render was checked")
        self.assertEqual(self.snap(), before)
        self.assertEqual(self.listed(), [q2])

    def test_every_spelling_in_every_field_is_refused(self):
        q2, _ = self.status("add", "ask", Q2)
        before = self.snap()
        for tag in SPELLINGS:
            cases = [
                (("add", "research", f"Does capture drop a {tag} tag in the export?"), "rejected:private_tag"),
                (("add", "research", Q3, "--why", f"the {tag} block survived"), "rejected:private_tag"),
                (("answer", q2, f"the {tag} account", "--evidence", "user"), "rejected:private_tag"),
                (("answer", q2, "the main account", "--evidence", f"user {tag}"), "rejected:private_tag"),
                (("close", q2, "--evidence", f"moot {tag}", "--reason", "obsolete"), "rejected:private_tag"),
                (("tried", q2, "--note", f"blocked by {tag}"), "rejected:private_tag"),
            ]
            for args, want in cases:
                self.assertEqual(self.status(*args)[1], want, (tag, args))
                self.assertEqual(self.snap(), before, (tag, args))
        self.assertEqual(self.listed(), [q2])

    def test_head_field_refusals_keep_their_statuses(self):
        """CLAUDE.md: the new private_tag branch must not change any refusal HEAD
        made. Statuses and ids as 91ff1ac's CLI printed them for these inputs
        (a whole block in ONE field was, and stays, secret_leak with the id of the
        redacted question); private_tag is checked last."""
        self.settings({"gate": {"english_only": True}})
        q2, _ = self.status("add", "ask", Q2)
        before = self.snap()
        whole = "Is <private>hunter2</private> the deploy password?"
        vi = "Khi nào chúng ta triển khai bản phát hành mới cho khách hàng?"
        cases = [
            (("add", "ask", whole), (_gaps.qid_for("Is [REDACTED:private-block] the deploy password?"),
                                     "rejected:secret_leak")),
            (("add", "ask", Q3, "--why", "see <private>vault notes</private>"), (_gaps.qid_for(Q3),
                                                                                 "rejected:secret_leak")),
            (("answer", q2, "the <private>main</private> account", "--evidence", "user"), (q2, "rejected:secret_leak")),
            (("answer", q2, "the main account", "--evidence", "user <private>x</private>"),
             (q2, "rejected:secret_leak")),
            (("tried", q2, "--note", "blocked by <private>x</private>"), (q2, "rejected:secret_leak")),
            (("add", "ask", f"Is {SECRET} still the deploy token?"),
             (_gaps.qid_for("Is [REDACTED:gitlab-pat] still the deploy token?"), "rejected:secret_leak")),
            (("add", "ask", "Why did [gowth-mem:curator ws=demo] fire twice today?"), (None, "rejected:control_token")),
            (("add", "ask", vi), (_gaps.qid_for(vi), "rejected:not_english")),
            # a lone tag next to an older refusal reason keeps the older status
            (("add", "ask", "Why did [gowth-mem:curator] print a </private> tag?"), (None, "rejected:control_token")),
            (("add", "ask", "Khi nào chúng ta triển khai thẻ <private> cho khách hàng?"), (None, "rejected:not_english")),
        ]
        for args, (want_id, want_st) in cases:
            qid, st = self.status(*args)
            self.assertEqual(st, want_st, args)
            if want_id:
                self.assertEqual(qid, want_id, args)
            self.assertEqual(self.snap(), before, args)
        # sanitize_failed (the filter crashed) is still checked before private_tag
        with mock.patch.object(_privacy, "sanitize", return_value=("x", -1)):
            self.assertEqual(_gaps.add("demo", "ask", OPENER_Q, settings={})[1], "rejected:sanitize_failed")
        self.assertEqual(self.snap(), before)

    def test_has_private_tag_matches_what_sanitize_pairs(self):
        for tag in SPELLINGS:
            self.assertTrue(_privacy.has_private_tag(f"x {tag} y"), tag)
            closing = tag.startswith("</")
            pair = f"a {tag} b\nc </private> d" if not closing else f"a <private> b\nc {tag} d"
            self.assertEqual(_privacy.sanitize(pair), ("a [REDACTED:private-block] d", 1), tag)
        # why a casefold() substring test is not enough
        self.assertNotIn("<private>", "<prıvate>".casefold())
        self.assertNotIn("<private>", "<prİvate>".casefold())
        for clean in ("", "private", "<privat>", "< private>", "<private >", "the private key", None, 7):
            self.assertFalse(_privacy.has_private_tag(clean), repr(clean))


# ── gaps-docs#4: an unreadable ledger is never replaced ────────────────────

class UnreadableTest(_Vault):
    def seed(self) -> list:
        a, _ = self.status("add", "ask", Q2)
        b, _ = self.status("add", "research", Q3)
        c, _ = self.status("add", "ask", Q4)
        self.assertEqual(self.status("answer", c, "the survey team", "--evidence", "user"), (c, "answered"))
        return [a, b, c]

    @unittest.skipIf(ROOT_USER, "root reads a 000 file")
    def test_permission_denied_is_refused_and_bytes_are_kept(self):
        a, b, c = self.seed()
        self.assertFalse(_gaps.ledger_unreadable("demo"))
        before = self.snap()
        os.chmod(self.qfile, 0o000)
        try:
            self.assertTrue(_gaps.ledger_unreadable("demo"))
            for _ in range(3):
                self.assertEqual(self.status("add", "research",
                                             "Does the wombat exporter support TLS client certs?")[1],
                                 "rejected:unreadable")
            self.assertEqual(self.status("answer", a, "the main account", "--evidence", "user"),
                             (a, "rejected:unreadable"))
            self.assertEqual(self.status("close", b, "--evidence", "moot", "--reason", "obsolete"),
                             (b, "rejected:unreadable"))
            self.assertEqual(self.status("tried", b, "--note", "no access"), (b, "rejected:unreadable"))
            out = self.run_cli("list").stdout
            self.assertIn("ledger unreadable", out)
            self.assertNotIn("no open questions", out)
        finally:
            os.chmod(self.qfile, 0o600)
        self.assertEqual(self.snap(), before)
        self.assertEqual(self.listed(), [a, b])
        self.assertEqual(self.listed("--closed"), [c])

    def test_a_non_utf8_byte_is_refused_never_written_back_lossy(self):
        a, b, c = self.seed()
        raw = self.qfile.read_bytes().replace(b"## Closed", b"  note: owner is Ren\xe9 at the caf\xe9 desk\n## Closed")
        self.assertIn(b"\xe9", raw)
        self.qfile.write_bytes(raw)
        self.assertTrue(_gaps.ledger_unreadable("demo"))
        before = self.snap()
        for _ in range(3):
            self.assertEqual(self.status("add", "research",
                                         "Does the wombat exporter support TLS client certs?")[1],
                             "rejected:unreadable")
            self.assertEqual(self.status("tried", b, "--note", "no access"), (b, "rejected:unreadable"))
            self.assertEqual(self.snap(), before)
        data = self.qfile.read_bytes()
        self.assertEqual(data.count(b"\xe9"), 2)
        self.assertNotIn(b"\xef\xbf\xbd", data, "a lossy decode was written back")
        # read-only views stay lenient but say why writes are refused
        r = self.run_cli("list")
        self.assertIn("cannot be read as UTF-8", r.stderr)
        self.assertEqual([ln.split(" · ")[0] for ln in r.stdout.splitlines()], [a, b])
        r = self.run_cli("list", "--json")
        self.assertEqual([q["id"] for q in json.loads(r.stdout)], [a, b])
        self.assertIn("cannot be read as UTF-8", r.stderr)
        self.assertEqual([q["id"] for q in _gaps.open_items("demo")], [a, b])

    def test_a_missing_ledger_is_still_created(self):
        shutil.rmtree(self.ws / "docs")
        self.assertFalse(_gaps.ledger_unreadable("demo"))
        self.assertIn("no open questions in demo", self.run_cli("list").stdout)
        qid, st = self.status("add", "ask", Q2)
        self.assertEqual(st, "added")
        text = self.qfile.read_text()
        self.assertTrue(text.startswith("# Open questions — demo\n"))
        self.assertIn('--evidence "<vault path of that [ref] entry | URL | commit>"', text)
        self.assertEqual(self.listed(), [qid])
        self.assertFalse(_gaps.ledger_unreadable("demo"))

    @unittest.skipIf(ROOT_USER, "root reads a 000 dir")
    def test_an_uninspectable_workspace_dir_is_unreadable_not_a_traceback(self):
        """3.9-3.12 `Path.is_symlink()` raises EACCES under a 000 dir (HEAD: a
        traceback); 3.13+ swallows it and the read fails. Both say unreadable."""
        qid, _ = self.status("add", "ask", Q2)
        before = self.snap()
        os.chmod(self.ws, 0o000)
        try:
            for _ in range(3):
                self.assertEqual(self.status("add", "research", Q3)[1], "rejected:unreadable")
            self.assertEqual(self.status("tried", qid, "--note", "no access"), (qid, "rejected:unreadable"))
            self.assertIn("ledger unreadable", self.run_cli("list").stdout)
            self.assertTrue(_gaps.ledger_unreadable("demo"))
        finally:
            os.chmod(self.ws, 0o755)
        self.assertEqual(self.snap(), before)
        self.assertEqual(self.listed(), [qid])


# ── docs is a regular file / dangling link; other OS errors; HEAD's refusals ─

class TargetTest(_Vault):
    def test_docs_as_a_regular_file_is_a_status_not_a_traceback(self):
        docs = self.ws / "docs"
        shutil.rmtree(docs)
        docs.write_text("not a dir\n")
        before = self.snap(docs)
        for _ in range(3):
            self.assertEqual(self.status("add", "ask", Q2)[1], "rejected:docs_not_a_dir")
            self.assertEqual(self.snap(docs), before)
        q = _gaps.qid_for(Q2)
        self.assertEqual(self.status("answer", q, "x", "--evidence", "user"), (q, "rejected:docs_not_a_dir"))
        self.assertEqual(self.status("close", q, "--evidence", "x", "--reason", "obsolete"),
                         (q, "rejected:docs_not_a_dir"))
        self.assertEqual(self.status("tried", q, "--note", "x"), (q, "rejected:docs_not_a_dir"))
        self.assertIn("no open questions in demo", self.run_cli("list").stdout)
        self.assertFalse(_gaps.ledger_unreadable("demo"))
        self.assertEqual(docs.read_text(), "not a dir\n")

    def test_a_dangling_docs_link_is_refused(self):
        docs = self.ws / "docs"
        shutil.rmtree(docs)
        docs.symlink_to(self.ws / "docs-gone")
        self.assertEqual(self.status("add", "ask", Q2)[1], "rejected:docs_not_a_dir")
        self.assertTrue(docs.is_symlink())
        self.assertFalse((self.ws / "docs-gone").exists(), "nothing created at the link target")

    @unittest.skipIf(ROOT_USER, "root writes a 555 dir")
    def test_any_other_os_error_is_write_failed(self):
        qid, _ = self.status("add", "ask", Q2)
        before = self.snap()
        os.chmod(self.ws / "docs", 0o555)
        try:
            for _ in range(3):
                self.assertEqual(self.status("add", "research", Q3)[1], "rejected:write_failed")
        finally:
            os.chmod(self.ws / "docs", 0o755)
        self.assertEqual(self.snap(), before)
        self.assertEqual(sorted(p.name for p in (self.ws / "docs").iterdir()), ["questions.md"],
                         "no temp file left behind")
        self.assertEqual(self.listed(), [qid])

    def test_a_link_swapped_in_after_the_checks_is_refused_under_the_lock(self):
        """`_target` ran before the lock; a link that appears in between must not
        be read through and replaced by a regular file."""
        outside = self.tmp / "outside.md"
        outside.write_text(_doc("## Open", _line_open(Q2, kind="ask"), "## Closed"))
        kept = outside.read_bytes()
        self.qfile.symlink_to(outside)
        with mock.patch.object(_gaps, "_target", return_value=(self.qfile, "")):
            self.assertEqual(_gaps.add("demo", "ask", Q3, settings={})[1], "rejected:symlink")
            self.assertEqual(_gaps.tried("demo", _gaps.qid_for(Q2), "no access", settings={})[1],
                             "rejected:symlink")
        self.assertTrue(self.qfile.is_symlink())
        self.assertEqual(outside.read_bytes(), kept)
        # control: without the patch `_target` refuses it before the lock, as at HEAD
        self.assertEqual(self.status("add", "ask", Q3)[1], "rejected:symlink")

    def test_head_refusals_replay_with_head_statuses(self):
        """CLAUDE.md: a new branch in front of an existing guard replays HEAD's
        refusal cases through the new entry point (statuses as at 91ff1ac)."""
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "keep.md").write_text("not the vault's\n")
        cases = []

        # no_vault
        env = dict(self.env, GOWTH_MEM_HOME=str(self.tmp / "no-such-vault"))
        cases.append(("no_vault", lambda: self.status("add", "ask", Q2, env=env)[1]))
        # bad_ws, no_workspace
        cases.append(("bad_ws", lambda: self.status("--ws", "../escape", "add", "ask", Q2)[1]))
        cases.append(("no_workspace", lambda: self.status("--ws", "nosuchws", "add", "ask", Q2)[1]))
        for want, run in cases:
            self.assertEqual(run(), f"rejected:{want}")
        self.assertFalse((self.vault / "workspaces" / "nosuchws").exists())
        self.assertFalse((self.tmp / "no-such-vault").exists())

        # symlink: the ledger links out of the vault
        self.qfile.symlink_to(outside / "keep.md")
        self.assertEqual(self.status("add", "ask", Q2)[1], "rejected:symlink")
        self.assertEqual((outside / "keep.md").read_text(), "not the vault's\n")
        self.qfile.unlink()
        # not_a_file: the ledger path is a directory
        self.qfile.mkdir()
        self.assertEqual(self.status("add", "ask", Q2)[1], "rejected:not_a_file")
        self.assertEqual(list(self.qfile.iterdir()), [])
        self.qfile.rmdir()
        # outside_workspace: docs links to a directory outside the workspace
        shutil.rmtree(self.ws / "docs")
        (self.ws / "docs").symlink_to(outside)
        self.assertEqual(self.status("add", "ask", Q2)[1], "rejected:outside_workspace")
        self.assertEqual(sorted(p.name for p in outside.iterdir()), ["keep.md"])
        # ... and a docs link that loops is refused too (3.9 raises RuntimeError there)
        (self.ws / "docs").unlink()
        (self.ws / "docs").symlink_to(self.ws / "docs")
        self.assertIn(self.status("add", "ask", Q2)[1], ("rejected:outside_workspace", "rejected:docs_not_a_dir"))


# ── gaps-docs#5: the reworded header heals old ledgers ─────────────────────

class HeaderTest(_Vault):
    def test_an_old_header_heals_on_the_next_write_and_stays_byte_identical(self):
        self.qfile.write_text(_doc("## Open", _line_open(Q2, kind="ask"), "## Closed", header=OLD_HEADER))
        qid, st = self.status("add", "research", Q3)
        self.assertEqual(st, "added")
        text = self.qfile.read_text()
        today = date.today().isoformat()
        self.assertEqual(text, _doc("## Open", _line_open(Q2, kind="ask"),
                                    f"- {qid} · research · {today} · {Q3} — from: session", "## Closed"))
        self.assertNotIn('--evidence "<source>"', text)
        self.assertEqual(text.count("> Gaps in this workspace's memory."), 1)
        before = self.snap()
        for _ in range(3):                    # no-op writes leave the bytes alone
            self.assertEqual(self.status("add", "research", Q3), (qid, "duplicate"))
            self.assertEqual(self.snap(), before)
        again = text
        for _ in range(3):                    # and a re-render is byte-identical
            again = _gaps._load(again).render("demo")
            self.assertEqual(again, text)


# ── growth probes (CLAUDE.md 4-test rule) ──────────────────────────────────

def _best(fn, arg, reps: int = 3) -> float:
    best = float("inf")
    for _ in range(reps):
        t0 = time.perf_counter()
        fn(arg)
        best = min(best, time.perf_counter() - t0)
    return best


SHAPES = {
    "opener-flood": lambda n: "<private>" * (n // 9),
    "closer-flood": lambda n: "</private>" * (n // 10),
    "unclosed-prefix-flood": lambda n: "<privat" * (n // 7),
    "angle-run": lambda n: "<" * n,
    "whitespace-run": lambda n: " \t" * (n // 2) + "x",
    "dotless-prefix-flood": lambda n: "<prıvat" * (n // 7),
    "slash-run": lambda n: "</" * (n // 2),
    "pair-flood": lambda n: "</private><private>" * (n // 19),
}


class GrowthTest(unittest.TestCase):
    def test_has_private_tag_is_linear(self):
        for shape, make in SHAPES.items():
            times = [_best(_privacy.has_private_tag, make(n) + "z") for n in (10_000, 20_000, 40_000)]
            self.assertLess(times[2], 0.5, f"{shape}: {times}")
            if times[2] > 0.01:
                self.assertLess(times[2], 6 * max(times[0], 1e-4), f"{shape} not linear: {times}")

    def test_the_render_check_on_hostile_ledgers_is_linear(self):
        """`_mutate` sanitizes the whole re-render before the write; a hand-edited
        line of 10k/20k/40k hostile chars must not make a write super-linear."""
        import _index  # noqa: E402  (patched: the best-effort reindex is not what is measured)
        tmp = Path(tempfile.mkdtemp(prefix="gowth_gaps_fix_growth_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        with mock.patch.dict(os.environ, {"GOWTH_MEM_HOME": str(tmp)}), mock.patch.object(_gaps, "_note"), \
                mock.patch.object(_index, "reindex_paths"):
            for k in SCRUB:
                os.environ.pop(k, None)
            (tmp / "workspaces" / "demo" / "docs").mkdir(parents=True)
            (tmp / "settings.json").write_text("{}")
            q = tmp / "workspaces" / "demo" / "docs" / "questions.md"
            for shape, want in (("opener-flood", "added"), ("pair-flood", "rejected:secret_leak"),
                                ("unclosed-prefix-flood", "added"), ("closer-flood", "added")):
                times = []
                for n in (10_000, 20_000, 40_000):
                    seed = _doc("## Open", "  " + SHAPES[shape](n), "## Closed")
                    best = float("inf")
                    # best of 5: one write includes a file replace, whose I/O jitter on a CI
                    # runner made a single shot read as super-linear (8.6 / 15.3 / 94 ms on
                    # ubuntu-24.04 while the same code measures 4.7 / 8.8 / 16.6 ms, flat 2x
                    # per doubling to 160k, locally)
                    for _ in range(5):
                        q.write_text(seed)
                        t0 = time.perf_counter()
                        _qid, st = _gaps.add("demo", "ask", Q2, settings={})
                        best = min(best, time.perf_counter() - t0)
                        self.assertEqual(st, want, shape)
                        if want != "added":
                            self.assertEqual(q.read_text(), seed, shape)
                        else:
                            self.assertIn(Q2, q.read_text(), shape)
                    times.append(best)
                self.assertLess(times[2], 2.0, f"{shape}: {times}")
                if times[2] > 0.05:
                    self.assertLess(times[2], 6 * max(times[0], 1e-3), f"{shape} not linear: {times}")


if __name__ == "__main__":
    unittest.main()
