"""v4.9 WP-B2 (spec §5): the MEMORY.md managed block — rules digest, the
`## Open questions` section (gap ledger + curator status), the incremental
shrink, and the Topics summary line.

The block stays deterministic: synced inputs only (docs/questions.md, the
newest docs/worklog/*.md, topic READMEs), total tie-breaks, no host / version /
now() — every machine renders the same bytes.
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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "hooks" / "scripts"
SPEC = ROOT / "docs" / "superpowers" / "specs" / "2026-11-07-v4.9-curator-design.md"
sys.path.insert(0, str(SCRIPTS))

import _memfile  # type: ignore  # noqa: E402

TODO = "> Cốt lõi 1 dòng (TODO)."
HANDOFF = ("# handoff — demo\n\n## 2026-11-06 snapshot\n"
           "- host:mac 2026-11-06 [doing] wire the curator\n"
           "- host:mac 2026-11-06 [next] review the cards\n")
CARD = ("<!-- gowth-mem:card v=1 updated=2026-11-07 -->\n### Core\n- Card body line for the topic.\n"
        "### Sources\n- a fixture\n<!-- /gowth-mem:card -->")


def _readme(slug: str, title: str, touched: str, tldr: str = TODO, card: str = "",
            fm_extra: str = "") -> str:
    card_part = f"\n\n{card}" if card else ""
    return (f"---\nslug: {slug}\ntitle: {title}\ntype: topic\nlast_touched: {touched}\n{fm_extra}---\n"
            f"# {title}\n\n## TL;DR\n\n{tldr}{card_part}\n\n## Aspects (auto)\n\n- none\n\n"
            f"## Cross-links (manual)\n\n(none)\n")


def _qline(qid: str, kind: str, opened: str, text: str, extra: str = "") -> str:
    return f"- {qid} · {kind} · {opened} · {text}{extra}\n"


def _questions(lines: list, closed: list = ()) -> str:
    return ("# Open questions — demo\n\n> header\n\n## Open\n" + "".join(lines)
            + "## Closed\n" + "".join(closed))


def _worklog(date: str, host: str, run: str, stats: str, phase: str = "2", end: str = "") -> str:
    return (f"---\ntype: worklog\nws: demo\ndate: {date}\nhost: {host}\nrun: {run}\nphase: {phase}\n"
            f"window_start: {date}T00:01\nwindow_end: {end or date + 'T10:40'}\nvault_sha: \n"
            f"processed: []\nstats: {stats}\n---\n# Worklog {date} — demo\n\n## Curator actions\n- none\n")


class _Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_mf49_")
        self._old_home = os.environ.get("GOWTH_MEM_HOME")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        os.environ.pop("GOWTH_WORKSPACE", None)
        self.home = Path(self.tmp)
        self.ws = "demo"
        self.wsd = self.home / "workspaces" / self.ws
        (self.wsd / "docs").mkdir(parents=True)
        (self.wsd / "workspace.json").write_text('{"name": "demo"}')
        (self.wsd / "docs" / "handoff.md").write_text(HANDOFF)
        (self.home / "shared").mkdir()
        (self.home / "shared" / "secrets.md").write_text("- `FAKE_API_KEY` — env var\n")
        (self.home / "settings.json").write_text('{"layout_version": 3}')
        for slug, touched in (("alpha", "2026-11-01"), ("beta", "2026-11-03")):
            d = self.wsd / slug
            d.mkdir()
            (d / "00-README.md").write_text(_readme(slug, slug.title(), touched))

    def tearDown(self):
        if self._old_home is None:
            os.environ.pop("GOWTH_MEM_HOME", None)
        else:
            os.environ["GOWTH_MEM_HOME"] = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def section(self, block: str, title: str) -> list:
        lines = block.splitlines()
        if title not in lines:
            return []
        i = lines.index(title) + 1
        j = i
        while j < len(lines) and not lines[j].startswith("## ") and not lines[j].startswith("<!-- gowth-mem:end"):
            j += 1
        return lines[i:j]

    def write_questions(self, lines: list, closed: list = ()) -> None:
        (self.wsd / "docs" / "questions.md").write_text(_questions(lines, closed))

    def write_worklog(self, name: str, text: str) -> Path:
        d = self.wsd / "docs" / "worklog"
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        p.write_text(text)
        return p


# ─── §5.1 rules digest ───────────────────────────────────────────────────────

class RulesDigestTest(_Case):
    def test_twelve_lines_verbatim_from_the_spec(self):
        lines = SPEC.read_text(encoding="utf-8").splitlines()
        start = next(i for i, ln in enumerate(lines) if ln.startswith("### 5.1 Rules digest"))
        spec_rules = []
        for ln in lines[start + 1:]:
            if not ln[:1].isdigit():
                if spec_rules:
                    break
                continue
            spec_rules.append(ln[ln.index("`") + 1:ln.rindex("`")])
        self.assertEqual(len(spec_rules), 12)
        self.assertEqual(_memfile.rules_digest(), spec_rules)

    def test_joined_length_is_pinned_under_2000(self):
        joined = "\n".join(_memfile.rules_digest())
        self.assertEqual(len(joined), 1993, "re-measure and re-pin when the digest changes")
        self.assertLessEqual(len(joined), 2000)
        self.assertEqual(len(_memfile.rules_digest()), 12)

    def test_block_carries_the_digest_and_using_stays_last(self):
        block = _memfile.render(self.ws)
        for rule in _memfile.rules_digest():
            self.assertIn(rule, block)
        heads = [ln for ln in block.splitlines() if ln.startswith("## ")]
        self.assertEqual(heads[0], "## Rules")
        self.assertEqual(heads[-1], "## Using memory")
        self.assertIn('/mem-save [type] "<entry>"', block)


# ─── §5.2 open questions ─────────────────────────────────────────────────────

class OpenQuestionsTest(_Case):
    def test_section_omitted_without_questions_or_worklog(self):
        block = _memfile.render(self.ws)
        self.assertNotIn("## Open questions", block)
        self.write_questions([], [_qline("Q-00000001", "ask", "2026-11-01→2026-11-02",
                                         "an already answered question here")])
        self.assertNotIn("## Open questions", _memfile.render(self.ws), "closed items do not open the section")

    def test_order_asks_first_oldest_first_id_tiebreak(self):
        self.write_questions([
            _qline("Q-0000000a", "research", "2026-11-01", "research question number one ok"),
            _qline("Q-0000000c", "ask", "2026-11-05", "ask question opened later in time"),
            _qline("Q-0000000b", "ask", "2026-11-02", "ask question with the larger id"),
            _qline("Q-00000009", "ask", "2026-11-02", "ask question with the smaller id"),
        ])
        block = _memfile.render(self.ws)
        sec = self.section(block, "## Open questions")
        self.assertEqual([ln.split(" ")[1] for ln in sec],
                         ["Q-00000009", "Q-0000000b", "Q-0000000c", "Q-0000000a"])
        self.assertEqual(sec[0], "- Q-00000009 ask: ask question with the smaller id")
        self.assertTrue(sec[3].startswith("- Q-0000000a research: "))
        heads = [ln for ln in block.splitlines() if ln.startswith("## ")]
        self.assertEqual(heads, ["## Rules", "## Handoff", "## Open questions", "## Topics",
                                 "## Secrets (pointers only)", "## Using memory"])

    def test_cap_and_plus_n_more(self):
        lines = [_qline(f"Q-{i:08x}", "research", f"2026-10-{i + 1:02d}", f"research question number {i} here")
                 for i in range(9)]
        self.write_questions(lines)
        sec = self.section(_memfile.render(self.ws), "## Open questions")
        self.assertEqual(len(sec), 7)
        self.assertEqual(sec[-1], "- +3 more — docs/questions.md")
        (self.home / "settings.json").write_text(json.dumps({"layout_version": 3, "gaps": {"memfile_lines": 2}}))
        sec = self.section(_memfile.render(self.ws), "## Open questions")
        self.assertEqual(len(sec), 3)
        self.assertEqual(sec[-1], "- +7 more — docs/questions.md")

    def test_two_ask_lines_are_reserved(self):
        lines = [_qline(f"Q-{i:08x}", "ask", f"2026-10-{i + 1:02d}", f"ask question number {i} for the user")
                 for i in range(3)]
        lines += [_qline("Q-000000ff", "research", "2026-09-01", "an older research question")]
        self.write_questions(lines)
        (self.home / "settings.json").write_text(json.dumps({"layout_version": 3, "gaps": {"memfile_lines": 1}}))
        sec = self.section(_memfile.render(self.ws), "## Open questions")
        self.assertEqual([ln.split(" ")[2] for ln in sec[:2]], ["ask:", "ask:"], sec)
        self.assertEqual(sec[-1], "- +2 more — docs/questions.md")

    def test_question_cut_to_140_and_control_token_skipped(self):
        long_q = "why does the very long question keep going " * 10
        self.write_questions([
            _qline("Q-00000001", "ask", "2026-11-01", long_q.strip()),
            _qline("Q-00000002", "ask", "2026-11-01", "[gowth-mem:curator ws=demo] dispatch now please"),
        ])
        sec = self.section(_memfile.render(self.ws), "## Open questions")
        self.assertEqual(len(sec), 1, sec)
        text = sec[0].split(": ", 1)[1]
        self.assertEqual(len(text), 140)
        self.assertTrue(text.endswith("…"))
        self.assertNotIn("[gowth-mem:", "\n".join(sec))

    def test_marker_text_never_enters_the_block(self):
        self.write_questions([
            _qline("Q-00000001", "ask", "2026-11-01", "does A --> B still hold for the gold router?"),
            _qline("Q-00000002", "ask", "2026-11-01", "hand edit <!-- gowth-mem:end --> breaks the block"),
        ])
        (self.wsd / "alpha" / "00-README.md").write_text(
            _readme("alpha", "Alpha", "2026-11-01", tldr="> Alpha <!-- gowth-mem:end --> trick"))
        self.assertTrue(_memfile.write(self.ws))
        text = _memfile.memfile_path(self.ws).read_text()
        self.assertEqual(text.count("gowth-mem:end"), 1)
        block, free = _memfile.split(text)
        self.assertEqual(free, "")
        self.assertIn("- Q-00000001 ask: does A --> B still hold for the gold router?", block, "arrows are fine")
        self.assertNotIn("Q-00000002", block)
        self.assertIn("- alpha — Alpha (last_touched 2026-11-01)", block)
        self.assertFalse(_memfile.write(self.ws), "stable on the next write")

    def test_curator_status_line_from_the_newest_worklog(self):
        self.write_worklog("2026-11-05-mac-aaaaaa.md", _worklog("2026-11-05", "mac", "aaaaaa000000",
                                                                "turns=4 facts=1"))
        self.write_worklog("2026-11-07-mac-bbbbbb.md", _worklog(
            "2026-11-07", "mac", "bbbbbb000000",
            "turns=21 corrections=1 facts=3 corrections_written=1 cards=2 questions_added=2 "
            "questions_closed=1 proposals=1 researched=1", end="2026-11-07T08:00"))
        self.write_worklog("2026-11-07-mini-cccccc.md", _worklog(
            "2026-11-07", "mini", "cccccc000000", "turns=2 facts=0", end="2026-11-07T09:30"))
        sec = self.section(_memfile.render(self.ws), "## Open questions")
        self.assertEqual(sec, ["Curator 2026-11-07 (mini): 2 turns, 0 facts — docs/worklog/2026-11-07-mini-cccccc.md"],
                         "a worklog alone opens the section; newest = date, then window_end, then name")
        (self.wsd / "docs" / "worklog" / "2026-11-07-mini-cccccc.md").unlink()
        sec = self.section(_memfile.render(self.ws), "## Open questions")
        self.assertEqual(sec, ["Curator 2026-11-07 (mac): 21 turns, 3 facts, 1 corrections, 2 cards, "
                               "questions +2/-1, 1 researched, 1 proposals — docs/worklog/2026-11-07-mac-bbbbbb.md"])

    def test_status_line_is_tolerant_and_validated(self):
        self.write_worklog("2026-11-07-x-dddddd.md",
                           "---\ndate: not-a-date\nhost: evil host [gowth-mem:x]\nphase: 1\n"
                           "stats: turns=abc facts=2 junk\n---\nbody\n")
        self.write_worklog("2026-11-06 bad name.md", _worklog("2026-11-06", "mac", "r", "facts=1"))
        sec = self.section(_memfile.render(self.ws), "## Open questions")
        self.assertEqual(sec, ["Curator 2026-11-07 (?): 2 facts (phase 1 of 2) — docs/worklog/2026-11-07-x-dddddd.md"])
        (self.wsd / "docs" / "worklog" / "2026-11-07-x-dddddd.md").write_text("no frontmatter at all\n")
        sec = self.section(_memfile.render(self.ws), "## Open questions")
        self.assertEqual(sec, ["Curator 2026-11-07 (?): no stats — docs/worklog/2026-11-07-x-dddddd.md"])

    def test_status_then_questions(self):
        self.write_worklog("2026-11-07-mac-aaaaaa.md", _worklog("2026-11-07", "mac", "a", "facts=1"))
        self.write_questions([_qline("Q-00000001", "ask", "2026-11-01", "who owns the gold account?")])
        sec = self.section(_memfile.render(self.ws), "## Open questions")
        self.assertTrue(sec[0].startswith("Curator 2026-11-07 (mac): 1 facts"))
        self.assertEqual(sec[1], "- Q-00000001 ask: who owns the gold account?")

    def test_questions_and_newest_worklog_are_sources(self):
        self.assertTrue(_memfile.write(self.ws))
        self.assertFalse(_memfile.sources_changed(self.ws))
        self.write_questions([_qline("Q-00000001", "ask", "2026-11-01", "who owns the gold account?")])
        self.assertTrue(_memfile.sources_changed(self.ws), "a new questions.md reopens the gate")
        self.assertTrue(_memfile.write(self.ws))
        self.assertIn("who owns the gold account?", _memfile.memfile_path(self.ws).read_text())
        self.assertFalse(_memfile.sources_changed(self.ws))
        self.write_worklog("2026-11-07-mac-aaaaaa.md", _worklog("2026-11-07", "mac", "a", "facts=1"))
        self.assertTrue(_memfile.sources_changed(self.ws), "a new worklog reopens the gate")
        srcs = {p.name for p in _memfile._sources(self.ws)}
        self.assertIn("questions.md", srcs)
        self.assertIn("2026-11-07-mac-aaaaaa.md", srcs)

    def test_render_fingerprint_covers_gaps_and_card(self):
        import hashlib
        h = hashlib.sha1()
        for name in ("_memfile.py", "_handoff.py", "_gaps.py", "_card.py"):
            h.update((SCRIPTS / name).read_bytes())
        self.assertEqual(_memfile.RENDER_FP, h.hexdigest()[:12])


# ─── §5.3 incremental shrink ─────────────────────────────────────────────────

class ShrinkTest(_Case):
    def _seed(self):
        (self.wsd / "docs" / "handoff.md").write_text(
            "## 2026-11-06 s\n" + "".join(f"- host:mac 2026-11-06 [done] item {i}\n" for i in range(80)))
        for i in range(40):
            d = self.wsd / f"t{i:02d}"
            d.mkdir()
            (d / "00-README.md").write_text(_readme(f"t{i:02d}", f"T{i}", "2026-10-05"))
        self.write_questions([_qline(f"Q-{i:08x}", "ask" if i < 3 else "research", f"2026-10-{i + 1:02d}",
                                     f"open question number {i} for the block") for i in range(8)])
        self.write_worklog("2026-11-07-mac-aaaaaa.md", _worklog("2026-11-07", "mac", "a", "facts=1"))

    def test_gaps_shrink_keeps_two_asks_and_the_more_line(self):
        self._seed()
        full = self.section(_memfile.render(self.ws), "## Open questions")
        self.assertEqual(len(full), 8, full)            # status + 6 + more
        # floor 20 + handoff 1+20 + gaps 1+3 + topics 1+10 = 56 lines once every step
        # down to gaps→3 has run; a 50-line budget then cuts topics (topics→0 step)
        block = _memfile.render(self.ws, max_lines=50)
        self.assertEqual(block.count("\n"), 50)
        sec = self.section(block, "## Open questions")
        self.assertEqual(len(sec), 3, sec)
        self.assertEqual([ln.split(" ")[2] for ln in sec[:2]], ["ask:", "ask:"], "status goes before asks")
        self.assertEqual(sec[2], "- +6 more — docs/questions.md", "the +N more line counts what is hidden")
        self.assertLess(len(self.section(block, "## Topics")), 10, "topics→0 runs after gaps→3")
        self.assertEqual(len(self.section(block, "## Handoff")), 20)
        self.assertEqual(self.section(block, "## Secrets (pointers only)"), [])
        # one line less than the gaps→3 state: gaps keeps 2 asks + more until topics are gone
        block = _memfile.render(self.ws, max_lines=58)
        sec = self.section(block, "## Open questions")
        self.assertEqual(len(sec), 5, "gaps lose single lines: 8 → 5 fits 58")
        self.assertEqual(sec[-1], "- +4 more — docs/questions.md")

    def test_char_budget_cuts_in_step_order_one_line_at_a_time(self):
        self._seed()
        full = _memfile.render(self.ws)
        self.assertLessEqual(len(full), _memfile.MAX_CHARS)
        topics_full = len(self.section(full, "## Topics"))
        block = _memfile.render(self.ws, max_chars=len(full) - 150)
        self.assertLessEqual(len(block), len(full) - 150)
        self.assertGreater(len(block), len(full) - 400, "incremental: only as much as needed is cut")
        topics = len(self.section(block, "## Topics"))
        self.assertLess(topics, topics_full)
        self.assertGreater(topics, 10, "topics lose single lines, not a 30-line chunk")
        self.assertEqual(self.section(block, "## Handoff"), self.section(full, "## Handoff"),
                         "handoff is untouched while topics are above their step minimum")
        self.assertEqual(self.section(block, "## Open questions"), self.section(full, "## Open questions"))

    def test_floor_keeps_rules_and_using(self):
        self._seed()
        block = _memfile.render(self.ws, free_zone_lines=185)
        self.assertEqual(block.count("\n"), _memfile.floor_lines())
        self.assertNotIn("## Open questions", block)
        self.assertIn("## Using memory", block)

    def test_hook_bootstrap_places_questions_after_handoff(self):
        self._seed()
        text = _memfile.render_hook_bootstrap(self.ws)
        self.assertLess(len(text), 8500)
        self.assertLess(text.index("## Handoff"), text.index("## Open questions"))
        self.assertLess(text.index("## Open questions"), text.index("## Rules"))


# ─── §5.4 topics summary line ────────────────────────────────────────────────

class SummaryLineTest(_Case):
    def line_for(self, slug: str) -> str:
        return next(ln for ln in _memfile.topic_index(self.ws) if ln.startswith(f"- {slug} — "))

    def test_quote_marker_stripped(self):
        (self.wsd / "alpha" / "00-README.md").write_text(
            _readme("alpha", "Alpha", "2026-11-01", tldr="> Alpha routes gold orders to the broker."))
        self.assertEqual(self.line_for("alpha"),
                         "- alpha — Alpha routes gold orders to the broker. (last_touched 2026-11-01)")

    def test_todo_placeholder_falls_back_to_the_title(self):
        self.assertEqual(self.line_for("alpha"), "- alpha — Alpha (last_touched 2026-11-01)")

    def test_card_region_skipped_and_suffixed(self):
        (self.wsd / "beta" / "00-README.md").write_text(_readme("beta", "Beta", "2026-11-03", tldr="", card=CARD))
        line = self.line_for("beta")
        self.assertEqual(line, "- beta — Beta [card] (last_touched 2026-11-03)",
                         "the card body is never the summary")
        (self.wsd / "beta" / "00-README.md").write_text(
            _readme("beta", "Beta", "2026-11-03", tldr="> Beta is the order router.", card=CARD))
        self.assertEqual(self.line_for("beta"), "- beta — Beta is the order router. [card] (last_touched 2026-11-03)")

    def test_frontmatter_summary_wins_and_long_text_is_cut(self):
        (self.wsd / "alpha" / "00-README.md").write_text(
            _readme("alpha", "Alpha", "2026-11-01", fm_extra="summary: > From the frontmatter.\n"))
        self.assertIn("- alpha — From the frontmatter. (last_touched", self.line_for("alpha"))
        (self.wsd / "alpha" / "00-README.md").write_text(
            _readme("alpha", "Alpha", "2026-11-01", tldr="> " + "word " * 60, card=CARD))
        line = self.line_for("alpha")
        summary = line[len("- alpha — "):line.index(" (last_touched")]
        self.assertTrue(summary.endswith("… [card]"), summary)
        self.assertEqual(len(summary), 100 + len(" [card]"))

    def test_corrupt_card_markers_are_not_a_card(self):
        broken = CARD.replace("<!-- /gowth-mem:card -->", "")
        (self.wsd / "beta" / "00-README.md").write_text(_readme("beta", "Beta", "2026-11-03", card=broken))
        self.assertNotIn("[card]", self.line_for("beta"))


# ─── determinism ─────────────────────────────────────────────────────────────

class DeterminismTest(_Case):
    def test_byte_identical_across_three_processes(self):
        ShrinkTest._seed(self)  # type: ignore[arg-type]
        (self.wsd / "beta" / "00-README.md").write_text(_readme("beta", "Beta", "2026-11-03", card=CARD))
        self.write_worklog("2026-11-07-mini-bbbbbb.md", _worklog("2026-11-07", "mini", "b", "facts=2",
                                                                 end="2026-11-07T10:40"))
        code = ("import sys; sys.path.insert(0, %r); import _memfile; "
                "sys.stdout.write(_memfile.render('demo'))" % str(SCRIPTS))
        outs = []
        for _ in range(3):
            r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                               env={**os.environ, "GOWTH_MEM_HOME": self.tmp})
            self.assertEqual(r.returncode, 0, r.stderr)
            outs.append(r.stdout)
        self.assertEqual(outs[0], outs[1])
        self.assertEqual(outs[1], outs[2])
        self.assertIn("## Open questions", outs[0])
        self.assertIn("[card]", outs[0])
        self.assertNotIn(os.uname().nodename, outs[0])

    def test_three_writes_are_byte_identical(self):
        ShrinkTest._seed(self)  # type: ignore[arg-type]
        self.assertTrue(_memfile.write(self.ws))
        p = _memfile.memfile_path(self.ws)
        first = p.read_bytes()
        for _ in range(2):
            self.assertFalse(_memfile.write(self.ws))
            self.assertEqual(p.read_bytes(), first)


# ─── growth (the new readers are line-based; the 4-test rule's probe) ────────

class GrowthTest(_Case):
    def test_hostile_questions_and_worklog_render_in_linear_time(self):
        shapes = {
            "unclosed": "- Q-0000000a · ask · 2026-11-01 · " + "(" * 50 + "\n",
            "spaces": "- Q-0000000b · ask · 2026-11-01 · a" + " " * 60 + "b\n",
            "separators": "- Q-0000000c · ask · 2026-11-01 · q" + " — why: x" * 6 + "\n",
            "prefix": "- Q-" * 20 + "\n",
        }
        for name, unit in shapes.items():
            times = []
            for size in (10_000, 20_000, 40_000):
                text = (unit * (size // len(unit) + 1))[:size]
                (self.wsd / "docs" / "questions.md").write_text("## Open\n" + text + "\n## Closed\n")
                self.write_worklog("2026-11-07-mac-aaaaaa.md",
                                   "---\nstats: " + "turns=1 " * (size // 8) + "\nhost: " + "x" * size + "\n---\n")
                t = time.perf_counter()
                block = _memfile.render(self.ws)
                times.append(time.perf_counter() - t)
                self.assertIn("## Rules", block)
                self.assertIn("Curator 2026-11-07 (?)", block, "the worklog was parsed")
            self.assertLess(times[2], 2.0, (name, times))
            self.assertLessEqual(times[2], 2.5 * times[1] + 0.05, (name, times))
            self.assertLessEqual(times[1], 2.5 * times[0] + 0.05, (name, times))


if __name__ == "__main__":
    unittest.main()
