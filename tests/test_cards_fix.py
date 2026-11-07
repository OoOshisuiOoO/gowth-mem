"""v4.9 review fixes for topic cards (group G4):

- cards#1  card lint sees entry lines the way the indexer does (Unicode
           whitespace, `splitlines()` separators, bullet + heading forms) —
           a differential fuzz against `_index.split_chunks` + `_extract_tag`.
- cards#2  one TL;DR locator: a suffixed `## TL;DR:` heading is THE TL;DR for
           `_card` as it is for `_moc`; a "## TL;DR" that is no heading line
           refuses the write (`rejected:tldr_heading`).
- cards#4  a `_moc` rebuild archives a card it would drop (to `.history/`)
           and writes nothing when that fails.
- gaps-docs#3 (card half)  no `<private>` / `</private>` tag in a curated
           field (`private_tag`), and a write whose whole-file sanitize would
           change the README is refused (`secret_leak`).

Every write test drives the REAL entry point (`_card.py`, `_moc.py`,
`_lesson.py`, `_validate.py`, `_index.py`) in a subprocess against a scratch
GOWTH_MEM_HOME and asserts positive signals.
"""
from __future__ import annotations

import itertools
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "hooks" / "scripts"
sys.path.insert(0, str(SCRIPTS))

TODAY = date.today().isoformat()
TODO = "Cốt lõi 1 dòng (TODO)."
BEGIN = "<!-- gowth-mem:card v=1 updated=2026-11-01 -->"
END = "<!-- /gowth-mem:card -->"
CARD_A = ("### Core\n- Redis is the session cache; eviction is allkeys-lru.\n"
          "### Gotchas\n- never FLUSHALL on the shared node (2026-10-02 incident)\n"
          "### Sources\n- runbook.md:12")
CARD_B = "### Core\n- Redis is the session cache; eviction is volatile-lru since 2026-11-07.\n### Sources\n- commit abc1234"
_SCRUB = ("AI_AGENT", "GOWTH_WORKSPACE", "CLAUDE_SUBAGENT", "GOWTH_MEM_OFF", "CLAUDE_CODE_ENTRYPOINT",
          "CLAUDE_PLUGIN_ROOT")
# Every character Python's `str.isspace()` (= the parsers' Unicode `\s`, =
# `str.lstrip()`) accepts, and the ones `str.splitlines()` ends a line at.
UNICODE_WS = tuple(chr(c) for c in range(0x110000) if chr(c).isspace())
LINE_SEPS = tuple(c for c in UNICODE_WS if len(("a" + c + "b").splitlines()) == 2 and c not in "\r\n")


def _env(home: Path, **extra: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in _SCRUB}
    env["GOWTH_MEM_HOME"] = str(home)
    env.update(extra)
    return env


