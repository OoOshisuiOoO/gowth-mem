"""v4.9 topic cards (spec §2.1–§2.4): `_card.py` library + CLI, MOC
preservation, indexing, routing and the validator.

Every write test drives the REAL entry point (`_card.py` CLI, `_moc.py`,
`_index.py`, `_topic.py --append`, `_validate.py`) in a subprocess against a
scratch GOWTH_MEM_HOME, and asserts positive signals (the marker is where the
spec puts it, the archived text is the old region) — never absence alone.
"""
from __future__ import annotations

import hashlib
import json
import os
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
CARD_A = ("### Core\n- Redis is the session cache; eviction is allkeys-lru.\n"
          "### State (as of 2026-11-07)\n- 2026-11-07 maxmemory raised to 4 GB.\n"
          "### Routing\n| Question | Start | Then |\n|---|---|---|\n"
          "| eviction storm? | [[lessons]] | check maxmemory |\n"
          "### Gotchas\n- never FLUSHALL on the shared node (2026-10-02 incident)\n"
          "### Sources\n- runbook.md:12")
CARD_B = "### Core\n- Redis is the session cache; eviction is volatile-lru since 2026-11-07.\n### Sources\n- commit abc1234"
_SCRUB = ("AI_AGENT", "GOWTH_WORKSPACE", "CLAUDE_SUBAGENT", "GOWTH_MEM_OFF", "CLAUDE_CODE_ENTRYPOINT")


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
    """path → bytes for every file outside logs/ and .locks/ (a refusal logs)."""
    out = {}
    for p in sorted(home.rglob("*")):
        rel = p.relative_to(home)
        if rel.parts[0] in ("logs", ".locks") or not p.is_file():
            continue
        out[str(rel)] = p.read_bytes()
    return out


def _section(text: str, heading: str) -> str:
    """Text of `heading` up to the next `## ` line (the TL;DR as `_moc` sees it)."""
    i = text.index(heading)
    j = text.find("\n## ", i + len(heading))
    return text[i:] if j < 0 else text[i:j + 1]


