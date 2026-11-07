#!/usr/bin/env python3
"""v4.9 §8 (WP-B3): data safety in the curator's path — `_forget` salvage.

Before: aspect archival salvaged only `- [type]` bullets, so the two dominant
live shapes — bare `[type] …` lines and `## [type]` blocks — vanished from the
synced layer into the gitignored `.archive/` (a vault copy with every aspect
past a 0-day threshold: 10 of 2,695 entries salvaged over 2,018 aspects). And
the provenance line the salvage wrote was read back as part of the bullet
above it, so whenever the archive step failed every later run appended the
same entries again.

Now ONE pure parser (`_forget.entry_blocks`) reads all three shapes for aspect
salvage, journal salvage and their seen-sets; the provenance line ends a block
and is never part of it; lessons.md is written under `entries-<ws>` taken
inside `topic-forget-<ws>`; and an aspect is archived only when its salvage
verifiably landed. Tests follow the CLAUDE.md 4-test rule: the real entry
point (`_forget.py --aspects`, `forget_aspects`, `forget_workspace`), 3-pass
byte-identical repeated writes with the archive step failing, 10k/20k/40k
growth probes on adversarial shapes, positive-signal assertions.
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "hooks" / "scripts"
FORGET = SCRIPTS / "_forget.py"
sys.path.insert(0, str(SCRIPTS))

import _forget  # noqa: E402  # type: ignore

SCRUB = ("AI_AGENT", "GOWTH_WORKSPACE", "CLAUDE_SUBAGENT", "GOWTH_MEM_OFF",
         "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_PLUGIN_ROOT")
WS = "demo"

# ── the three live shapes (+ the ordered/wrapped list item and an H1 block) ──

BULLET = ("- [tool] `rg -n pattern` needs ripgrep 14.1 or newer for the json flag\n"
          "  install with brew install ripgrep\n"
          "\n"
          "  a second indented paragraph of the same bullet")
BLOCK = ("## [decision] Use fcntl locks for multi-session safety\n"
         "because os.replace is atomic and flock is advisory.\n"
         "\n"
         "### Details stay inside the block\n"
         "```bash\n"
         "# a bash comment is not a heading\n"
         "flock -n /tmp/x.lock true\n"
         "## neither is this\n"
         "```\n"
         "- [exp] a nested bullet inside the block stays inside it")
BARE = ("[ref] Heron cache eviction is LRU with a 10 minute idle TTL\n"
        "Source: https://example.org/heron/docs#eviction\n"
        "\n"
        "UPDATE 2026-11-05: the TTL became configurable in heron 2.4\n"
        "- a plain bullet that belongs to the same entry")
H1_BLOCK = ("# [exp] Rebuilding the index after a schema bump takes 40 seconds\n"
            "\n"
            "Measured on the 15k-chunk vault; the incremental pass is 0.4 s.")
ORDERED = ("1. `[reflection]` Compare the healthy twin node before any deep forensics\n"
           "   on the broken one. Source: session aaaa1111 turn 12")


def _aspect_text(name: str, body: str) -> str:
    return (f"---\ntype: aspect\ndate: {name[:10]}\ntopic: t\ntitle: [exp] a frontmatter title echo\n"
            f"slug: t-{name[11:-3]}\n---\n\n# {name}\n\n{body}\n")


def _w(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _state(p: Path) -> tuple:
    return p.read_bytes(), os.stat(p).st_mtime_ns


class _Vault(unittest.TestCase):
    """A scratch vault: workspace `demo`, topic `t` (README + lessons.md)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gowth_forget_salvage_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "vault"
        self.wsd = self.home / "workspaces" / WS
        _w(self.wsd / "workspace.json", json.dumps({"name": WS}))
        _w(self.home / "settings.json", "{}")
        _w(self.home / "config.json", json.dumps({"active_workspace": WS}))
        self.topic = self.wsd / "t"
        _w(self.topic / "00-README.md", "---\nslug: t\ntitle: T\n---\n# T\n")
        self.lessons = _w(self.topic / "lessons.md", "# Lessons\n")
        patcher = mock.patch.dict(os.environ, {"GOWTH_MEM_HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        for k in SCRUB:
            os.environ.pop(k, None)

    def aspect(self, name: str, body: str) -> Path:
        return _w(self.topic / name, _aspect_text(name, body))

    def seed_all_shapes(self) -> list:
        return [self.aspect("2020-01-01-bullet.md", BULLET),
                self.aspect("2020-01-02-block.md", BLOCK),
                self.aspect("2020-01-03-bare.md", BARE),
                self.aspect("2020-01-04-h1.md", H1_BLOCK),
                self.aspect("2020-01-05-ordered.md", ORDERED)]

    def cli(self, *extra: str, timeout: int = 120) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k not in SCRUB}
        env.update({"GOWTH_MEM_HOME": str(self.home), "HOME": str(self.tmp / "h"),
                    "CLAUDE_CONFIG_DIR": str(self.tmp / "c"), "GOWTH_MEM_NO_AUTOHEAL": "1"})
        r = subprocess.run([sys.executable, str(FORGET), "--all-workspaces", "--aspects",
                            "--aspect-threshold-days", "30", "--keep-newest", "0", *extra],
                           capture_output=True, text=True, env=env, timeout=timeout)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        return r

    def run_aspects(self, dry_run: bool = False) -> dict:
        return _forget.forget_aspects(WS, 30, 0, dry_run, self.home)

    def archived(self) -> list:
        d = self.home / ".archive" / "topics" / WS / "t"
        return sorted(p.name for p in d.glob("*.md.gz")) if d.is_dir() else []

    def break_archive(self) -> None:
        """Make `_archive_one` fail through the REAL code path: the archive
        directory it must create is a file."""
        _w(self.home / ".archive" / "topics" / WS / "t", "not a directory\n")


# ── the parser ─────────────────────────────────────────────────────────────

class EntryBlocksTest(unittest.TestCase):
    def spans(self, text: str) -> list:
        return [(shape, block) for shape, _a, _b, block in _forget.entry_spans(text)]

    def test_each_shape_and_its_extent(self):
        doc = ("---\ntitle: [exp] frontmatter is never an entry\nsummary: |\n  [ref] nor is yaml\n---\n\n"
               "# Aspect title\n\n" + BULLET + "\nnot indented: ends the bullet\n\n" + BLOCK +
               "\n\n## Plain section (not an entry)\nprose\n\n" + BARE + "\n\n"
               "[hypothesis] second bare entry. Verify: run the probe twice\n" + ORDERED + "\n" + H1_BLOCK + "\n")
        got = self.spans(doc)
        self.assertEqual(got, [("bullet", BULLET), ("block", BLOCK), ("bare", BARE),
                               ("bare", "[hypothesis] second bare entry. Verify: run the probe twice"),
                               ("bullet", ORDERED), ("block", H1_BLOCK)])
        self.assertEqual(_forget.entry_blocks(doc), [b for _s, b in got])

    def test_provenance_line_ends_a_block_and_is_never_part_of_it(self):
        prov = "  (salvaged from archived aspect 2026-01-01-x.md)"
        for block in (BULLET, BLOCK, BARE, H1_BLOCK, ORDERED):
            doc = f"# Lessons\n## Salvaged from archived aspects\n\n{block}\n{prov}\ntrailing prose line\n"
            self.assertEqual(_forget.entry_blocks(doc), [block], block[:20])
        # old v4.1 salvage format (bullet + provenance) reads back as the bare bullet
        old = "- [decision] archive by knowledge date because mtime is perturbed\n" + prov + "\n"
        self.assertEqual(_forget.entry_blocks(old),
                         ["- [decision] archive by knowledge date because mtime is perturbed"])

    def test_non_entries(self):
        doc = ("## [auto-precompact-dump] 2026-01-01\n### [assistant]\n[lesson] not a schema type\n"
               "#hashtag line\nthe [decision] tag mid-line\n- [ ] a checklist item\n"
               "```\n- [decision] inside a closed fence is code\n[ref] code too\n```\n"
               "[Decision] Upper-case types count because the schema is case-insensitive\n")
        self.assertEqual(self.spans(doc), [
            ("bare", "[Decision] Upper-case types count because the schema is case-insensitive")])

    def test_unclosed_fence_is_plain_text(self):
        doc = "```python\nnever closed\n\n- [exp] still an entry after a stray fence opener\n"
        self.assertEqual(_forget.entry_blocks(doc), ["- [exp] still an entry after a stray fence opener"])
        doc2 = "## [decision] block with a stray ~~~ opener\n~~~\n## Next\n[ref] next entry Source: x\n"
        self.assertEqual(_forget.entry_blocks(doc2),
                         ["## [decision] block with a stray ~~~ opener\n~~~", "[ref] next entry Source: x"])

    def test_heading_levels(self):
        doc = ("### [ref] an H3 entry block Source: docs\nbody\n#### sub stays\nmore\n"
               "### Next H3 section ends it\nx\n## [goal] goal Status: active\n### inside\n# Top\n")
        self.assertEqual(self.spans(doc), [
            ("block", "### [ref] an H3 entry block Source: docs\nbody\n#### sub stays\nmore"),
            ("block", "## [goal] goal Status: active\n### inside")])

    def test_h1_entry_keeps_its_h2_update_sections(self):
        """The live `# [exp] …` entries carry later updates as `##` sections."""
        entry = ("# [exp] Vague task: check the widget logs\n\n**Symptom:** one line, no host\n\n"
                 "## Update 2 (same thread)\n**Did:** measured the queue\n\n## Update 3\n- curl it")
        doc = entry + "\n# Next top-level heading\n[ref] after it. Source: x\n"
        self.assertEqual(self.spans(doc), [("block", entry), ("bare", "[ref] after it. Source: x")])

    def test_bullet_keeps_an_adjacent_fence_but_not_a_detached_one(self):
        doc = "- [tool] run the probe:\n```bash\npython3 probe.py --all\n```\n"
        self.assertEqual(_forget.entry_blocks(doc), [doc.rstrip()])
        doc2 = "- [tool] run the probe with python3 3.9:\n\n```bash\nunrelated code\n```\n"
        self.assertEqual(_forget.entry_blocks(doc2), ["- [tool] run the probe with python3 3.9:"])

    def test_frontmatter_needs_a_closer(self):
        doc = "---\n[ref] not frontmatter without a closing marker. Source: x\n"
        self.assertEqual(len(_forget.entry_blocks(doc)), 1)

    def test_body_len(self):
        self.assertEqual(_forget._body_len("- [exp] nope"), 4)
        self.assertEqual(_forget._body_len("## [ref] t\nSource: y"), len("t\nSource: y"))
        self.assertEqual(_forget._body_len("1. `[reflection]` abc"), 3)


# ── aspect salvage through the real entry point ────────────────────────────

class AspectSalvageEntryPointTest(_Vault):
    def test_every_shape_is_salvaged_with_provenance_then_archived(self):
        aspects = self.seed_all_shapes()
        r = self.cli()
        self.assertIn("archived 5 aspect(s)", r.stdout)
        self.assertIn("salvaged 5 entr(y/ies) into lessons.md (bullet 2, block 2, bare 1)", r.stdout)
        text = self.lessons.read_text()
        self.assertTrue(text.startswith("# Lessons\n## Salvaged from archived aspects\n\n"), text[:80])
        for p, block in zip(aspects, (BULLET, BLOCK, BARE, H1_BLOCK, ORDERED)):
            self.assertIn(block + "\n  (salvaged from archived aspect " + p.name + ")\n", text)
            self.assertFalse(p.exists(), p.name)
        self.assertNotIn("frontmatter title echo", text)
        # read back: exactly the five blocks, in order
        self.assertEqual(_forget.entry_blocks(text), [BULLET, BLOCK, BARE, H1_BLOCK, ORDERED])
        self.assertEqual(len(self.archived()), 5)
        gz = self.home / ".archive" / "topics" / WS / "t" / self.archived()[1]
        with gzip.open(gz, "rt") as fh:
            self.assertIn("## [decision] Use fcntl locks", fh.read())

    def test_dry_run_reports_shapes_and_writes_nothing(self):
        aspects = self.seed_all_shapes()
        before = _state(self.lessons)
        r = self.cli("--dry-run")
        self.assertIn("[dry-run] forget-aspects: [demo] archived 5 aspect(s)", r.stdout)
        self.assertIn("(bullet 2, block 2, bare 1)", r.stdout)
        self.assertEqual(_state(self.lessons), before)
        self.assertTrue(all(p.exists() for p in aspects))
        self.assertFalse((self.home / ".archive").exists())

    def test_v41_salvage_already_in_lessons_is_not_added_again(self):
        """The v4.1 format (bullet + provenance) left by a run whose archive
        failed: the aspect is archived now and the entry is not duplicated."""
        a = self.aspect("2020-01-01-old.md", "- [decision] archive by knowledge date because mtime is perturbed\n")
        _w(self.lessons, "# Lessons\n## Salvaged from archived aspects\n\n"
                         "- [decision] archive by knowledge date because mtime is perturbed\n"
                         "  (salvaged from archived aspect 2020-01-01-old.md)\n")
        before = self.lessons.read_bytes()
        r = self.cli()
        self.assertIn("archived 1 aspect(s)", r.stdout)
        self.assertIn("salvaged 0 entr(y/ies)", r.stdout)
        self.assertEqual(self.lessons.read_bytes(), before)
        self.assertFalse(a.exists())

    def test_appending_after_an_open_block_starts_a_new_section(self):
        hand = "## [decision] hand-written decision kept because the user wrote it\nits body line"
        _w(self.lessons, "# Lessons\n## Salvaged from archived aspects\n\n- [exp] earlier salvaged entry\n"
                         "  (salvaged from archived aspect 2019-01-01-a.md)\n" + hand + "\n")
        self.aspect("2020-01-01-bullet.md", BULLET)
        self.cli()
        text = self.lessons.read_text()
        self.assertEqual(text.count("## Salvaged from archived aspects"), 2)
        # the hand-written block did not swallow the new entry, and reads back unchanged
        self.assertEqual(_forget.entry_blocks(text), ["- [exp] earlier salvaged entry", hand, BULLET])

    def test_secret_is_salvaged_redacted_and_the_reruns_are_stable(self):
        a = self.aspect("2020-01-01-secret.md",
                        "[ref] the staging db login works again. password=hunter2hunter2hunter2 Source: ops log")
        self.break_archive()
        self.cli()
        first = _state(self.lessons)
        text = self.lessons.read_text()
        self.assertIn("[REDACTED:kv-secret]", text)
        self.assertNotIn("hunter2hunter2", text)
        self.assertIn("(salvaged from archived aspect 2020-01-01-secret.md)", text)
        for _ in range(2):
            r = self.cli()
            self.assertIn("salvaged 0 entr(y/ies)", r.stdout)
            self.assertEqual(_state(self.lessons), first)
        self.assertTrue(a.exists())

    def test_head_refusal_cases_replayed_through_the_entry_point(self):
        """HEAD's guards still hold on the new path: a topic's newest aspects,
        young aspects, README/lessons.md, reserved dirs and non-topic folders
        are never salvaged or archived."""
        old = self.aspect("2020-01-01-old.md", BARE)
        newest = [self.aspect(f"2020-02-0{i}-new.md", f"[exp] newest number {i} is protected by keep-newest")
                  for i in (1, 2, 3)]
        doc = _w(self.wsd / "docs" / "2020-01-01-note.md", "[ref] docs are reserved. Source: x\n")
        domain = _w(self.wsd / "dom" / "2020-01-01-loose.md", "[ref] a domain holds no aspects. Source: x\n")
        env = {k: v for k, v in os.environ.items() if k not in SCRUB}
        env.update({"GOWTH_MEM_HOME": str(self.home), "HOME": str(self.tmp / "h")})
        r = subprocess.run([sys.executable, str(FORGET), "--all-workspaces", "--aspects",
                            "--aspect-threshold-days", "30"], capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("archived 1 aspect(s)", r.stdout)       # keep-newest 3 (default) protects the rest
        self.assertFalse(old.exists())
        for p in newest + [doc, domain, self.topic / "00-README.md", self.lessons]:
            self.assertTrue(p.exists(), p)
        text = self.lessons.read_text()
        self.assertIn(BARE, text)
        self.assertNotIn("protected by keep-newest", text)
        self.assertNotIn("reserved", text)
        # threshold: with keep-newest 0 the old ones go, an aspect dated today never does
        young = self.aspect(time.strftime("%Y-%m-%d") + "-young.md", "[exp] a young aspect is never a candidate")
        r = self.cli()
        self.assertIn("archived 3 aspect(s)", r.stdout)
        self.assertTrue(young.exists())
        self.assertNotIn("young aspect", self.lessons.read_text())
        self.assertTrue(doc.exists() and domain.exists())


# ── repeated writes: the archive step fails, the salvage must not grow ──────

class RepeatedWriteTest(_Vault):
    def test_three_cli_passes_with_a_failing_archive_are_byte_identical(self):
        aspects = self.seed_all_shapes()
        self.break_archive()
        r1 = self.cli()
        self.assertIn("salvaged 5 entr(y/ies)", r1.stdout)
        self.assertIn("archived 0 aspect(s)", r1.stdout)
        after1 = _state(self.lessons)
        self.assertEqual(_forget.entry_blocks(self.lessons.read_text()), [BULLET, BLOCK, BARE, H1_BLOCK, ORDERED])
        for _ in range(2):
            r = self.cli()
            self.assertIn("salvaged 0 entr(y/ies)", r.stdout)
            self.assertEqual(_state(self.lessons), after1, "lessons.md changed on a re-run")
        self.assertTrue(all(p.exists() for p in aspects))

    def test_three_in_process_passes_with_archive_one_failing(self):
        self.seed_all_shapes()
        with mock.patch.object(_forget, "_archive_one", return_value=False) as arch:
            r1 = self.run_aspects()
            after1 = _state(self.lessons)
            r2 = self.run_aspects()
            r3 = self.run_aspects()
        self.assertEqual((r1["salvaged"], r1["shapes"]), (5, {"bullet": 2, "block": 2, "bare": 1}))
        self.assertEqual((r2["salvaged"], r3["salvaged"]), (0, 0))
        self.assertEqual((r1["candidates"], r2["candidates"], r3["candidates"]), (5, 5, 5))
        self.assertEqual(_state(self.lessons), after1)
        self.assertEqual(arch.call_count, 15)          # every landed aspect was offered to the archive
        self.assertEqual(self.lessons.read_text().count("(salvaged from archived aspect "), 5)

    def test_journal_salvage_three_passes_are_byte_identical(self):
        jd = self.wsd / "journal"
        # (a `##` block runs to the next `#`/`##` heading, so it goes last)
        j = _w(jd / "2020-01-01.md", "## [auto-precompact-dump] 2020-01-01\n### [user]\nraw prose\n\n"
                                     + BULLET + "\n\n" + BARE + "\n\n" + BLOCK + "\n")
        old = time.time() - 30 * 86400
        os.utime(j, (old, old))
        with mock.patch.object(_forget, "_archive_one", return_value=False):
            r1 = _forget.forget_workspace(WS, 7, 75_000, True, False, self.home)
            salv = jd / "_salvage.md"
            after1 = _state(salv)
            r2 = _forget.forget_workspace(WS, 7, 75_000, True, False, self.home)
            r3 = _forget.forget_workspace(WS, 7, 75_000, True, False, self.home)
        self.assertEqual((r1["salvaged"], r2["salvaged"], r3["salvaged"]), (3, 0, 0))
        self.assertEqual(_state(salv), after1)
        text = salv.read_text()
        for block in (BULLET, BARE, BLOCK):
            self.assertIn(block + "\n  (salvaged from archived journal 2020-01-01.md)\n", text)
        self.assertNotIn("raw prose", text)
        self.assertEqual(_forget.entry_blocks(text), [BULLET, BARE, BLOCK])

    def test_unreadable_journal_salvage_file_is_never_replaced(self):
        """It used to be read as "" on any error and then overwritten with the
        new entries alone; now the run stops before writing or archiving."""
        jd = self.wsd / "journal"
        salv = _w(jd / "_salvage.md", "# _salvage.md\n\n## salvaged 2020-01-01\n\n"
                                      "- [exp] an older salvaged entry that must survive the run\n")
        j = _w(jd / "2020-01-01.md", BARE + "\n")
        old = time.time() - 30 * 86400
        os.utime(j, (old, old))
        before = _state(salv)
        real = Path.read_text

        def denied(path, *a, **k):
            if path.name == "_salvage.md":
                raise PermissionError("denied")
            return real(path, *a, **k)

        with mock.patch.object(Path, "read_text", denied):
            with self.assertRaises(PermissionError):
                _forget.forget_workspace(WS, 7, 75_000, True, False, self.home)
        self.assertEqual(_state(salv), before)
        self.assertTrue(j.exists())
        # readable again: the entry is salvaged below the old one, then the journal archived
        r = _forget.forget_workspace(WS, 7, 75_000, True, False, self.home)
        self.assertEqual((r["salvaged"], r["archived"]), (1, 1))
        text = salv.read_text()
        self.assertIn("an older salvaged entry that must survive the run", text)
        self.assertIn(BARE + "\n  (salvaged from archived journal 2020-01-01.md)\n", text)


# ── locks and the never-archive-unsalvaged rule ─────────────────────────────

class LockAndLandingTest(_Vault):
    def test_entries_lock_timeout_archives_nothing(self):
        from _lock import file_lock  # type: ignore
        aspects = self.seed_all_shapes()
        other = _w(self.wsd / "u" / "00-README.md", "---\nslug: u\n---\n# U\n").parent
        aspects.append(_w(other / "2020-01-01-x.md", _aspect_text("2020-01-01-x.md", BARE)))
        before = _state(self.lessons)
        with file_lock(f"entries-{WS}", timeout=5):
            t0 = time.time()
            r = self.cli()
            waited = time.time() - t0
        self.assertEqual(_state(self.lessons), before)
        self.assertFalse((other / "lessons.md").exists())
        self.assertTrue(all(p.exists() for p in aspects))
        self.assertFalse((self.home / ".archive" / "topics").exists())
        self.assertIn("2 topic(s) left for the next run", r.stdout)
        self.assertLess(waited, 9, "one 5 s wait, then the rest of the workspace is tried once")
        # released: the next run salvages and archives (positive control)
        r = self.cli()
        self.assertIn("archived 6 aspect(s)", r.stdout)
        self.assertEqual(len(self.archived()), 5)
        self.assertIn(BARE, (other / "lessons.md").read_text())

    def test_in_process_lock_timeout(self):
        from _lock import file_lock  # type: ignore
        import _topic  # type: ignore
        self.seed_all_shapes()
        with mock.patch.object(_topic, "ENTRIES_LOCK_TIMEOUT", 0.2), file_lock(f"entries-{WS}", timeout=5):
            r = self.run_aspects()
        self.assertEqual((r["archived"], r["salvaged"], r["skipped"], r["candidates"]), (0, 0, 1, 5))
        self.assertEqual(self.lessons.read_text(), "# Lessons\n")

    def test_entries_lock_is_taken_inside_the_forget_lock(self):
        import _topic  # type: ignore
        from _lock import file_lock  # type: ignore
        real = _topic.entries_lock
        seen = []

        def spy(ws, timeout=None):
            try:                                   # topic-forget must be held right now
                with file_lock(f"topic-forget-{ws}", timeout=0):
                    seen.append("forget-free")
            except TimeoutError:
                seen.append("forget-held")
            return real(ws, timeout)

        self.seed_all_shapes()
        with mock.patch.object(_topic, "entries_lock", spy):
            r = self.run_aspects()
        self.assertEqual(seen, ["forget-held"])
        self.assertEqual(r["archived"], 5)

    def test_dry_run_takes_no_entries_lock(self):
        from _lock import file_lock  # type: ignore
        self.seed_all_shapes()
        with file_lock(f"entries-{WS}", timeout=5):
            t0 = time.time()
            r = self.run_aspects(dry_run=True)
            self.assertLess(time.time() - t0, 3)
        self.assertEqual((r["archived"], r["salvaged"]), (5, 5))

    def test_failed_lessons_write_archives_nothing(self):
        aspects = self.seed_all_shapes()
        with mock.patch.object(_forget, "safe_write", side_effect=OSError("disk full")):
            r = self.run_aspects()
        self.assertEqual((r["archived"], r["skipped"]), (0, 1))
        self.assertTrue(all(p.exists() for p in aspects))
        self.assertEqual(self.archived(), [])

    def test_unreadable_lessons_archives_nothing(self):
        aspects = self.seed_all_shapes()
        _w(self.lessons, "# Lessons\n\n- [exp] an entry the run must not lose by overwriting\n")
        before = _state(self.lessons)
        real = Path.read_text

        def denied(path, *a, **k):
            if path.name == "lessons.md":
                raise PermissionError("denied")
            return real(path, *a, **k)

        with mock.patch.object(Path, "read_text", denied):
            r = self.run_aspects()
        self.assertEqual((r["archived"], r["skipped"]), (0, 1))
        self.assertTrue(all(p.exists() for p in aspects))
        self.assertEqual(_state(self.lessons), before)

    def test_lessons_path_that_cannot_be_replaced_archives_nothing(self):
        aspects = self.seed_all_shapes()
        self.lessons.unlink()
        self.lessons.mkdir()                       # the write's os.replace fails
        r = self.run_aspects()
        self.assertEqual((r["archived"], r["skipped"]), (0, 1))
        self.assertTrue(all(p.exists() for p in aspects))

    def test_salvage_that_would_not_read_back_is_neither_written_nor_archived(self):
        """lessons.md holds a stray `<private>` opener and an aspect entry a
        `</private>`: sanitizing the composed file (what safe_write stores)
        would cut from one to the other — an existing lesson AND the new entry
        gone. The read-back check refuses: nothing written, that aspect kept;
        an aspect whose salvage is unaffected is still archived."""
        _w(self.lessons, "# Lessons\n\nnotes <private> left open by hand\n\n"
                         "- [exp] an existing lesson that a careless append would erase\n")
        bad = self.aspect("2020-01-01-closer.md",
                          "[ref] a closing tag </private> sits in this entry. Source: x")
        before = _state(self.lessons)
        r = self.run_aspects()
        self.assertEqual((r["archived"], r["salvaged"], r["skipped"]), (0, 0, 1))
        self.assertEqual(_state(self.lessons), before)
        self.assertTrue(bad.exists())
        # an aspect without entries has nothing to land and is archived
        empty = self.aspect("2020-01-02-prose.md", "only prose, no entry here at all")
        r = self.run_aspects()
        self.assertEqual(r["archived"], 1)
        self.assertFalse(empty.exists())
        self.assertTrue(bad.exists())
        self.assertEqual(_state(self.lessons), before)
        self.assertIn("an existing lesson that a careless append would erase", self.lessons.read_text())

    def test_a_stray_frontmatter_opener_atop_lessons_does_not_block_salvage(self):
        _w(self.lessons, "---\n# Lessons\n")
        a = self.aspect("2020-01-01-rule.md", "[ref] thematic break inside the entry. Source: x\n---\nmore text")
        r = self.run_aspects()
        self.assertEqual((r["archived"], r["salvaged"], r["skipped"]), (1, 1, 0))
        self.assertFalse(a.exists())
        self.assertIn("[ref] thematic break inside the entry. Source: x\n---\nmore text\n  (salvaged from",
                      self.lessons.read_text())

    def test_an_open_h1_entry_at_the_end_of_lessons_gets_an_h1_section(self):
        hand = "# [exp] a hand-written H1 entry at the end of lessons\n\n## its later update section\nstill it"
        _w(self.lessons, "# Lessons\n\n" + hand + "\n")
        a = self.aspect("2020-01-01-bullet.md", BULLET)
        r = self.run_aspects()
        self.assertEqual((r["archived"], r["salvaged"], r["skipped"]), (1, 1, 0))
        text = self.lessons.read_text()
        self.assertIn("still it\n# Salvaged from archived aspects\n\n" + BULLET + "\n", text)
        self.assertEqual(_forget.entry_blocks(text), [hand, BULLET])
        self.assertFalse(a.exists())


# ── growth probes (CLAUDE.md rule 3) ────────────────────────────────────────

def _best(fn, arg, reps: int = 3) -> float:
    best = float("inf")
    for _ in range(reps):
        t0 = time.perf_counter()
        fn(arg)
        best = min(best, time.perf_counter() - t0)
    return best


SHAPES = {
    # unclosed openers
    "unclosed-fence-openers": lambda n: "~~~ info\n" * (n // 9) + "[ref] survives the openers. Source: x\n",
    "unclosed-backtick-runs": lambda n: "`" * n + "\n[ref] after a backtick run. Source: x\n",
    "frontmatter-never-closed": lambda n: "---\n" + "key: value\n" * (n // 11) + "[ref] tail. Source: x\n",
    # whitespace runs
    "space-run-then-entry": lambda n: " \t" * (n // 2) + "[decision] long indent because test\n",
    "blank-lines-in-bullet": lambda n: "- [exp] head of a bullet entry here\n" + "\n" * n + "  indented tail\n",
    # repeated separators
    "dash-flood": lambda n: "- " * (n // 2) + "\n",
    "ordered-flood": lambda n: "1. " * (n // 3) + "\n",
    "hash-run": lambda n: "#" * n + "\n## [goal] g Status: active\n",
    "hash-run-inside-block": lambda n: "## [goal] g Status: active\n" + "#" * n + "\n",
    "space-line-inside-bare": lambda n: "[ref] r Source: x\n" + " " * n + "x\n",
    "fence-long-info": lambda n: "## [decision] d because x\n```" + "a" * n + "\n# inside\n```\n",
    "fence-pairs-with-hash-lines": lambda n: "## [decision] d because x\n" + "```\n# c\n```\n" * (n // 12),
    "provenance-flood": lambda n: "  (salvaged from archived aspect x.md)\n" * (n // 39),
    # token-prefix floods
    "type-prefix-flood": lambda n: "[decisio" * (n // 8) + "\n",
    "bracket-flood": lambda n: "- [" * (n // 3) + "\n",
    "entry-per-line": lambda n: "".join(f"[ref] e{i:07d} Source: x\n" for i in range(n // 26)),
    "bare-heads-one-line": lambda n: "[ref]" * (n // 5) + "\n",
}
# positive signals: what each shape must parse to (blocks found)
EXPECT = {
    "unclosed-fence-openers": 1, "unclosed-backtick-runs": 1, "frontmatter-never-closed": 1,
    "space-run-then-entry": 1, "blank-lines-in-bullet": 1, "dash-flood": 0, "ordered-flood": 0,
    "hash-run": 1, "hash-run-inside-block": 1, "space-line-inside-bare": 1, "fence-long-info": 1,
    "fence-pairs-with-hash-lines": 1, "provenance-flood": 0, "type-prefix-flood": 0,
    "bracket-flood": 0, "entry-per-line": None, "bare-heads-one-line": 1,
}


class GrowthTest(_Vault):
    def _linear(self, name: str, times: list, bound: float = 2.0) -> None:
        self.assertLess(times[2], bound, (name, times))
        if times[2] > 0.02:
            self.assertLess(times[2], 6 * max(times[0], 1e-3), f"{name} not linear: {times}")

    def test_parser_is_linear_on_adversarial_shapes(self):
        for name, make in SHAPES.items():
            times = [_best(_forget.entry_spans, make(n)) for n in (10_000, 20_000, 40_000)]
            self._linear(name, times)
            got = _forget.entry_blocks(make(40_000))
            want = EXPECT[name] if EXPECT[name] is not None else 40_000 // 26
            self.assertEqual(len(got), want, name)
        # the blank-line run stays inside the bullet (continuation found past 40k blank lines)
        self.assertTrue(_forget.entry_blocks(SHAPES["blank-lines-in-bullet"](40_000))[0].endswith("indented tail"))
        # the hash lines inside fences never ended the block
        blk = _forget.entry_blocks(SHAPES["fence-pairs-with-hash-lines"](40_000))[0]
        self.assertEqual(blk.count("# c"), 40_000 // 12)

    def test_salvage_entry_point_is_linear(self):
        """forget_aspects (read → parse → compose → sanitize → verify → write →
        archive) on one hostile aspect per size; a fresh topic each time."""
        shapes = {k: SHAPES[k] for k in ("unclosed-fence-openers", "space-run-then-entry",
                                         "fence-pairs-with-hash-lines", "entry-per-line", "type-prefix-flood")}
        for name, make in shapes.items():
            times = []
            for n in (10_000, 20_000, 40_000):
                folder = self.wsd / f"g-{name}-{n}"
                _w(folder / "00-README.md", "---\nslug: g\n---\n# G\n")
                _w(folder / "2020-01-01-probe.md", make(n))
                t0 = time.perf_counter()
                r = _forget.forget_aspects(WS, 30, 0, False, self.home)
                times.append(time.perf_counter() - t0)
                self.assertEqual(r["archived"], 1, (name, n))
                lessons = (folder / "lessons.md")
                if EXPECT[name] == 0:
                    self.assertFalse(lessons.exists(), (name, n))
                else:
                    self.assertIn("(salvaged from archived aspect 2020-01-01-probe.md)", lessons.read_text())
                shutil.rmtree(folder)
            self._linear(name, times, bound=5.0)


if __name__ == "__main__":
    unittest.main()