def _w(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _skeleton(slug: str, summary: str = "") -> str:
    from _topic_templates import render  # type: ignore
    return render("misc", slug, slug.replace("-", " ").title(), "2026-01-05", [], summary)


def _tree(home: Path) -> dict:
    out = {}
    for p in sorted(home.rglob("*")):
        rel = p.relative_to(home)
        if rel.parts[0] in ("logs", ".locks") or not p.is_file():
            continue
        out[str(rel)] = p.read_bytes()
    return out


def _indexer_tags(body: str) -> bool:
    """Would `_index._index_one` give any chunk of `body` an entry tag?"""
    import _index  # type: ignore
    return any(_index._extract_tag(h) or _index._extract_tag(c) for h, c in _index.split_chunks(body))


def _head_is_entry_line(line: str) -> bool:
    """`_card._is_entry_line` as shipped at 91ff1ac (ASCII " \\t" only, no
    bullet + heading, "\\n" lines only) — the fuzz's negative control."""
    import _card  # type: ignore
    s = line.lstrip(" \t")
    if s[:1] in ("-", "*"):
        s = s[1:].lstrip(" \t")
    elif s[:1] == "#":
        k = len(s) - len(s.lstrip("#"))
        if k > 6:
            return False
        s = s[k:].lstrip(" \t")
    if not s.startswith("["):
        return False
    close = s.find("]", 1, 13)
    return close > 1 and s[1:close].lower() in _card.ENTRY_TYPES


def _readme(heading_block: str, tail: str = "") -> str:
    """A hand-written README: frontmatter, H1, `heading_block`, the two MOC
    sections (+ `tail` before the manual one)."""
    return ("---\nslug: redis-cache\ntitle: Redis Cache\ntype: misc\nstatus: draft\n---\n\n# Redis Cache\n\n"
            + heading_block.rstrip("\n") + "\n\n## Aspects (auto)\n\n(empty)\n\n" + tail
            + "## Cross-links (manual)\n\n- [[postgres]]\n")


class _Vault(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_cardfix_")
        self.home = Path(self.tmp)
        self.wsd = self.home / "workspaces" / "w1"
        _w(self.wsd / "workspace.json", "{}")
        (self.home / "settings.json").write_text(json.dumps({"layout_version": 3}))
        self.readme = _w(self.wsd / "redis-cache" / "00-README.md", _skeleton("redis-cache"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_py(self, script: str, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
        r = subprocess.run([sys.executable, str(SCRIPTS / script), *args], input=stdin,
                           capture_output=True, text=True, timeout=120, env=_env(self.home))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        return r

    def set_card(self, body: str, topic: str = "redis-cache") -> str:
        out = self.run_py("_card.py", "--ws", "w1", "--topic", topic, "--set", "-", stdin=body).stdout
        return out.strip().split("\t")[1]

    def summary(self, line: str, topic: str = "redis-cache") -> str:
        out = self.run_py("_card.py", "--ws", "w1", "--topic", topic, "--summary", line).stdout
        return out.strip().split("\t")[1]

    def check(self) -> dict:
        rows = json.loads(self.run_py("_card.py", "--ws", "w1", "--check", "--json").stdout)
        return {r["topic"]: r for r in rows}

    def moc(self) -> subprocess.CompletedProcess:
        return self.run_py("_moc.py", "--ws", "w1")

    def scan(self, *extra: str) -> list:
        return json.loads(self.run_py("_validate.py", "--scan", "--ws", "w1", "--json", *extra).stdout)


# ── cards#1 ─────────────────────────────────────────────────────────────────

class EntryLineParityTests(unittest.TestCase):
    """Every line the indexer tags as an entry must be refused by card lint."""

    def setUp(self):
        import _card  # type: ignore
        self.c = _card

    @staticmethod
    def _fuzz_lines():
        hashes = ("", "#", "##", "###", "####", "######", "#######")
        bullets = ("", "-", "*", "+")
        reps = ("", " ", "\t", "\xa0", "\u2003", "\u3000", "\x1f")
        # A: structure (hash run, bullet, hash run — both orders) x whitespace
        # in every gap, independently present or not.
        for w in reps:
            for h1, b, h2 in itertools.product(hashes, bullets, hashes):
                for g in itertools.product(("", w), repeat=4):
                    for tag in ("[decision]", "[ref]"):
                        yield g[0] + h1 + g[1] + b + g[2] + h2 + g[3] + tag + " use valkey because licence"
        # B: EVERY Unicode whitespace character in fixed shapes.
        for w in UNICODE_WS:
            for shape in ("{w}[decision] x y", "-{w}[decision] x y", "##{w}[decision] x", "-{w}##{w}[ref] x",
                          "##{w}-{w}[ref] x", "###{w}[exp] x", "x{w}[decision] y", "x{w}##{w}[decision] y",
                          "- see below{w}## [decision] use valkey", "{w}{w}- {w}[hypothesis] x"):
                yield shape.format(w=w)
        # C: every `splitlines()` separator between prose and an entry.
        for s in LINE_SEPS:
            for shape in ("prose{s}[decision] x", "- prose{s}- [ref] x", "prose{s}## [decision] t",
                          "prose{s}### - [exp] t", "{s}[goal] x", "a{s}b{s}#### [tool] x"):
                yield shape.format(s=s)

    def test_the_parsers_whitespace_is_str_isspace(self):
        # The premise of the fix: `re`'s `\s` == str.isspace() == what lstrip() drops.
        self.assertEqual(UNICODE_WS, tuple(chr(c) for c in range(0x110000) if re.match(r"\s", chr(c))))
        self.assertEqual(len(UNICODE_WS), 29)
        self.assertEqual(set(LINE_SEPS), set("\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"))

    def test_lint_rejects_every_line_the_indexer_tags(self):
        """For every fuzz line whose card body the indexer tags as an entry,
        lint refuses the body AND some line of it is an `entry_line` (what
        `--check` / `_validate --scan` report as `card-entry-line`)."""
        tagged = 0
        head_missed = 0
        missed = []
        for line in self._fuzz_lines():
            for body in ("### Core\n" + line + "\n- trailing line", line + "\n- trailing line"):
                if not _indexer_tags(body):
                    continue
                tagged += 1
                ok, reason = self.c.lint(body, {})
                lines = self.c.normalize_body(body).split("\n")
                if (ok or not reason.startswith(("entry_line:", "bad_heading:"))
                        or not any(self.c._is_entry_line(ln) for ln in lines)):
                    missed.append((line, ok, reason))
                if not any(_head_is_entry_line(ln) for ln in lines):
                    head_missed += 1
        self.assertEqual(missed[:20], [], f"{len(missed)} indexer-tagged bodies pass the card lint")
        self.assertGreater(tagged, 7000)          # positive signal: the fuzz really produced entries
        # … and the fuzz holds the forms the shipped check let through
        # (NBSP / EMSP / VT leads, `- ## [x]`, U+2028 breaks, …).
        self.assertGreater(head_missed, 1000, head_missed)

    def test_lines_no_parser_reads_as_an_entry_stay_allowed(self):
        body = ("### Core\n- [ ] open todo\n- [[wikilink]] and [label] text\n- #hashtag\n#### deeper\n"
                "- [docs](x.md)\n| [decision] | a table cell |\n> [decision] quoted line\n+ [decision] plus bullet\n"
                "- see the [decision] entry in the aspect\n####### [decision] seven hashes\n- - [ref] nested\n"
                "- `[decision]` in code")
        self.assertEqual(self.c.lint(body, {}), (True, ""))
        for line in body.split("\n")[1:]:
            self.assertFalse(_indexer_tags("### Core\n" + line + "\n- trailing line"), line)   # control

    def test_the_reviewed_forms(self):
        forms = {
            "nbsp-lead": "\xa0[decision] never run FLUSHALL on the shared node because it evicts sessions",
            "nbsp-after-bullet": "-\xa0[decision] x because y",
            "emsp-lead": "\u2003[decision] x because y",
            "vt-lead": "\x0b[ref] the shared node is redis-7",
            "ff-lead": "\x0c[ref] x",
            "u202f": "\u202f[exp] x",
            "u3000": "\u3000[exp] x",
            "bullet-h2": "- ## [decision] pin redis 7.2 because of the bug",
            "star-h3": "* ### [ref] x",
            "h2-nbsp": "##\xa0[decision] x",
            "h3-nbsp": "###\xa0[decision] x",
            "h3-bullet": "### - [exp] the heading text is read as a bullet entry",
            "u2028-h2": "- see below\u2028## [decision] use valkey over redis because licence",
        }
        for name, line in forms.items():
            body = "### Core\n" + line + "\n- trailing line"
            self.assertTrue(_indexer_tags(body), name)                          # the indexer reads it
            self.assertEqual(self.c.lint(body, {}), (False, "entry_line:2"), name)
        # The README line number of a validator finding is the "\n" line.
        text = ("# T\n\n## TL;DR\n\n> s\n\n" + BEGIN + "\n### Core\n- ok line\n" + forms["u2028-h2"]
                + "\n" + END + "\n")
        self.assertEqual(self.c.validate_issues(text, {}), ["card-entry-line:10"])

    def test_entry_check_is_linear(self):
        """CLAUDE.md rule 3 for the changed parser: ~2x per doubling."""
        shapes = {
            "sep-flood": lambda n: "\u2028" * n + "[decision] x",
            "ws-run": lambda n: "\xa0" * n + "[decision] x",
            "hash-bullet": lambda n: "#-" * (n // 2) + "[decision] x",
            "hash-run": lambda n: "#" * n + " [decision] x",
            "bracket-flood": lambda n: "- " + "[" * n,
            "sep-prose": lambda n: "ab\u2028" * (n // 3) + "[ref] x",
        }
        for name, make in shapes.items():
            times = []
            for n in (10_000, 20_000, 40_000):
                line = make(n)
                t = time.perf_counter()
                for _ in range(5):
                    self.c._is_entry_line(line)
                    self.c.lint("### Core\n" + line, {})
                times.append(time.perf_counter() - t)
            self.assertLess(times[-1], 2.0, (name, times))
            self.assertLess(times[2], max(times[1], 0.01) * 3, (name, times))
        # Positive signals: the scans parsed the 40k inputs.
        self.assertTrue(self.c._is_entry_line(shapes["ws-run"](40_000)))
        self.assertTrue(self.c._is_entry_line(shapes["sep-flood"](40_000)))
        self.assertTrue(self.c._is_entry_line(shapes["sep-prose"](40_000)))
        self.assertFalse(self.c._is_entry_line(shapes["hash-run"](40_000)))


class EntryLineCliTests(_Vault):
    def test_set_refuses_a_disguised_entry_line_and_the_index_never_tags_card_text(self):
        before = self.readme.read_bytes()
        for line in ("\xa0[decision] never run FLUSHALL on the shared node because it evicts sessions",
                     "- ## [decision] pin redis 7.2 because of the bug",
                     "- see below\u2028## [decision] use valkey over redis because licence",
                     "\x0b[ref] the shared node is redis-7"):
            self.assertEqual(self.set_card("### Core\n" + line + "\n- follow-up line"),
                             "rejected:entry_line:2", repr(line))
        self.assertEqual(self.readme.read_bytes(), before)
        # Positive control: the same CLI writes a clean card, and nothing in
        # the README is indexed as a typed entry.
        self.assertEqual(self.set_card(CARD_A), "written")
        self.run_py("_index.py", "--full", "--no-archive")
        db = sqlite3.connect(str(self.home / "index.db"))
        try:
            rows = db.execute("SELECT tag, content FROM chunks WHERE path LIKE '%redis-cache/00-README.md'").fetchall()
        finally:
            db.close()
        self.assertTrue(any("allkeys-lru" in c for _t, c in rows), rows)
        self.assertEqual([t for t, _c in rows if t], [])

    def test_validator_and_check_report_a_hand_written_disguised_line(self):
        text = self.readme.read_text().replace(
            f"> {TODO}\n", f"> Session cache\n\n{BEGIN}\n### Core\n- fine line\n"
                           "\xa0[decision] never run FLUSHALL because it evicts sessions\n"
                           "-\u2003##\u2003[ref] a disguised reference\n" + END + "\n")
        self.readme.write_text(text)
        lines = text.split("\n")
        nbsp = next(i for i, ln in enumerate(lines, 1) if ln.startswith("\xa0[decision]"))
        findings = self.scan()
        issues = next((f["issues"] for f in findings if f["file"].endswith("redis-cache/00-README.md")), [])
        self.assertEqual(issues, [f"card-entry-line:{nbsp}", f"card-entry-line:{nbsp + 1}"])
        row = self.check()["redis-cache"]
        self.assertEqual((row["status"], row["card"]), ("invalid", "invalid"))


# ── cards#2 ─────────────────────────────────────────────────────────────────

SUFFIXED = ("## TL;DR:", "## TL;DR — what this is", "## TL;DR (2026)")
HAND_SUMMARY = "> Hand-written summary the user curated."
CAVEAT = "Key caveat the user wrote down."


def _heading_lines(text: str) -> list:
    return [ln for ln in text.split("\n") if ln.startswith("## TL;DR")]


class TldrLocatorTests(_Vault):
    def _hand_readme(self, heading: str) -> str:
        return _readme(f"{heading}\n\n{HAND_SUMMARY}\n\n{CAVEAT}\n")

    def test_set_keeps_one_tldr_and_the_hand_written_prose_through_three_rebuilds(self):
        import _card  # type: ignore
        for heading in SUFFIXED:
            with self.subTest(heading=heading):
                self.readme.write_text(self._hand_readme(heading))
                row = self.check()["redis-cache"]
                self.assertEqual((row["status"], row["summary"]), ("none", "ok"))   # the summary is read
                self.assertEqual(self.set_card(CARD_A), "written")
                snaps = []
                for _ in range(3):
                    self.moc()
                    snaps.append(self.readme.read_text())
                text = snaps[-1]
                self.assertEqual(_heading_lines(text), [heading])
                self.assertEqual((text.count(HAND_SUMMARY), text.count(CAVEAT)), (1, 1))
                self.assertEqual(_card.card_state(text)[0], "ok")
                self.assertIn(f"{HAND_SUMMARY}\n\n<!-- gowth-mem:card v=1 updated={TODAY} -->", text)
                self.assertEqual(snaps[0], snaps[1])
                self.assertEqual(snaps[1], snaps[2])
                self.assertEqual(self.check()["redis-cache"]["status"], "ok")
                self.assertFalse((self.readme.parent / ".history").exists())
                # The same --set twice more: nothing moves.
                for _ in range(2):
                    self.assertEqual(self.set_card(CARD_A), "unchanged")
                    self.assertEqual(self.readme.read_text(), text)

    def test_summary_replaces_the_hand_written_one_in_place(self):
        for heading in SUFFIXED:
            with self.subTest(heading=heading):
                self.readme.write_text(self._hand_readme(heading))
                self.assertEqual(self.summary("New one-liner"), "written")
                for _ in range(3):
                    self.moc()
                text = self.readme.read_text()
                self.assertEqual(_heading_lines(text), [heading])
                self.assertIn(f"{heading}\n\n> New one-liner\n\n{CAVEAT}", text)
                self.assertNotIn(HAND_SUMMARY, text)            # replaced, as asked …
                self.assertEqual(text.count(CAVEAT), 1)          # … and nothing else lost
                self.assertEqual(self.summary("New one-liner"), "unchanged")

    def test_a_tldr_hit_that_is_no_heading_line_is_refused(self):
        variants = {
            "h3": _readme(f"### TL;DR\n\n{HAND_SUMMARY}\n"),
            "mid-line": _readme("Notes on the ## TL;DR convention we follow.\n"),
            "frontmatter": _readme("## Overview\n\nbody\n").replace(
                "title: Redis Cache\n", "title: Redis Cache\ndescription: see ## TL;DR below\n"),
            # A line START inside the frontmatter (a YAML block scalar) is no
            # heading either — even with a real TL;DR below it.
            "frontmatter-block": _readme(f"## TL;DR\n\n{HAND_SUMMARY}\n").replace(
                "status: draft\n", "status: draft\nnotes: |\n  ## TL;DR first, then the card\n"),
        }
        for name, text in variants.items():
            with self.subTest(variant=name):
                self.readme.write_text(text)
                self.assertEqual(self.set_card(CARD_A), "rejected:tldr_heading")
                self.assertEqual(self.summary("A summary"), "rejected:tldr_heading")
                self.assertEqual(self.readme.read_text(), text)
        # Positive control: a README with no "## TL;DR" anywhere gets one.
        self.readme.write_text(_readme("## Overview\n\nbody\n"))
        self.assertEqual(self.set_card(CARD_A), "written")
        self.assertEqual(_heading_lines(self.readme.read_text()), ["## TL;DR"])

    def test_validator_reads_a_suffixed_summary(self):
        self.readme.write_text(self._hand_readme("## TL;DR:"))
        issues = next(f["issues"] for f in self.scan("--cards") if f["file"].endswith("redis-cache/00-README.md"))
        self.assertEqual(issues, ["card-none"])                 # no card-todo-summary
        self.assertEqual(self.set_card(CARD_A), "written")
        self.assertEqual([f for f in self.scan("--cards") if f["file"].endswith("redis-cache/00-README.md")], [])

    def test_locator_is_linear(self):
        """CLAUDE.md rule 3 for the changed locator (`tldr_span` +
        `_frontmatter_end`, used by every card read/write and `--check`)."""
        import _card  # type: ignore
        shapes = {
            "fm-newline-run": lambda n: "---" + "\n" * n + "## TL;DR\nx\n",
            "fm-unclosed": lambda n: "---\n" + "a: b\n" * (n // 5) + "## TL;DR\nx\n",
            "fm-dash-lines": lambda n: "---\n" + "--\n" * (n // 3) + "## TL;DR\nx\n",
            "mid-line-run": lambda n: "x" * n + " ## TL;DR\n",
            "space-run": lambda n: " " * n + "## TL;DR\n" + "## y\n" * 10,
            "h2-flood": lambda n: "## TL;DR\n" + "## z\n" * (n // 5),
        }
        fns = (_card.tldr_span, _card.tldr_heading_bad, _card.card_state, _card.summary_text)
        for name, make in shapes.items():
            times = []
            for n in (10_000, 20_000, 40_000):
                text = make(n)
                t = time.perf_counter()
                for _ in range(5):
                    for fn in fns:
                        fn(text)
                times.append(time.perf_counter() - t)
            self.assertLess(times[-1], 1.0, (name, times))
            self.assertLess(times[2], max(times[1], 0.01) * 3, (name, times))
        # Positive signals: the scans parsed the 40k inputs.
        text = shapes["fm-newline-run"](40_000)
        self.assertEqual(_card.tldr_span(text), (3 + 40_000, len(text)))
        self.assertIsNone(_card.tldr_span(shapes["mid-line-run"](40_000)))
        self.assertTrue(_card.tldr_heading_bad(shapes["mid-line-run"](40_000)))
        self.assertEqual(_card.tldr_span(shapes["space-run"](40_000)), (0, 40_000 + 9))

    def test_locator_unit(self):
        import _card  # type: ignore
        fm_hit = "---\ndescription: |\n  ## TL;DR\n---\n# T\n## TL;DR\nx\n"
        for text, want in (("# T\n## TL;DR:\nx\n## Next\n", (4, 16)), ("  ## TL;DR\nx", (0, 12)),
                           ("## TL;DR — a\n", (0, 13)), ("### TL;DR\n", None), ("a ## TL;DR\n", None),
                           ("no heading\n", None), (fm_hit, None),
                           ("---\nslug: t\n---\n## TL;DR\nx\n## N\n", (16, 27)),
                           ("---\n---\n## TL;DR\n", (8, 17))):    # `---\n---` is no frontmatter (`_FRONT_RE`)
            self.assertEqual(_card.tldr_span(text), want, text)
        self.assertTrue(_card.tldr_heading_bad("### TL;DR\n"))
        self.assertTrue(_card.tldr_heading_bad(fm_hit))
        self.assertFalse(_card.tldr_heading_bad("## TL;DR:\n"))
        self.assertFalse(_card.tldr_heading_bad("nothing\n"))
        from _frontmatter import _FRONT_RE  # type: ignore
        for text in (fm_hit, "---\nslug: t\n---\n## TL;DR\n", "---\n---\n## TL;DR\n", "--- x\n---\n", "x\n"):
            m = _FRONT_RE.match(text)
            self.assertEqual(_card._frontmatter_end(text), len(m.group(0).rstrip("\n")) if m else 0, text)


# ── cards#4 ─────────────────────────────────────────────────────────────────

CARD_LINES = f"{BEGIN}\n### Core\n- curated truth line\n### Sources\n- runbook.md:12\n{END}"
DROPPED = {
    # A hand-written H2 between the TL;DR summary and the card.
    "h2-above-card": _readme(f"## TL;DR\n\n> s\n\n## Context\n\nhand-written context\n\n{CARD_LINES}\n"),
    # The TL;DR heading renamed: the rebuild writes a skeleton TL;DR.
    "renamed-tldr": _readme(f"## Summary\n\n> s\n\n{CARD_LINES}\n"),
    # The card under the section the rebuild regenerates.
    "under-aspects": _readme("## TL;DR\n\n> s\n").replace(
        "## Aspects (auto)\n\n(empty)\n", f"## Aspects (auto)\n\n{CARD_LINES}\n\n(empty)\n"),
}
KEPT = {
    "ok": _readme(f"## TL;DR\n\n> s\n\n{CARD_LINES}\n"),
    "unbalanced-no-end": _readme(f"## TL;DR\n\n> s\n\n{BEGIN}\n### Core\n- curated truth line\n"),
    "duplicate": _readme(f"## TL;DR\n\n> s\n\n{CARD_LINES}\n{CARD_LINES.replace('curated', 'second')}\n"),
    "after-crosslinks": _readme("## TL;DR\n\n> s\n") + f"\n{CARD_LINES}\n",
    "suffixed-heading": _readme(f"## TL;DR — t\n\n> s\n\n{CARD_LINES}\n"),
    "h2-inside-card": _readme(f"## TL;DR\n\n> s\n\n{CARD_LINES.replace('### Sources', '## Sources (hand)')}\n"),
}


class MocArchiveTests(_Vault):
    def history(self) -> list:
        d = self.readme.parent / ".history"
        return sorted(d.iterdir()) if d.is_dir() else []

    def assert_archived(self, name: str) -> None:
        hist = self.history()
        self.assertEqual(len(hist), 1, (name, hist))
        arch = hist[0].read_text()
        self.assertTrue(arch.startswith("# verbatim archive (moc-rebuild) of workspaces/w1/redis-cache/00-README.md\n"),
                        arch)
        self.assertIn(CARD_LINES, arch)                      # begin marker … end marker, verbatim
        self.assertRegex(hist[0].name, r"^card-[0-9a-f]{12}\.txt$")

    def test_a_card_the_rebuild_would_drop_is_archived_first(self):
        for name, text in DROPPED.items():
            with self.subTest(case=name):
                shutil.rmtree(self.readme.parent / ".history", ignore_errors=True)
                self.readme.write_text(text)
                self.assertEqual(self.check()["redis-cache"]["issues"], ["card-outside-tldr"])
                snaps = []
                for _ in range(3):
                    r = self.moc()
                    self.assertIn("topic:w1:redis-cache", r.stdout)     # the rebuild really ran
                    snaps.append(self.readme.read_bytes())
                self.assert_archived(name)
                self.assertNotIn(b"curated truth line", snaps[0])   # the rebuild did drop it …
                self.assertEqual(snaps[0], snaps[1])                 # … once, then idempotent
                self.assertEqual(snaps[1], snaps[2])
                self.assertEqual(len(self.history()), 1)

    def test_every_dropped_card_lands_in_one_archive_in_readme_order(self):
        second = CARD_LINES.replace("curated truth line", "second card line")
        text = DROPPED["h2-above-card"].replace(
            "## Aspects (auto)\n\n(empty)\n", f"## Aspects (auto)\n\n{second}\n\n(empty)\n")
        self.readme.write_text(text)
        self.moc()
        hist = self.history()
        self.assertEqual(len(hist), 1, hist)
        arch = hist[0].read_text()
        self.assertIn(f"{CARD_LINES}\n{second}\n", arch)            # both, verbatim, in order
        out = self.readme.read_text()
        self.assertNotIn("curated truth line", out)
        self.assertNotIn("second card line", out)

    def test_a_lesson_on_another_topic_archives_it_too(self):
        _w(self.wsd / "other" / "00-README.md", _skeleton("other", "Other"))
        for name, text in DROPPED.items():
            with self.subTest(case=name):
                shutil.rmtree(self.readme.parent / ".history", ignore_errors=True)
                self.readme.write_text(text)
                r = self.run_py("_lesson.py", "--ws", "w1", "--topic", "other",
                                "--symptom", f"probe lesson {name} for the rebuild",
                                "--tried", "nothing", "--root", "a rebuild ran because a lesson landed",
                                "--fix", "none", "--source", "runbook.md:12")
                self.assertIn("appended:", r.stdout)
                self.assertIn(f"topic:w1:redis-cache", r.stdout)        # its MOC spawn rebuilt this README
                self.assert_archived(name)
                self.assertNotIn("curated truth line", self.readme.read_text())

    def test_when_archiving_fails_nothing_is_written(self):
        outside = Path(tempfile.mkdtemp(prefix="gowth_hist_"))
        try:
            (self.readme.parent / ".history").symlink_to(outside, target_is_directory=True)
            for name, text in DROPPED.items():
                with self.subTest(case=name):
                    self.readme.write_text(text)
                    r = self.moc()
                    self.assertEqual(self.readme.read_text(), text)
                    self.assertIn("could not be archived", r.stderr)
                    self.assertNotIn("topic:w1:redis-cache", r.stdout)
                    self.assertEqual(self.check()["redis-cache"]["issues"], ["card-outside-tldr"])
            self.assertEqual(list(outside.iterdir()), [])
            # Positive control: with a real .history the same rebuild archives and writes.
            (self.readme.parent / ".history").unlink()
            self.moc()
            self.assert_archived("after-unlink")
            self.assertNotEqual(self.readme.read_text(), list(DROPPED.values())[-1])
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_cards_the_rebuild_keeps_are_never_archived_nor_frozen(self):
        import _card  # type: ignore
        for name, text in KEPT.items():
            with self.subTest(case=name):
                self.readme.write_text(text)
                state = _card.card_state(text)[0]
                aspect = _w(self.readme.parent / f"2026-11-0{len(name) % 9 + 1}-{name}.md",
                            f"---\n---\n- [exp] probe {name}\n")
                for _ in range(3):
                    self.moc()
                out = self.readme.read_text()
                self.assertEqual(self.history(), [], name)
                self.assertIn(aspect.stem, out)                      # the Aspects list was rebuilt
                self.assertIn("- curated truth line", out)           # the card body kept
                self.assertEqual(_card.card_state(out)[0], state)
                aspect.unlink()

    def test_card_spans_and_the_rebuild_are_linear(self):
        import _card  # type: ignore
        import _moc  # type: ignore
        saved = os.environ.get("GOWTH_MEM_HOME")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        try:
            shapes = {
                "begin-flood": lambda n: _readme("## TL;DR\n\n> s\n\n" + f"{BEGIN}\n" * (n // 48)),
                "begin-h2": lambda n: _readme("## TL;DR\n\n> s\n\n" + f"{BEGIN}\n## x\n" * (n // 53)),
                "outside-big": lambda n: _readme("## TL;DR\n\n> s\n\n## Context\n\n" + f"{BEGIN}\n"
                                                 + "- line of the card\n" * (n // 19) + f"{END}\n"),
                "end-flood": lambda n: _readme("## TL;DR\n\n> s\n\n" + f"{END}\n" * (n // 25)),
                # Many DISTINCT corrupt cards in a regenerated section: one
                # archive per rebuild, not one file (or one scan) per card.
                "many-dropped": lambda n: _readme("## TL;DR\n\n> s\n").replace(
                    "## Aspects (auto)\n\n(empty)\n", "## Aspects (auto)\n\n" + "".join(
                        f"<!-- gowth-mem:card v=1 n={i:05d} -->\n- body {i:05d}\n" for i in range(n // 48))),
            }
            for name, make in shapes.items():
                times = []
                for n in (10_000, 20_000, 40_000):
                    folder = self.wsd / f"g-{name}-{n}"
                    _w(folder / "00-README.md", make(n))
                    t = time.perf_counter()
                    _card.card_spans(make(n))
                    self.assertIsNotNone(_moc.rebuild_topic_readme(folder))
                    times.append(time.perf_counter() - t)
                self.assertLess(times[-1], 3.0, (name, times))
                self.assertLess(times[2], max(times[1], 0.02) * 3, (name, times))
            # Positive signals: the spans were found and the big outside card archived.
            self.assertEqual(len(_card.card_spans(shapes["begin-h2"](40_000))), 40_000 // 53)
            hist = list((self.wsd / "g-outside-big-40000" / ".history").iterdir())
            self.assertEqual(len(hist), 1)
            self.assertEqual(hist[0].read_text().count("- line of the card"), 40_000 // 19)
            hist = list((self.wsd / "g-many-dropped-40000" / ".history").iterdir())
            self.assertEqual(len(hist), 1)
            self.assertEqual(hist[0].read_text().count("<!-- gowth-mem:card v=1 n="), 40_000 // 48)
            self.assertFalse((self.wsd / "g-begin-flood-40000" / ".history").exists())   # kept in the TL;DR
        finally:
            if saved is None:
                os.environ.pop("GOWTH_MEM_HOME", None)
            else:
                os.environ["GOWTH_MEM_HOME"] = saved


# ── gaps-docs#3, card half ──────────────────────────────────────────────────

class PrivateTagTests(_Vault):
    def test_lint_refuses_a_private_tag_in_any_spelling(self):
        import _card  # type: ignore
        for tag in ("<private>", "</private>", "<PRIVATE>", "<prıvate>", "</PRİVATE>", "<prİvate>"):
            self.assertEqual(_card.lint(f"### Core\n- capture strips {tag} blocks", {}), (False, "private_tag"), tag)
            self.assertEqual(_card.lint_summary(f"Capture strips {tag} blocks", {})[:2], (False, "private_tag"), tag)
        # A closed block is a private tag too, not a secret — the clearer reason.
        self.assertEqual(_card.lint("### Core\n- a <private>x</private> b", {}), (False, "private_tag"))
        self.assertEqual(_card.lint("### Core\n- the private-block filter", {}), (True, ""))   # control

    def test_summary_then_set_with_split_tags_never_corrupt_the_card(self):
        import _card  # type: ignore
        self.assertEqual(self.set_card(CARD_A), "written")
        self.assertEqual(self.summary("Session cache for the API tier"), "written")
        before = _tree(self.home)
        self.assertEqual(self.summary("Capture redacts everything wrapped in <private> tags before sync."),
                         "rejected:private_tag")
        self.assertEqual(self.set_card(CARD_A + "\n- the closing </private> tag is matched too"),
                         "rejected:private_tag")
        self.assertEqual(self.summary("Capture redacts <prıvate> spans"), "rejected:private_tag")
        self.assertEqual(self.set_card(CARD_A + "\n- closing </PRİVATE> spelling"), "rejected:private_tag")
        self.assertEqual(_tree(self.home), before)
        text = self.readme.read_text()
        self.assertEqual(_card.card_state(text)[0], "ok")
        self.assertEqual(self.check()["redis-cache"]["status"], "ok")
        self.assertIn("## Cross-links (manual)", text)

    def test_fail_closed_catches_split_tags_even_past_the_lint(self):
        """Defence in depth: the writers themselves (lint bypassed) refuse a
        write whose whole-file sanitize would pair a `<private>` from one
        write with a `</private>` from the next — the begin marker survives."""
        import _card  # type: ignore
        saved = os.environ.get("GOWTH_MEM_HOME")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        skeleton = self.readme.read_bytes()
        try:
            # Summary first: an opener alone redacts nothing, so it lands …
            self.assertEqual(_card._apply_summary(self.readme, "capture drops <prıvate> spans"), "written")
            before = self.readme.read_bytes()
            # … and the card holding the closer would pair with it: refused.
            self.assertEqual(_card._apply_card(self.readme, CARD_B + "\n- the closing </private> tag", TODAY),
                             "rejected:secret_leak")
            self.assertEqual(self.readme.read_bytes(), before)
            # Card first (insert, then replace): the closer below lands alone …
            self.readme.write_bytes(skeleton)
            self.assertEqual(_card._apply_card(self.readme, CARD_A + "\n- the closing </PRİVATE> tag", TODAY),
                             "written")
            before = self.readme.read_bytes()
            # … and a summary opener above it would pair with it: refused.
            self.assertEqual(_card._apply_summary(self.readme, "capture drops <private> spans"),
                             "rejected:secret_leak")
            self.assertEqual(self.readme.read_bytes(), before)
            text = self.readme.read_text()
            self.assertEqual(_card.card_state(text)[0], "ok")
            self.assertIn("allkeys-lru", text)
            self.assertFalse((self.readme.parent / ".history").exists())   # refused before any archive
        finally:
            if saved is None:
                os.environ.pop("GOWTH_MEM_HOME", None)
            else:
                os.environ["GOWTH_MEM_HOME"] = saved

    def test_a_readme_the_write_would_redact_refuses_the_write(self):
        base = self.readme.read_text()
        hand_edits = {
            # Half a tag in the TL;DR prose, the other half in the manual section.
            "split-private": base.replace(f"> {TODO}\n", f"> {TODO}\n\nCapture drops a <private> tag.\n")
                                 .replace("## Cross-links (manual)\n", "## Cross-links (manual)\n\n"
                                                                       "- the closing </private> tag\n"),
            # A raw secret pasted into the manual section.
            "secret": base.replace("## Cross-links (manual)\n",
                                   "## Cross-links (manual)\n\n- key AKIAIOSFODNN7EXAMPLF\n"),
        }
        for name, text in hand_edits.items():
            with self.subTest(edit=name):
                self.readme.write_text(text)
                r = self.run_py("_card.py", "--ws", "w1", "--topic", "redis-cache", "--set", "-", stdin=CARD_A)
                self.assertEqual(r.stdout.strip().split("\t")[1], "rejected:secret_leak")
                self.assertNotIn("redacted", r.stderr)               # safe_write never ran
                self.assertEqual(self.summary("Session cache for the API tier"), "rejected:secret_leak")
                self.assertEqual(self.readme.read_text(), text)
                self.assertFalse((self.readme.parent / ".history").exists())
        # Positive control: once the hand edit is gone, the same writes land.
        self.readme.write_text(base)
        self.assertEqual(self.set_card(CARD_A), "written")
        self.assertEqual(self.summary("Session cache for the API tier"), "written")

    def test_a_replace_and_a_confirm_are_refused_too(self):
        import _card  # type: ignore
        self.assertEqual(self.set_card(CARD_A), "written")
        carded = self.readme.read_text()
        # Hand edit: an older `updated=` (so the same body would be a confirm)
        # plus a raw secret in the manual section.
        edited = (carded.replace(f"updated={TODAY}", "updated=2025-01-01")
                  .replace("## Cross-links (manual)\n", "## Cross-links (manual)\n\n- token ghp_" + "a" * 36 + "\n"))
        self.readme.write_text(edited)
        self.assertEqual(self.set_card(CARD_A), "rejected:secret_leak")                       # confirm path
        self.assertEqual(self.set_card(CARD_A.replace("allkeys", "volatile")), "rejected:secret_leak")  # replace
        self.assertEqual(self.readme.read_text(), edited)
        self.assertFalse((self.readme.parent / ".history").exists())   # refused before the archive
        self.assertEqual(_card.card_state(edited)[0], "ok")


class HeadRefusalReplayTests(_Vault):
    """CLAUDE.md: a new branch in front of an existing guard ships with a
    replay of HEAD's refusals through the new entry point. The whole-README
    sanitize check now runs before `_archive` (replace path) and the
    `tldr_heading` check before `_with_tldr`; HEAD's refusals must still win."""

    def test_archive_failed_unreadable_and_corrupt_markers_still_refuse(self):
        self.assertEqual(self.set_card(CARD_A), "written")
        carded = self.readme.read_bytes()
        outside = Path(tempfile.mkdtemp(prefix="gowth_hist_"))
        hist = self.readme.parent / ".history"
        try:
            hist.symlink_to(outside, target_is_directory=True)
            self.assertEqual(self.set_card(CARD_A.replace("allkeys", "volatile")), "rejected:archive_failed")
            self.assertEqual(self.readme.read_bytes(), carded)
            self.assertEqual(list(outside.iterdir()), [])
        finally:
            if hist.is_symlink():
                hist.unlink()
            shutil.rmtree(outside, ignore_errors=True)
        # A README that is no valid UTF-8 is never decoded lossily and written back.
        self.readme.write_bytes(carded + b"\n- caf\xe9 note\n")
        broken = self.readme.read_bytes()
        self.assertEqual(self.set_card(CARD_B), "rejected:unreadable")
        self.assertEqual(self.summary("Another summary"), "rejected:unreadable")
        self.assertEqual(self.readme.read_bytes(), broken)
        # Corrupt markers win over the new TL;DR check: two cards under a
        # mid-line "## TL;DR" are `markers_corrupt`, not `tldr_heading`.
        text = carded.decode("utf-8").replace("# Redis Cache\n", "# Redis Cache\n\nsee ## TL;DR\n")
        text = text.replace(END + "\n", END + "\n" + BEGIN + "\n- b\n" + END + "\n")
        self.readme.write_text(text)
        self.assertEqual(self.set_card(CARD_B), "rejected:markers_corrupt")
        self.assertEqual(self.summary("Another summary"), "rejected:markers_corrupt")
        self.assertEqual(self.readme.read_text(), text)
        # Positive control: the clean README takes the same writes.
        self.readme.write_bytes(carded)
        self.assertEqual(self.set_card(CARD_B), "written")
        self.assertEqual(self.summary("Another summary"), "written")
        self.assertEqual(len(list(hist.iterdir())), 1)                       # CARD_A archived


class DocsTests(unittest.TestCase):
    def test_new_statuses_and_the_repair_are_documented(self):
        topic = (ROOT / "commands" / "mem-topic.md").read_text()
        for word in ("tldr_heading", "private_tag", "moc-rebuild", "card-outside-tldr"):
            self.assertIn(word, topic)
        validate = (ROOT / "templates" / "ops" / "validate.md").read_text()
        for word in ("card-outside-tldr", "card-markers", "card-entry-line", ".history/card-"):
            self.assertIn(word, validate)
        spec = (ROOT / "docs" / "superpowers" / "specs" / "2026-11-07-v4.9-curator-design.md").read_text()
        for word in ("tldr_heading", "private_tag", "moc-rebuild", "splitlines()"):
            self.assertIn(word, spec)


if __name__ == "__main__":
    unittest.main()