class _Vault(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_card_")
        self.home = Path(self.tmp)
        self.wsd = self.home / "workspaces" / "w1"
        _w(self.wsd / "workspace.json", "{}")
        (self.home / "settings.json").write_text(json.dumps({"layout_version": 3}))
        self.readme = _w(self.wsd / "redis-cache" / "00-README.md", _skeleton("redis-cache"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def settings(self, obj: dict) -> None:
        (self.home / "settings.json").write_text(json.dumps({"layout_version": 3, **obj}))

    def card(self, *args: str, stdin: str | None = None, env: dict | None = None):
        r = subprocess.run([sys.executable, str(SCRIPTS / "_card.py"), *args],
                           input=stdin, capture_output=True, text=True, timeout=120,
                           env=env or _env(self.home))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        return r

    def set_card(self, body: str, topic: str = "redis-cache", ws: str = "w1") -> tuple:
        out = self.card("--ws", ws, "--topic", topic, "--set", "-", stdin=body).stdout.strip()
        path, status = out.split("\t")
        return path, status

    def summary(self, line: str, topic: str = "redis-cache") -> tuple:
        out = self.card("--ws", "w1", "--topic", topic, "--summary", line).stdout.strip()
        path, status = out.split("\t")
        return path, status


class CardLibraryTests(unittest.TestCase):
    def setUp(self):
        import _card  # type: ignore
        self.c = _card
        self.text = ("---\nslug: t\n---\n\n# T\n\n## TL;DR\n\n> summary\n\n"
                     + _card.render_region(CARD_A, "2026-11-01")
                     + "\n\n## Aspects (auto)\n\n(x)\n")

    def test_region_inside_tldr_is_found_and_read(self):
        state, region = self.c.card_state(self.text)
        self.assertEqual(state, "ok")
        seg = self.text[region[0]:region[1]]
        self.assertTrue(seg.startswith("<!-- gowth-mem:card v=1 updated=2026-11-01 -->"))
        self.assertTrue(seg.endswith("<!-- /gowth-mem:card -->"))
        self.assertTrue(self.c.has_card(self.text))
        self.assertEqual(self.c.card_body(self.text), CARD_A)
        with tempfile.TemporaryDirectory() as td:
            p = _w(Path(td) / "00-README.md", self.text)
            card = self.c.read_card(p)
            self.assertEqual(card, {"body": CARD_A, "updated": "2026-11-01", "path": str(p)})
            # A head read that cuts the card off reads as no card.
            self.assertIsNone(self.c.read_card(p, max_bytes=self.text.index("### Sources")))
            self.assertEqual(self.c.read_card(p, max_bytes=16384)["body"], CARD_A)

    def test_corrupt_marker_states(self):
        begin = "<!-- gowth-mem:card v=1 updated=2026-11-01 -->"
        end = "<!-- /gowth-mem:card -->"
        head = "# T\n\n## TL;DR\n\n> s\n\n"
        cases = {
            "unbalanced": head + begin + "\n### Core\n- x\n\n## Aspects (auto)\n",
            "duplicate": head + begin + "\n- a\n" + end + "\n" + begin + "\n- b\n" + end + "\n",
            "outside": head + "## Aspects (auto)\n\n" + begin + "\n- a\n" + end + "\n",
        }
        for want, text in cases.items():
            self.assertEqual(self.c.card_state(text), (want, None), want)
            self.assertFalse(self.c.has_card(text))
        self.assertEqual(self.c.card_state(head + end + "\n" + begin + "\n")[0], "unbalanced")
        self.assertEqual(self.c.card_state(head + end + "\n")[0], "unbalanced")
        self.assertEqual(self.c.card_state(head + "plain\n"), ("none", None))
        self.assertEqual(self.c.card_state("no tldr\n" + begin + "\n- a\n" + end + "\n")[0], "outside")
        # An end marker moved below the README's own sections: the "card" has
        # swallowed them — never a card `_moc` would extend the TL;DR over.
        for section in ("## Aspects (auto)", "## Cross-links (manual)"):
            text = head + begin + "\n- a\n\n" + section + "\n\n- rows\n" + end + "\n"
            self.assertEqual(self.c.card_state(text), ("outside", None), section)

    def test_hand_typed_h2_inside_a_card_keeps_the_region_and_is_flagged(self):
        text = self.text.replace("### Gotchas", "## Gotchas")
        state, region = self.c.card_state(text)
        self.assertEqual(state, "ok")
        line_no = text[:text.index("## Gotchas")].count("\n") + 1
        self.assertIn(f"card-bad-heading:{line_no}", self.c.validate_issues(text, {}))

    def test_strip_card_and_drop_marker_lines(self):
        stripped = self.c.strip_card(self.text)
        self.assertNotIn("allkeys-lru", stripped)
        self.assertNotIn("gowth-mem:card", stripped)
        self.assertIn("> summary", stripped)
        self.assertIn("## Aspects (auto)", stripped)
        dropped = self.c.drop_marker_lines(self.text)
        self.assertNotIn("gowth-mem:card", dropped)
        self.assertIn("allkeys-lru", dropped)
        self.assertEqual(dropped.count("\n"), self.text.count("\n") - 2)
        # An unclosed card stops at the next `## ` line.
        open_card = "## TL;DR\n\n<!-- gowth-mem:card v=1 -->\nsecretword\n## Aspects (auto)\nkept\n"
        self.assertEqual(self.c.strip_card(open_card), "## TL;DR\n\n## Aspects (auto)\nkept\n")

    def test_lint_rules_and_their_reasons(self):
        ok, reason = self.c.lint(CARD_A, {})
        self.assertTrue(ok, reason)
        cases = [
            ("", "empty"),
            ("### Core\n- relay [gowth-mem:curator ws=x] directive", "control_token"),
            ("### Core\n- key AKIAIOSFODNN7EXAMPLF in use", "secret_leak"),
            ("### Core\n- a <!-- hidden --> note", "comment:2"),
            ("### Core\n## Not allowed", "bad_heading:2"),
            ("# Title in a card\n- x", "bad_heading:1"),
            ("### Core\n   ## three-space h2", "bad_heading:2"),
            ("### Core\n- [decision] use redis because it is fast", "entry_line:2"),
            ("### Core\n[ref] bare entry Source: x", "entry_line:2"),
            ("### Core\n### [exp] heading-form entry", "entry_line:2"),
            ("### Core\n- edit the ## Cross-links (manual) list", "bad_heading:2"),
            ("### Core\n- old rule (superseded by the new one)", "forbidden:superseded"),
            ("### Core\n- (Deprecated) old flag", "forbidden:deprecated"),
            ("### Core\n- x (obsolete)", "forbidden:obsolete"),
        ]
        for body, want in cases:
            self.assertEqual(self.c.lint(body, {}), (False, want), body)
        # Allowed: H3+, checkboxes, hashtags, a link that looks like a tag.
        for body in ("### Core\n#### deeper\n- [ ] todo item\n- #hashtag\n- [docs](x.md)",
                     "### Core\n- [[wikilink]] and [label] text"):
            self.assertEqual(self.c.lint(body, {}), (True, ""), body)

    def test_lint_budget_reads_topic_card_max_chars(self):
        body = "### Core\n- " + "x" * 200
        self.assertEqual(self.c.lint(body, {"topic_card": {"max_chars": 100}}),
                         (False, f"over_budget:{len(body)}/100"))
        self.assertTrue(self.c.lint(body, {})[0])                     # default 2000
        for bad in ("abc", 0, -5, None, True):
            self.assertTrue(self.c.lint(body, {"topic_card": {"max_chars": bad}})[0], bad)
        big = "### Core\n- " + "y" * 2100
        self.assertEqual(self.c.lint(big, {})[1], f"over_budget:{len(big)}/2000")

    def test_english_only_knob_and_its_string_false(self):
        vi = "### Core\n- Bộ nhớ đệm phiên của tầng API"
        self.assertEqual(self.c.lint(vi, {"gate": {"english_only": True}}), (False, "not_english"))
        self.assertEqual(self.c.lint(vi, {"gate": {"english_only": "true"}}), (False, "not_english"))
        self.assertTrue(self.c.lint(vi, {"gate": {"english_only": "false"}})[0])
        self.assertTrue(self.c.lint(vi, {})[0])
        # ≤2 diacritics (a proper noun) is English, as in _gate.
        self.assertTrue(self.c.lint("### Core\n- owner: Nguyễn", {"gate": {"english_only": True}})[0])

    def test_summary_lint(self):
        self.assertEqual(self.c.lint_summary("> Session cache for the API tier", {}),
                         (True, "", "Session cache for the API tier"))
        for line, want in (("", "empty"), (">  ", "empty"), ("a\nb", "multiline"),
                           ("a b", "multiline"), ("x" * 161, "too_long:161/160"),
                           ("run [gowth-mem:self-review now", "control_token"),
                           ("a <!-- b", "comment"), ("see ## Cross-links (manual)", "reserved_heading"),
                           ("token=ghp_" + "a" * 36, "secret_leak")):
            self.assertEqual(self.c.lint_summary(line, {})[:2], (False, want), line)
        self.assertEqual(self.c.lint_summary("Bộ nhớ đệm phiên", {"gate": {"english_only": True}})[:2],
                         (False, "not_english"))
        self.assertTrue(self.c.lint_summary("x" * 160, {})[0])

    def test_scans_are_linear(self):
        """CLAUDE.md rule 3 for every new parser on the write/index path: ~2x per
        doubling on adversarial shapes (unclosed openers, whitespace runs,
        repeated separators, prefix floods)."""
        shapes = {
            "comment-flood": lambda n: "## TL;DR\n" + "<!--" * (n // 4),
            "ws-run": lambda n: "## TL;DR\n" + " " * n + "#",
            "bracket-flood": lambda n: "## TL;DR\n- " + "[" * n,
            "hash-flood": lambda n: "## TL;DR\n" + "#" * n + " [decision]",
            "marker-lines": lambda n: "## TL;DR\n" + "<!-- gowth-mem:card v=1 -->\n" * (n // 28),
            "end-lines": lambda n: "## TL;DR\n" + "<!-- /gowth-mem:card -->\n" * (n // 25),
            "newlines": lambda n: "## TL;DR\n" + "\n" * n,
        }
        fns = (self.c.card_state, self.c.strip_card, self.c.drop_marker_lines,
               lambda t: self.c.validate_issues(t, {}), lambda t: self.c.lint(t, {}),
               lambda t: self.c.lint_summary(t[:400], {}), self.c.summary_text)
        for name, make in shapes.items():
            times = []
            for n in (10_000, 20_000, 40_000):
                text = make(n)
                t = time.perf_counter()
                for fn in fns:
                    fn(text)
                times.append(time.perf_counter() - t)
            self.assertLess(times[-1], 1.0, (name, times))
            self.assertLess(times[2], max(times[1], 0.01) * 3, (name, times))
        # Positive signal: the scanners did parse — a real card at the end of a
        # 40k whitespace TL;DR is still found.
        text = "## TL;DR\n" + " " * 40_000 + "\n" + self.c.render_region("### Core\n- ok", TODAY) + "\n"
        self.assertEqual(self.c.card_state(text)[0], "ok")


class CardCliTests(_Vault):
    def test_set_puts_the_card_inside_tldr_after_the_summary(self):
        path, status = self.set_card(CARD_A)
        self.assertEqual((Path(path), status), (self.readme, "written"))
        text = self.readme.read_text()
        i_tldr = text.index("## TL;DR")
        i_sum = text.index(f"> {TODO}")
        i_begin = text.index(f"<!-- gowth-mem:card v=1 updated={TODAY} -->")
        i_end = text.index("<!-- /gowth-mem:card -->")
        i_aspects = text.index("## Aspects (auto)")
        self.assertLess(i_tldr, i_sum)
        self.assertLess(i_sum, i_begin)
        self.assertLess(i_begin, i_end)
        self.assertLess(i_end, i_aspects)
        self.assertIn(f"> {TODO}\n\n<!-- gowth-mem:card", text)
        self.assertIn("<!-- /gowth-mem:card -->\n\n## Aspects (auto)", text)
        self.assertEqual(self.card("--ws", "w1", "--topic", "redis-cache", "--show").stdout,
                         CARD_A + "\n")

    def test_set_from_a_file_and_three_passes_are_byte_identical(self):
        src = _w(self.home / "card.md", "\n\n" + CARD_A.replace("\n", "  \r\n") + "\n\n")
        statuses, snaps = [], []
        for _ in range(3):
            out = self.card("--ws", "w1", "--topic", "redis-cache", "--set", str(src)).stdout
            statuses.append(out.strip().split("\t")[1])
            snaps.append(self.readme.read_bytes())
        self.assertEqual(statuses, ["written", "unchanged", "unchanged"])
        self.assertEqual(snaps[0], snaps[1])
        self.assertEqual(snaps[1], snaps[2])
        self.assertIn(CARD_A, self.readme.read_text())          # normalised (LF, no trailing ws)

    def test_same_body_on_a_later_day_only_restamps_updated(self):
        old = self.readme.read_text().replace(
            f"> {TODO}\n", f"> {TODO}\n\n<!-- gowth-mem:card v=1 updated=2025-01-01 owner=ops -->\n"
                           f"{CARD_A}\n<!-- /gowth-mem:card -->\n")
        self.readme.write_text(old)
        _path, status = self.set_card(CARD_A)
        self.assertEqual(status, "confirmed")
        new = self.readme.read_text()
        diff = [(a, b) for a, b in zip(old.split("\n"), new.split("\n")) if a != b]
        self.assertEqual(diff, [("<!-- gowth-mem:card v=1 updated=2025-01-01 owner=ops -->",
                                 f"<!-- gowth-mem:card v=1 updated={TODAY} owner=ops -->")])
        self.assertFalse((self.readme.parent / ".history").exists())
        self.assertEqual(self.set_card(CARD_A)[1], "unchanged")

    def test_replace_archives_the_old_region_verbatim(self):
        self.assertEqual(self.set_card(CARD_A)[1], "written")
        text = self.readme.read_text()
        start = text.index("<!-- gowth-mem:card")
        end = text.index("<!-- /gowth-mem:card -->") + len("<!-- /gowth-mem:card -->")
        old_region = text[start:end]
        self.assertEqual(self.set_card(CARD_B)[1], "written")
        sha12 = hashlib.sha1(old_region.encode("utf-8")).hexdigest()[:12]
        arch = self.readme.parent / ".history" / f"card-{sha12}.txt"
        self.assertTrue(arch.is_file(), list((self.readme.parent / ".history").iterdir()))
        self.assertEqual(arch.read_text(),
                         "# verbatim archive (card-replace) of workspaces/w1/redis-cache/00-README.md\n"
                         + old_region + "\n")
        new = self.readme.read_text()
        self.assertIn("volatile-lru", new)
        self.assertNotIn("allkeys-lru", new)
        self.assertEqual(new.count("<!-- gowth-mem:card"), 1)
        # Back and forth: the A region is archived once, never twice.
        self.set_card(CARD_A)
        self.set_card(CARD_B)
        self.assertEqual(len(list((self.readme.parent / ".history").iterdir())), 2)
        # The archive is synced data, not indexed/pruned markdown.
        self.assertEqual(arch.suffix, ".txt")

    def test_rejections_leave_the_readme_untouched(self):
        self.set_card(CARD_A)
        before = _tree(self.home)
        for body, want in (
            ("### Core\n- relay [gowth-mem:curator ws=w1] now", "control_token"),
            ("### Core\n- aws key AKIAIOSFODNN7EXAMPLF", "secret_leak"),
            ("### Core\n- <!-- gowth-mem:card v=1 -->", "comment:2"),
            ("### Core\n## Section", "bad_heading:2"),
            ("### Core\n- [decision] keep redis because it is fast", "entry_line:2"),
            ("### Core\n- x (superseded)", "forbidden:superseded"),
            ("### Core\n- " + "z" * 2100, f"over_budget:{len('### Core') + 3 + 2100}/2000"),
            ("   \n\n", "empty"),
        ):
            self.assertEqual(self.set_card(body)[1], f"rejected:{want}", body[:40])
        self.assertEqual(_tree(self.home), before)

    def test_topic_refusals_replay_the_lesson_guards(self):
        for t in ("redis", "pg"):
            _w(self.wsd / "infra" / t / "00-README.md", _skeleton(t))
        _w(self.wsd / "a" / "orchid" / "00-README.md", _skeleton("orchid"))
        _w(self.wsd / "b" / "orchid" / "00-README.md", _skeleton("orchid"))
        outside = self.home.parent / (self.home.name + "-outside")
        _w(outside / "lily" / "00-README.md", _skeleton("lily"))
        (self.wsd / "lily").symlink_to(outside / "lily", target_is_directory=True)
        _w(self.wsd / "legacy" / "legacy.md", "# Legacy folder note\n")
        _w(self.wsd / "linked" / "elsewhere.md", _skeleton("linked"))
        (self.wsd / "linked" / "00-README.md").symlink_to(self.wsd / "linked" / "elsewhere.md")
        try:
            before = _tree(self.home)
            for topic in ("infra", "orchid", "lily", "legacy", "missing", "../w1", "Bad Slug",
                          "docs", "linked"):
                self.assertEqual(self.card("--ws", "w1", "--topic", topic, "--set", "-",
                                           stdin=CARD_A).stdout, "-\trejected:no_topic\n", topic)
                self.assertEqual(self.card("--ws", "w1", "--topic", topic, "--summary",
                                           "A fine summary").stdout, "-\trejected:no_topic\n", topic)
            for ws in ("../w1", "Bad", "w1/x", ""):
                self.assertEqual(self.card("--ws", ws, "--topic", "redis-cache", "--set", "-",
                                           stdin=CARD_A).stdout, "-\trejected:bad_ws\n", ws)
            self.assertEqual(_tree(self.home), before)
            self.assertEqual(list(outside.rglob(".history")), [])
            # Positive control: the same CLI writes the real topic.
            self.assertEqual(self.set_card(CARD_A)[1], "written")
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_corrupt_markers_are_refused(self):
        base = self.readme.read_text()
        begin, end = "<!-- gowth-mem:card v=1 updated=2025-01-01 -->", "<!-- /gowth-mem:card -->"
        variants = {
            "begin-only": base.replace(f"> {TODO}\n", f"> {TODO}\n\n{begin}\n- x\n"),
            "two-cards": base.replace(f"> {TODO}\n", f"> {TODO}\n\n{begin}\n- a\n{end}\n{begin}\n- b\n{end}\n"),
            "outside": base.replace("## Cross-links (manual)\n", f"## Cross-links (manual)\n\n{begin}\n- c\n{end}\n"),
        }
        for name, text in variants.items():
            self.readme.write_text(text)
            self.assertEqual(self.set_card(CARD_A)[1], "rejected:markers_corrupt", name)
            self.assertEqual(self.summary("New summary line")[1], "rejected:markers_corrupt", name)
            self.assertEqual(self.readme.read_text(), text, name)
            self.assertEqual(self.card("--ws", "w1", "--topic", "redis-cache", "--show").stdout, "", name)

    def test_lock_timeout_is_a_refusal(self):
        code = ("import sys; sys.path.insert(0, sys.argv[1])\n"
                "import _card\n_card.LOCK_TIMEOUT = 0.3\n"
                "sys.exit(_card.main(sys.argv[2:]))\n")
        before = self.readme.read_bytes()
        from _lock import file_lock  # type: ignore
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        try:
            with file_lock("moc", timeout=5):
                r = subprocess.run([sys.executable, "-c", code, str(SCRIPTS), "--ws", "w1", "--topic",
                                    "redis-cache", "--set", "-"], input=CARD_A, capture_output=True,
                                   text=True, timeout=60, env=_env(self.home))
                r2 = subprocess.run([sys.executable, "-c", code, str(SCRIPTS), "--ws", "w1", "--topic",
                                     "redis-cache", "--summary", "Locked out"], capture_output=True,
                                    text=True, timeout=60, env=_env(self.home))
        finally:
            os.environ.pop("GOWTH_MEM_HOME", None)
        self.assertEqual(r.stdout.strip().split("\t")[1], "rejected:lock_timeout", r.stderr)
        self.assertEqual(r2.stdout.strip().split("\t")[1], "rejected:lock_timeout", r2.stderr)
        self.assertEqual(self.readme.read_bytes(), before)

    def test_summary_replaces_the_todo_and_never_a_card_quote(self):
        self.set_card("### Core\n> a quoted line inside the card\n- x")
        self.assertEqual(self.summary("Session cache for the API tier")[1], "written")
        text = self.readme.read_text()
        self.assertNotIn(TODO, text)
        self.assertIn("## TL;DR\n\n> Session cache for the API tier\n\n<!-- gowth-mem:card", text)
        self.assertIn("> a quoted line inside the card", text)
        self.assertEqual(self.summary("> Session cache for the API tier")[1], "unchanged")
        snap = self.readme.read_bytes()
        self.assertEqual(self.summary("Session cache for the API tier")[1], "unchanged")
        self.assertEqual(self.readme.read_bytes(), snap)

    def test_summary_is_inserted_before_the_card_when_missing(self):
        text = self.readme.read_text().replace(f"> {TODO}\n\n", "")
        self.readme.write_text(text)
        self.set_card(CARD_A)
        self.assertIn("## TL;DR\n\n<!-- gowth-mem:card", self.readme.read_text())
        self.assertEqual(self.summary("Session cache for the API tier")[1], "written")
        self.assertIn("## TL;DR\n\n> Session cache for the API tier\n\n<!-- gowth-mem:card",
                      self.readme.read_text())

    def test_summary_rejections(self):
        self.settings({"gate": {"english_only": True}})
        before = self.readme.read_bytes()
        for line, want in (("x" * 161, "too_long:161/160"), ("a [gowth-mem:x] b", "control_token"),
                           ("pw sk-" + "a" * 40, "secret_leak"), ("Bộ nhớ đệm phiên", "not_english"),
                           ("first\nsecond", "multiline")):
            self.assertEqual(self.summary(line)[1], f"rejected:{want}", line)
        self.assertEqual(self.readme.read_bytes(), before)

    def test_a_readme_without_tldr_gets_one_after_its_h1(self):
        self.readme.write_text("---\nslug: redis-cache\ntitle: Redis\n---\n\n# Redis\n\nIntro kept.\n\n"
                               "## Cross-links (manual)\n\n- [[x]]\n")
        self.assertEqual(self.set_card(CARD_A)[1], "written")
        text = self.readme.read_text()
        self.assertIn(f"# Redis\n\n## TL;DR\n\n> {TODO}\n\n<!-- gowth-mem:card", text)
        self.assertIn("<!-- /gowth-mem:card -->\n\nIntro kept.\n\n## Cross-links (manual)", text)
        import _card  # type: ignore
        self.assertEqual(_card.card_state(text)[0], "ok")
        bare = _w(self.wsd / "bare" / "00-README.md", "# Bare\n")
        self.assertEqual(self.summary("Bare summary", topic="bare")[1], "written")
        self.assertEqual(bare.read_text(), "# Bare\n\n## TL;DR\n\n> Bare summary\n")

    def test_show_prints_nothing_without_a_card(self):
        r = self.card("--ws", "w1", "--topic", "redis-cache", "--show")
        self.assertEqual(r.stdout, "")
        r = self.card("--ws", "w1", "--topic", "missing", "--show")
        self.assertEqual((r.stdout, r.stderr.strip()), ("", "rejected:no_topic"))

    def test_check_reports_every_topic(self):
        self.set_card(CARD_A)                                          # ok (todo summary) …
        self.summary("Session cache for the API tier")                 # … then ok
        _w(self.wsd / "plain" / "00-README.md", _skeleton("plain", "A real summary"))    # none
        _w(self.wsd / "todo" / "00-README.md", _skeleton("todo"))                         # todo-summary
        stale = _w(self.wsd / "stale" / "00-README.md", _skeleton("stale", "S"))
        stale.write_text(stale.read_text().replace(
            "> S\n", "> S\n\n<!-- gowth-mem:card v=1 updated=2025-01-01 -->\n- x\n<!-- /gowth-mem:card -->\n"))
        _w(self.wsd / "stale" / "2026-01-02-new.md", "---\n---\n- [exp] newer aspect\n")
        over = _w(self.wsd / "over" / "00-README.md", _skeleton("over", "O"))
        over.write_text(over.read_text().replace(
            "> O\n", "> O\n\n<!-- gowth-mem:card v=1 updated=2026-01-01 -->\n- " + "w" * 2100
                     + "\n<!-- /gowth-mem:card -->\n"))
        bad = _w(self.wsd / "bad" / "00-README.md", _skeleton("bad", "B"))
        bad.write_text(bad.read_text().replace("> B\n", "> B\n\n<!-- gowth-mem:card v=1 -->\n- x\n"))
        _w(self.wsd / "legacy" / "legacy.md", "# Legacy\n")
        rows = json.loads(self.card("--ws", "w1", "--check", "--json").stdout)
        got = {r["topic"]: r["status"] for r in rows}
        self.assertEqual(got, {"redis-cache": "ok", "plain": "none", "todo": "todo-summary",
                               "stale": "stale", "over": "over-budget", "bad": "invalid",
                               "legacy": "legacy"})
        by = {r["topic"]: r for r in rows}
        self.assertEqual(by["redis-cache"]["updated"], TODAY)
        self.assertEqual(by["redis-cache"]["chars"], len(CARD_A))
        self.assertEqual(by["bad"]["issues"], ["card-markers:unbalanced"])
        self.assertTrue(by["stale"]["stale"])
        text = self.card("--ws", "w1", "--check").stdout
        self.assertIn("ok\tredis-cache\tworkspaces/w1/redis-cache/00-README.md", text)
        self.assertIn("cards ws=w1: 7 topic(s)", text)

    def test_hostile_bodies_through_the_cli_are_refused_fast(self):
        before = self.readme.read_bytes()
        for body in ("### Core\n" + "<!--" * 10_000, "### Core\n" + " " * 40_000 + "## x",
                     "### Core\n- " + "[" * 40_000, "#" * 40_000 + " [decision] x",
                     "### Core\n" + "- ok line\n" * 4_000):
            t = time.perf_counter()
            path, status = self.set_card(body)
            self.assertLess(time.perf_counter() - t, 10.0)
            self.assertTrue(status.startswith("rejected:"), (status, body[:20]))
        self.assertEqual(self.readme.read_bytes(), before)

    def test_mem_topic_documents_the_card_subcommand(self):
        doc = (ROOT / "commands" / "mem-topic.md").read_text()
        self.assertIn("card `<slug>`", doc)
        for flag in ("--show", "--set -", "--summary", "--check"):
            self.assertIn(flag, doc)
        self.assertIn('"${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_card.py"', doc)
        for status in ("written", "confirmed", "unchanged", "rejected:"):
            self.assertIn(status, doc)
        # The documented `set` block runs as written: Claude Code substitutes
        # ${CLAUDE_PLUGIN_ROOT} in command files; the heredoc must terminate.
        block = next(b for b in doc.split("```bash\n")[1:] if "--set - <<'CARD'" in b).split("```")[0]
        script = (block.replace("${CLAUDE_PLUGIN_ROOT}", str(ROOT))
                  .replace('"<ws>"', "w1").replace('"<slug>"', "redis-cache"))
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60,
                           env=_env(self.home))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip().split("\t")[1], "written", r.stdout + r.stderr)
        self.assertIn("### Routing\n| Question | Start | Then |", self.readme.read_text())


class CardPreservationTests(_Vault):
    def moc(self) -> None:
        r = subprocess.run([sys.executable, str(SCRIPTS / "_moc.py"), "--ws", "w1"],
                           env=_env(self.home), capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_carded_tldr_survives_three_rebuilds_byte_for_byte(self):
        self.set_card(CARD_A)
        self.summary("Session cache for the API tier")
        tldr = _section(self.readme.read_text(), "## TL;DR")
        self.assertIn("<!-- /gowth-mem:card -->", tldr)
        _w(self.readme.parent / "2026-11-01-eviction.md", "---\n---\n- [exp] eviction storm at 09:00\n")
        snaps = []
        for _ in range(3):
            self.moc()
            text = self.readme.read_text()
            self.assertEqual(_section(text, "## TL;DR"), tldr)
            snaps.append(text)
        self.assertIn("2026-11-01-eviction", snaps[0])          # the rebuild really ran
        self.assertEqual(snaps[0], snaps[1])
        self.assertEqual(snaps[1], snaps[2])

    def test_a_hand_typed_h2_inside_the_card_no_longer_cuts_it(self):
        self.set_card(CARD_A)
        text = self.readme.read_text().replace("### Gotchas", "## Gotchas (hand edit)")
        self.readme.write_text(text)
        for _ in range(3):
            self.moc()
            out = self.readme.read_text()
            self.assertIn("## Gotchas (hand edit)\n- never FLUSHALL", out)
            self.assertIn("- runbook.md:12\n<!-- /gowth-mem:card -->", out)
            self.assertEqual(out.count("## Aspects (auto)"), 1)
        import _card  # type: ignore
        self.assertEqual(_card.card_state(out)[0], "ok")
        self.assertTrue(any(i.startswith("card-bad-heading:") for i in _card.validate_issues(out, {})))

    def test_an_end_marker_moved_below_the_sections_never_grows_the_readme(self):
        # Hand edit: the end marker dragged under the manual section. Extending
        # the TL;DR over that "card" would copy the manual section into it on
        # every rebuild; it is treated as outside the TL;DR (v4.8 cut) instead.
        self.set_card(CARD_A)
        text = self.readme.read_text().replace("<!-- /gowth-mem:card -->\n", "")
        self.readme.write_text(text.rstrip("\n") + "\n<!-- /gowth-mem:card -->\n")
        sizes = []
        for _ in range(3):
            self.moc()
            out = self.readme.read_text()
            sizes.append(len(out))
            self.assertEqual(out.count("## Cross-links (manual)"), 1)
            self.assertEqual(out.count("## Aspects (auto)"), 1)
        self.assertEqual(sizes[0], sizes[1])
        self.assertEqual(sizes[1], sizes[2])
        self.assertEqual(self.set_card(CARD_B)[1], "rejected:markers_corrupt")

    def test_unknown_frontmatter_keys_survive_in_order(self):
        self.readme.write_text(
            "---\nslug: redis-cache\nowner: platform-team\ntitle: Redis Cache\nreview_by: 2026-12-01\n"
            "runbook:\n  url: https://wiki/redis\n  pager: ops\ntype: runbook\nstatus: active\n---\n\n"
            "# Redis Cache\n\n## TL;DR\n\n> s\n")
        for _ in range(3):
            self.moc()
        text = self.readme.read_text()
        fm = text.split("---\n")[1]
        self.assertTrue(fm.endswith("tags: []\nowner: platform-team\nreview_by: 2026-12-01\n"
                                    "runbook:\n  url: https://wiki/redis\n  pager: ops\n"), fm)
        self.assertIn("type: runbook\nstatus: active\n", fm)

    def test_scalar_and_block_list_values_are_not_mangled(self):
        self.readme.write_text(
            "---\nslug: redis-cache\ntitle: Redis\naliases: redis\nparents: infra\nlinks: \"pg, mysql\"\n"
            "tags:\n  - cache\n  - 'hot path'\n---\n\n# Redis\n\n## TL;DR\n\n> s\n")
        self.moc()
        first = self.readme.read_text()
        self.assertIn("aliases: [redis]\n", first)              # was [r, e, d, i, s]
        self.assertIn("parents: [infra]\n", first)
        self.assertIn('links: ["pg, mysql"]\n', first)          # one item, still one item
        self.assertIn("tags: [cache, hot path]\n", first)       # block list kept
        self.moc()
        self.moc()
        self.assertEqual(self.readme.read_text(), first)
        from _frontmatter import parse  # type: ignore
        fm = parse(first)[0]
        self.assertEqual((fm["aliases"], fm["links"], fm["tags"]), (["redis"], ["pg, mysql"], ["cache", "hot path"]))

    def test_workspace_map_label_is_never_a_marker(self):
        text = self.readme.read_text().replace(f"> {TODO}\n\n", "")
        self.readme.write_text(text)
        self.set_card(CARD_A)
        self.moc()
        moc = (self.wsd / "_MAP.md").read_text()
        line = next(ln for ln in moc.splitlines() if ln.startswith("- [[redis-cache]]"))
        self.assertNotIn("gowth-mem", line)
        self.assertEqual(line, "- [[redis-cache]] — Redis Cache _(status: draft)_")

    def test_index_drops_the_markers_and_keeps_the_body(self):
        self.set_card(CARD_A)
        r = subprocess.run([sys.executable, str(SCRIPTS / "_index.py"), "--full", "--no-archive"],
                           env=_env(self.home), capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        db = sqlite3.connect(str(self.home / "index.db"))
        try:
            rows = db.execute("SELECT heading, content FROM chunks WHERE path LIKE '%redis-cache/00-README.md'"
                              ).fetchall()
            hits = db.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'FLUSHALL'").fetchone()[0]
            marker = db.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'gowth'").fetchone()[0]
        finally:
            db.close()
        self.assertTrue(rows)
        self.assertTrue(any(h == "Gotchas" for h, _c in rows), rows)
        self.assertEqual(hits, 1)                               # the card body is searchable
        self.assertEqual(marker, 0)                             # the marker lines are not
        self.assertFalse(any("gowth-mem:card" in c for _h, c in rows))
        # A card write re-indexes its README right away.
        self.set_card(CARD_B)
        db = sqlite3.connect(str(self.home / "index.db"))
        try:
            self.assertEqual(db.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'FLUSHALL'"
                                        ).fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'volatile'"
                                        ).fetchone()[0], 1)
        finally:
            db.close()

    def _route(self, entry: str) -> str:
        r = subprocess.run([sys.executable, str(SCRIPTS / "_topic.py"), "--route", entry, "--ws", "w1"],
                           env=_env(self.home), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.split("\t")[0]

    def test_routing_never_scores_the_card(self):
        words = "zircon lattice beacon harbor quartz"
        target = _w(self.wsd / "other-topic" / "00-README.md", _skeleton("other-topic", "Other"))
        _w(target.parent / "2026-10-01-zircon.md", f"---\n---\n- [exp] {words} relay\n")   # overlap 6
        self.set_card(f"### Core\n- {words} relay fails drifts because")                     # overlap 9
        entry = f"[exp] {words} relay fails because the lattice drifts"
        self.assertEqual(self._route(entry), "other-topic")
        # Control: the same text with the two marker lines removed (no card any
        # more) out-scores the aspect — so the card really was what got skipped.
        carded = self.readme.read_text()
        import _card  # type: ignore
        self.readme.write_text(_card.drop_marker_lines(carded))
        self.assertEqual(self._route(entry), "redis-cache")
        self.readme.write_text(carded)
        # The real write entry point routes the same way.
        r = subprocess.run([sys.executable, str(SCRIPTS / "_topic.py"), "--append", entry, "--ws", "w1"],
                           env=_env(self.home), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        path, status = r.stdout.strip().split("\t")
        self.assertEqual((Path(path).parent.name, status), ("other-topic", "written"))
        self.assertIn("relay fails because the lattice drifts", Path(path).read_text())


class CardJunkSafetyTests(_Vault):
    def test_a_carded_readme_is_never_a_pristine_skeleton(self):
        """`_validate --prune-junk` deletes README-only folders whose README is
        the pristine skeleton (the TODO placeholder included). A card or a
        summary is curation: the folder must survive."""
        import _validate  # type: ignore
        _w(self.wsd / "alpha" / "00-README.md", _skeleton("alpha"))
        _w(self.wsd / "alpha" / "2026-01-01-beta.md", "---\nslug: alpha-beta\ntopic: alpha\n---\n- [exp] x\n")
        junk = _w(self.wsd / "alpha-beta" / "00-README.md", _skeleton("alpha-beta"))
        self.assertTrue(_validate._is_pristine_skeleton(junk.read_text(), "alpha-beta"))   # control
        r = subprocess.run([sys.executable, str(SCRIPTS / "_validate.py"), "--scan", "--ws", "w1", "--json"],
                           env=_env(self.home), capture_output=True, text=True, timeout=60)
        self.assertIn("junk-topic-folder", r.stdout)
        self.assertEqual(self.set_card(CARD_A, topic="alpha-beta")[1], "written")
        self.assertFalse(_validate._is_pristine_skeleton(junk.read_text(), "alpha-beta"))
        r = subprocess.run([sys.executable, str(SCRIPTS / "_validate.py"), "--prune-junk", "--ws", "w1"],
                           env=_env(self.home), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("removed 0 junk", r.stdout)
        self.assertTrue(junk.is_file())
        # A summary alone is curation too.
        other = _w(self.wsd / "alpha-gamma" / "00-README.md", _skeleton("alpha-gamma"))
        self.assertEqual(self.summary("Gamma summary", topic="alpha-gamma")[1], "written")
        self.assertFalse(_validate._is_pristine_skeleton(other.read_text(), "alpha-gamma"))


class CardSettingsTests(_Vault):
    def test_max_chars_is_read_from_settings_json(self):
        body = "### Core\n- " + "m" * 140
        self.settings({"topic_card": {"max_chars": 100}})
        self.assertEqual(self.set_card(body)[1], f"rejected:over_budget:{len(body)}/100")
        self.settings({"topic_card": {"max_chars": "100"}})          # a numeric string is a number
        self.assertEqual(self.set_card(body)[1], f"rejected:over_budget:{len(body)}/100")
        self.settings({"topic_card": {"max_chars": "lots"}})         # malformed → the default 2000
        self.assertEqual(self.set_card(body)[1], "written")

    def test_english_only_string_false_lets_a_vietnamese_card_through(self):
        vi = "### Core\n- Bộ nhớ đệm phiên của tầng API"
        self.settings({"gate": {"english_only": "true"}})
        self.assertEqual(self.set_card(vi)[1], "rejected:not_english")
        self.settings({"gate": {"english_only": "false"}})
        self.assertEqual(self.set_card(vi)[1], "written")


class GrowthThroughEntryPointsTests(unittest.TestCase):
    """CLAUDE.md rule 3 through the real write / index / routing entry points:
    `_moc.rebuild_topic_readme` (frontmatter grouping, the card-aware TL;DR),
    `_index.reindex_paths` (marker strip), `_topic._pick_topic` (strip_card)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_cardgrowth_")
        self.home = Path(self.tmp)
        self.wsd = self.home / "workspaces" / "w1"
        _w(self.wsd / "workspace.json", "{}")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        for k in _SCRUB:
            os.environ.pop(k, None)

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _linear(self, name: str, times: list, bound: float = 2.0) -> None:
        self.assertLess(times[-1], bound, (name, times))
        self.assertLess(times[2], max(times[1], 0.02) * 3, (name, times))

    def test_moc_rebuild_is_linear_on_hostile_readmes(self):
        import _moc  # type: ignore
        head = "---\nslug: t\ntitle: T\n"
        tail = "---\n\n# T\n\n## TL;DR\n\n> s\n"
        shapes = {
            "fm-long-value": lambda n: f"{head}owner: {'a' * n}\n{tail}",
            "fm-long-non-key": lambda n: f"{head}{'a' * n}\n{tail}",
            "fm-space-run": lambda n: f"{head}key{' ' * n}x\n{tail}",
            "fm-dash-run": lambda n: f"{head}{'a-' * (n // 2)}\n{tail}",
            "fm-block-list": lambda n: f"{head}tags:\n" + "  - t\n" * (n // 6) + tail,
            "card-h2-flood": lambda n: (f"{head}{tail}\n<!-- gowth-mem:card v=1 -->\n" + "## x\n" * (n // 5)
                                        + "<!-- /gowth-mem:card -->\n"),
            "marker-flood": lambda n: f"{head}{tail}\n" + "<!-- gowth-mem:card v=1 -->\n" * (n // 28),
        }
        outs = {}
        for name, make in shapes.items():
            times = []
            for n in (10_000, 20_000, 40_000):
                folder = self.wsd / f"{name}-{n}"
                _w(folder / "00-README.md", make(n))
                t = time.perf_counter()
                self.assertIsNotNone(_moc.rebuild_topic_readme(folder))
                times.append(time.perf_counter() - t)
                outs[name] = (folder / "00-README.md").read_text()
            self._linear(name, times)
        # Positive signals: the rebuild parsed what it was fed.
        self.assertIn("owner: " + "a" * 40_000 + "\n", outs["fm-long-value"])       # unknown key kept
        self.assertIn("tags: [" + ", ".join(["t"] * (40_000 // 6)) + "]", outs["fm-block-list"])
        self.assertTrue(outs["card-h2-flood"].rstrip().endswith("(curate `[[wikilinks]]` to related topics "
                                                                "here — preserved across MOC rebuilds)"))
        self.assertIn("## x\n<!-- /gowth-mem:card -->\n\n## Aspects (auto)", outs["card-h2-flood"])

    def test_index_marker_strip_is_linear(self):
        import _index  # type: ignore
        r = subprocess.run([sys.executable, str(SCRIPTS / "_index.py"), "--no-archive"],
                           env=_env(self.home), capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        shapes = {
            "begin-lines": lambda n: "## TL;DR\n" + "<!-- gowth-mem:card v=1 -->\n" * (n // 28) + "keepword\n",
            "end-lines": lambda n: "## TL;DR\n" + "<!-- /gowth-mem:card -->\n" * (n // 25) + "keepword\n",
            "hint-long-line": lambda n: "## TL;DR\ngowth-mem:card " + "x" * n + "\nkeepword\n",
            "hint-many-lines": lambda n: "gowth-mem:card\n" + "y\n" * (n // 2) + "keepword\n",
        }
        for name, make in shapes.items():
            times = []
            for n in (10_000, 20_000, 40_000):
                p = _w(self.wsd / f"g-{name}" / f"2026-01-0{len(times) + 1}-probe.md", make(n))
                t = time.perf_counter()
                self.assertEqual(_index.reindex_paths([p]), 1)
                times.append(time.perf_counter() - t)
            self._linear(name, times)
        db = sqlite3.connect(str(self.home / "index.db"))
        try:
            self.assertEqual(db.execute("SELECT count(*) FROM chunks WHERE content LIKE '%<!-- gowth-mem:card%' "
                                        "OR content LIKE '%<!-- /gowth-mem:card%'").fetchone()[0], 0)
            self.assertGreaterEqual(db.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'keepword'"
                                               ).fetchone()[0], 4)
        finally:
            db.close()

    def test_routing_scan_is_linear_on_hostile_cards(self):
        import _topic  # type: ignore
        words = "zircon lattice beacon"           # 3 = min_keyword_overlap: a match if scored
        shapes = {
            "open-card": lambda n: "## TL;DR\n\n<!-- gowth-mem:card v=1 -->\n" + f"{words}\n" * (n // 22),
            "marker-flood": lambda n: "## TL;DR\n" + f"<!-- gowth-mem:card v=1 -->\n{words}\n" * (n // 50),
            "end-flood": lambda n: "## TL;DR\n" + "<!-- /gowth-mem:card -->\n" * (n // 25),
        }
        for name, make in shapes.items():
            times = []
            for n in (10_000, 20_000, 40_000):
                d = self.wsd / f"r-{name}-{n}"
                _w(d / "00-README.md", "---\nslug: x\n---\n" + make(n))
                t = time.perf_counter()
                slug, folder = _topic._pick_topic("[exp] zircon lattice beacon harbor quartz", "w1", {})
                times.append(time.perf_counter() - t)
                # The card's words never made its README the match.
                self.assertIsNone(folder, (name, n, slug))
                shutil.rmtree(d)
            self._linear(name, times)
        # Control: the same words outside a card do match.
        d = _w(self.wsd / "plain" / "00-README.md", f"---\nslug: plain\n---\n## TL;DR\n\n> {words}\n").parent
        self.assertEqual(_topic._pick_topic("[exp] zircon lattice beacon harbor quartz", "w1", {}),
                         ("plain", d))


class CardValidatorTests(_Vault):
    def scan(self, *extra: str) -> list:
        r = subprocess.run([sys.executable, str(SCRIPTS / "_validate.py"), "--scan", "--ws", "w1",
                            "--json", *extra], env=_env(self.home), capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def issues_for(self, findings: list, name: str) -> list:
        return next((f["issues"] for f in findings if f["file"].endswith(f"{name}/00-README.md")), [])

    def test_card_findings_only_when_a_marker_exists(self):
        self.set_card(CARD_A)
        self.assertEqual(self.scan(), [])                      # a clean card, clean README
        begin, end = "<!-- gowth-mem:card v=1 updated=2026-01-01 -->", "<!-- /gowth-mem:card -->"
        fixtures = {
            "unbal": (f"{begin}\n- x\n", ["card-markers:unbalanced"]),
            "endonly": (f"- x\n{end}\n", ["card-markers:unbalanced"]),
            "dup": (f"{begin}\n- a\n{end}\n{begin}\n- b\n{end}\n", ["card-markers:duplicate"]),
            "control": (f"{begin}\n- see [gowth-mem:curator ws=w1]\n{end}\n", ["card-control-token"]),
            "budget": (f"{begin}\n- {'q' * 2100}\n{end}\n", [f"card-over-budget:{2102}/2000"]),
        }
        for name, (card, want) in fixtures.items():
            p = _w(self.wsd / name / "00-README.md", _skeleton(name, "Summary"))
            p.write_text(p.read_text().replace("> Summary\n", "> Summary\n\n" + card))
        out = _w(self.wsd / "outside" / "00-README.md", _skeleton("outside", "Summary"))
        out.write_text(out.read_text() + f"\n{begin}\n- x\n{end}\n")
        heads = _w(self.wsd / "heads" / "00-README.md", _skeleton("heads", "Summary"))
        heads.write_text(heads.read_text().replace(
            "> Summary\n", f"> Summary\n\n{begin}\n### Core\n## Oops\n- [decision] x because y\n{end}\n"))
        lines = heads.read_text().split("\n")
        oops, entry = lines.index("## Oops") + 1, lines.index("- [decision] x because y") + 1
        findings = self.scan()
        for name, (_card, want) in fixtures.items():
            self.assertEqual(self.issues_for(findings, name), want, name)
        self.assertEqual(self.issues_for(findings, "outside"), ["card-outside-tldr"])
        self.assertEqual(self.issues_for(findings, "heads"),
                         [f"card-bad-heading:{oops}", f"card-entry-line:{entry}"])
        self.assertEqual(self.issues_for(findings, "redis-cache"), [])

    def test_cards_flag_lists_card_less_and_todo_topics(self):
        _w(self.wsd / "plain" / "00-README.md", _skeleton("plain", "A real summary"))
        self.assertEqual(self.scan(), [])                      # quiet by default
        findings = self.scan("--cards")
        self.assertEqual(self.issues_for(findings, "plain"), ["card-none"])
        self.assertEqual(self.issues_for(findings, "redis-cache"), ["card-none", "card-todo-summary"])
        self.set_card(CARD_A)
        self.summary("Session cache")
        self.assertEqual(self.issues_for(self.scan("--cards"), "redis-cache"), [])


if __name__ == "__main__":
    unittest.main()
