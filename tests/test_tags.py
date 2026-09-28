"""Tests for _tags.py (v4.0 deterministic auto-tagging).

Covers:
  - extract_tags determinism, code-token preservation, VI stopword removal, caps,
    empty/short input, prefix collapse
  - strip_tags / strip_tags_text round-trip (dedup stability primitive)
  - merge_frontmatter_tags (inline + block forms, idempotence, no-frontmatter)
  - apply_inline_tags (first-line suffix, idempotent)
  - append_entry writes inline suffix + frontmatter union (idempotent)
  - topic auto-create denylist (AKIA placeholder → misc, not akia... topic)
  - backfill dry-run vs --apply on a tmp GOWTH_MEM_HOME fixture
  - v4.7.6: identifier patterns start only at token starts (no #top-hook
    fragments), linear on long runs, strip_tags identical to the old regex
"""
from __future__ import annotations  # `dict | None` below; the suite targets 3.9+

import importlib.util
import json
import os
import random
import shutil
import re
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "hooks" / "scripts"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _run_in_home(code: str, home: Path, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["GOWTH_MEM_HOME"] = str(home)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT, env=env, check=True, capture_output=True, text=True,
    )


def _scaffold(home: Path, ws: str = "w1", tags_enabled: bool = True) -> None:
    (home / "workspaces" / ws).mkdir(parents=True, exist_ok=True)
    (home / "workspaces" / ws / "workspace.json").write_text("{}")
    (home / "settings.json").write_text(json.dumps({
        "gate": {"enabled": True, "strict": True},
        "tags": {"enabled": tags_enabled, "max_per_entry": 7, "max_frontmatter": 15},
        "topic_routing": {"min_keyword_overlap": 3, "default_topic": "misc", "default_aspect": "note"},
    }))


class ExtractTagsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_tags_")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        self.t = load_module("gowth_tags_ex", SCRIPTS / "_tags.py")

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)

    def test_determinism(self):
        text = "[decision] use FTS5 weighted BM25 for recall because keyword outranks body — see _query.py"
        a = self.t.extract_tags(text)
        b = self.t.extract_tags(text)
        self.assertEqual(a, b)
        self.assertTrue(a, "expected some tags")

    def test_code_token_preservation(self):
        text = "[tool] run `python3 _index.py --full` and check settings.json plus GOWTH_MEM_HOME"
        tags = self.t.extract_tags(text)
        # Priority identifiers survive as tags.
        self.assertIn("_index.py", tags)
        self.assertIn("settings.json", tags)
        self.assertIn("gowth_mem_home", tags)

    def test_camelcase_and_kebab_preserved(self):
        tags = self.t.extract_tags("[exp] the topic-routing denylist calls parseConfig on PostgreSQL")
        self.assertIn("topic-routing", tags)
        self.assertIn("parseconfig", tags)
        self.assertIn("postgresql", tags)

    def test_vi_stopword_removal(self):
        # VI stopwords (là, của, được, không, và, ...) must be dropped; content kept.
        text = "[exp] chiến lược này của tôi được kiểm chứng và không bị lỗi trên gold futures"
        tags = self.t.extract_tags(text)
        for stop in ("của", "được", "không", "này", "và", "bị", "trên"):
            self.assertNotIn(stop, tags, f"VI stopword {stop!r} leaked into tags")
        # Substring collapse keeps the compound (`gold-futures`), which still
        # LIKE-matches a `futures` keyword search — either form is acceptable.
        self.assertTrue(
            any("futures" in t for t in tags),
            f"expected a futures tag, got {tags}",
        )

    def test_cap_respected(self):
        text = "[ref] alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima"
        self.assertLessEqual(len(self.t.extract_tags(text, max_tags=3)), 3)
        self.assertLessEqual(len(self.t.extract_tags(text, max_tags=7)), 7)

    def test_short_input_yields_fewer_never_pads(self):
        tags = self.t.extract_tags("[decision] use ATR sizing", max_tags=7)
        self.assertLessEqual(len(tags), 7)
        self.assertIn("atr", tags)
        self.assertIn("sizing", tags)

    def test_empty_and_blank_input(self):
        self.assertEqual(self.t.extract_tags(""), [])
        self.assertEqual(self.t.extract_tags("   \n  "), [])
        self.assertEqual(self.t.extract_tags("[decision] foo", max_tags=0), [])

    def test_drops_versions_dates_hex_and_numeric_ranges(self):
        text = "[ref] release v3.4 on 2026-06-19 commit deadbeef1234 range 15-20 dollars"
        tags = self.t.extract_tags(text)
        for bad in ("v3.4", "2026-06-19", "deadbeef1234", "15-20"):
            self.assertNotIn(bad, tags, f"{bad!r} should be dropped")

    def test_prefix_collapse_keeps_longer(self):
        # "topic" is a strict prefix of "topic-routing" → dropped in favour of longer.
        tags = self.t.extract_tags("[exp] topic topic-routing topic-routing decision about topic")
        self.assertIn("topic-routing", tags)
        self.assertNotIn("topic", tags)

    def test_typical_output_is_3_to_5(self):
        # A prose-heavy entry should stay in the 3-5 band (SOFT_TOTAL), not 7.
        text = ("[decision] use exponential moving average crossover strategy with "
                "average true range volatility filter to reduce false breakout whipsaw noise")
        tags = self.t.extract_tags(text, max_tags=7)
        self.assertLessEqual(len(tags), 5)
        self.assertGreaterEqual(len(tags), 3)

    def test_identifier_heavy_may_exceed_soft_cap(self):
        # Many priority identifiers can push past SOFT_TOTAL up to the hard cap.
        text = "[tool] wire _tags.py _index.py _query.py _dedup.py _topic.py _lesson.py together"
        tags = self.t.extract_tags(text, max_tags=7)
        self.assertGreater(len(tags), 5)
        self.assertLessEqual(len(tags), 7)

    def test_noun_phrase_bigram_from_adjacent_high_score(self):
        tags = self.t.extract_tags("[ref] gold futures dominate the session. Source: CME")
        self.assertIn("gold-futures", tags)

    def test_casing_boost_retains_proper_noun(self):
        # A single Capitalized proper noun flows through with a casing boost.
        tags = self.t.extract_tags("[decision] route traffic through Cloudflare workers globally")
        self.assertIn("cloudflare", tags)

    def test_non_ascii_words_are_not_mangled_into_tags(self):
        # VI diacritic content must not produce garbage like "chi-n-l-c".
        tags = self.t.extract_tags("[exp] chiến lược này của tôi trên gold futures")
        for tg in tags:
            self.assertNotIn("-n-", tg, f"mangled diacritic tag: {tg}")
        self.assertTrue(any("gold" in tg or "futures" in tg for tg in tags))


class StripRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_tags_strip_")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        self.t = load_module("gowth_tags_strip", SCRIPTS / "_tags.py")

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)

    def test_format_suffix(self):
        self.assertEqual(self.t.format_suffix([]), "")
        self.assertEqual(self.t.format_suffix(["a", "b"]), "  #a #b")

    def test_strip_tags_roundtrip(self):
        line = "[decision] use FTS5 recall see _query.py"
        tags = ["fts5", "_query.py", "recall"]
        with_tags = self.t.apply_inline_tags(line, tags)
        self.assertEqual(self.t.strip_tags(with_tags), line)

    def test_strip_tags_preserves_non_trailing_hash(self):
        # A leading hashtag (not a trailing run) must be preserved.
        line = "#foo is at the start of this line"
        self.assertEqual(self.t.strip_tags(line), line)

    def test_strip_tags_text_multiline(self):
        text = "[exp] first line  #a #b\nsecond line body\nthird  #c"
        stripped = self.t.strip_tags_text(text)
        self.assertEqual(stripped, "[exp] first line\nsecond line body\nthird")

    def test_apply_inline_tags_idempotent(self):
        line = "[ref] gold futures range. Source: CME"
        tags = ["gold", "futures", "cme"]
        once = self.t.apply_inline_tags(line, tags)
        twice = self.t.apply_inline_tags(once, tags)
        self.assertEqual(once, twice)


class MergeFrontmatterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_tags_fm_")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        self.t = load_module("gowth_tags_fm", SCRIPTS / "_tags.py")

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)

    def test_union_inline_preserves_existing_order(self):
        fm = "---\nslug: x\ntitle: X\ntags: [gowth-mem, release]\n---\n\nbody"
        out = self.t.merge_frontmatter_tags(fm, ["release", "fts5", "bm25"], 15)
        self.assertIn("tags: [gowth-mem, release, fts5, bm25]", out)
        self.assertIn("slug: x", out)
        self.assertTrue(out.rstrip().endswith("body"))

    def test_idempotent(self):
        fm = "---\ntags: [a, b]\n---\n\nbody"
        once = self.t.merge_frontmatter_tags(fm, ["b", "c"], 15)
        twice = self.t.merge_frontmatter_tags(once, ["b", "c"], 15)
        self.assertEqual(once, twice)

    def test_cap_respected(self):
        fm = "---\ntags: [a, b, c]\n---\n\nbody"
        out = self.t.merge_frontmatter_tags(fm, ["d", "e", "f", "g"], 5)
        tags = self.t._tags_from_frontmatter(out)
        self.assertEqual(len(tags), 5)

    def test_adds_tags_key_when_absent(self):
        fm = "---\nslug: x\ntitle: X\n---\n\nbody"
        out = self.t.merge_frontmatter_tags(fm, ["new"], 15)
        self.assertIn("tags: [new]", out)
        self.assertIn("slug: x", out)

    def test_block_form_parsed(self):
        fm = "---\nslug: x\ntags:\n  - a\n  - b\n---\n\nbody"
        out = self.t.merge_frontmatter_tags(fm, ["b", "c"], 15)
        tags = self.t._tags_from_frontmatter(out)
        self.assertEqual(tags, ["a", "b", "c"])

    def test_no_frontmatter_prepends_minimal_block(self):
        text = "## [exp] something happened"
        out = self.t.merge_frontmatter_tags(text, ["foo", "bar"], 15)
        self.assertTrue(out.startswith("---\ntags: [foo, bar]\n---\n"))
        self.assertIn("## [exp] something happened", out)


class DedupHashStabilityTests(unittest.TestCase):
    """The SHA-1 hash of an entry must be identical with/without inline tags."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_tags_dedup_")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        self.t = load_module("gowth_tags_dedup", SCRIPTS / "_tags.py")

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)

    def test_strip_tags_text_makes_hash_stable(self):
        import hashlib
        entry = "[decision] use FTS5 for recall because keyword outranks body"
        tags = self.t.extract_tags(entry)
        tagged = self.t.apply_inline_tags(entry, tags)
        self.assertNotEqual(entry, tagged)  # tags actually appended
        h_plain = hashlib.sha1(self.t.strip_tags_text(entry).encode()).hexdigest()[:16]
        h_tagged = hashlib.sha1(self.t.strip_tags_text(tagged).encode()).hexdigest()[:16]
        self.assertEqual(h_plain, h_tagged)


class AppendEntryWiringTests(unittest.TestCase):
    def test_append_entry_writes_suffix_and_frontmatter_union_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            _scaffold(home)
            code = (
                "import sys; sys.path.insert(0,'hooks/scripts')\n"
                "from _topic import append_entry\n"
                "from _tags import _tags_from_frontmatter, TAG_TOKEN_RE\n"
                "c='[decision] use FTS5 weighted BM25 for recall because keyword outranks body'\n"
                "p,w=append_entry(c, ws='w1')\n"
                "assert w, 'first write should succeed'\n"
                "txt=p.read_text()\n"
                "# inline suffix present on the entry line\n"
                "assert '  #' in txt, 'no inline tag suffix: '+txt\n"
                "assert txt.startswith('---'), 'no frontmatter: '+txt\n"
                "t1=_tags_from_frontmatter(txt)\n"
                "assert t1, 'no frontmatter tags'\n"
                "# second identical write: frontmatter tags must not duplicate\n"
                "p2,w2=append_entry(c, ws='w1')\n"
                "t2=_tags_from_frontmatter(p2.read_text())\n"
                "assert len(t2)==len(set(t2)), 'duplicate frontmatter tags: '+str(t2)\n"
                "assert set(t1)==set(t2), 'tag set changed: '+str(t1)+' vs '+str(t2)\n"
                "print('ok')\n"
            )
            out = _run_in_home(code, home, {"GOWTH_WORKSPACE": "w1"})
            self.assertIn("ok", out.stdout)

    def test_tags_disabled_writes_no_suffix(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            _scaffold(home, tags_enabled=False)
            code = (
                "import sys; sys.path.insert(0,'hooks/scripts')\n"
                "from _topic import append_entry\n"
                "c='[decision] use FTS5 for recall because it is faster than grep'\n"
                "p,w=append_entry(c, ws='w1')\n"
                "assert w\n"
                "txt=p.read_text()\n"
                "assert '#' not in txt, 'unexpected tags when disabled: '+txt\n"
                "print('ok')\n"
            )
            out = _run_in_home(code, home, {"GOWTH_WORKSPACE": "w1"})
            self.assertIn("ok", out.stdout)


class DenylistRoutingTests(unittest.TestCase):
    def test_akia_placeholder_does_not_mint_topic(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            _scaffold(home)
            code = (
                "import sys; sys.path.insert(0,'hooks/scripts')\n"
                "from _topic import route\n"
                "slug,p,hint=route('AKIAIOSFODNN7EXAMPLE placeholder key leaked', ws='w1')\n"
                "assert not slug.startswith('akia'), 'minted akia topic: '+slug\n"
                "assert slug=='misc', 'expected misc, got '+slug\n"
                "print('slug='+slug)\n"
            )
            out = _run_in_home(code, home, {"GOWTH_WORKSPACE": "w1"})
            self.assertIn("slug=misc", out.stdout)

    def test_guard_new_slug_patterns(self):
        topic = load_module("gowth_topic_guard", SCRIPTS / "_topic.py")
        for bad in ("akiaxyz123", "example-thing", "placeholder", "redacted-x",
                    "xxxx", "todo", "test", "test-3"):
            self.assertEqual(topic._guard_new_slug(bad, "misc"), "misc", f"{bad} not guarded")
        for ok in ("ema-cross", "risk-manager", "gold-futures"):
            self.assertEqual(topic._guard_new_slug(ok, "misc"), ok, f"{ok} wrongly guarded")


class BackfillTests(unittest.TestCase):
    def _make_aspect(self, home: Path, ws: str, name: str, body: str) -> Path:
        d = home / "workspaces" / ws / "ema-cross"
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        p.write_text(body)
        return p

    def test_backfill_dry_run_then_apply(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            _scaffold(home)
            today = date.today().isoformat()
            # Aspect file WITH frontmatter but empty tags.
            self._make_aspect(
                home, "w1", f"{today}-signal.md",
                "---\nslug: ema-cross-signal\ntype: aspect\ntags: []\n---\n\n"
                "## [decision] use EMA crossover with ATR filter because it reduces whipsaw\n"
                "body line here\n",
            )
            t = load_module("gowth_tags_bf", SCRIPTS / "_tags.py")
            os.environ["GOWTH_MEM_HOME"] = str(home)
            try:
                dry = t.backfill(ws="w1", apply=False)
                self.assertFalse(dry["applied"])
                self.assertEqual(dry["changed"], 1, f"expected 1 change, got {dry}")
                # Dry-run must NOT write.
                p = home / "workspaces" / "w1" / "ema-cross" / f"{today}-signal.md"
                self.assertIn("tags: []", p.read_text())

                res = t.backfill(ws="w1", apply=True)
                self.assertTrue(res["applied"])
                self.assertEqual(res["changed"], 1)
                after = t._tags_from_frontmatter(p.read_text())
                self.assertTrue(after, "no tags written on apply")
                # Tags come from the ENTRY content, not the folder slug.
                self.assertIn("crossover", after)

                # Idempotent: a second apply changes nothing.
                res2 = t.backfill(ws="w1", apply=True)
                self.assertEqual(res2["changed"], 0)
            finally:
                os.environ.pop("GOWTH_MEM_HOME", None)

    def test_backfill_never_rewrites_entry_lines(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            _scaffold(home)
            today = date.today().isoformat()
            entry = "## [decision] use EMA crossover with ATR filter because it reduces whipsaw"
            self._make_aspect(
                home, "w1", f"{today}-x.md",
                f"---\nslug: ema-cross-x\ntype: aspect\ntags: []\n---\n\n{entry}\nbody\n",
            )
            t = load_module("gowth_tags_bf2", SCRIPTS / "_tags.py")
            os.environ["GOWTH_MEM_HOME"] = str(home)
            try:
                t.backfill(ws="w1", apply=True)
                p = home / "workspaces" / "w1" / "ema-cross" / f"{today}-x.md"
                text = p.read_text()
                # The entry line itself is untouched (no inline #tags injected).
                self.assertIn(entry + "\n", text)
            finally:
                os.environ.pop("GOWTH_MEM_HOME", None)


# The pre-v4.7.6 strip_tags, kept as the reference implementation: dedup hashes
# stored in index.db were computed with it, so the rewrite must match it exactly.
_OLD_STRIP_RE = re.compile(
    r"\s*(?:#[A-Za-z0-9_][A-Za-z0-9._-]*)(?:\s+#[A-Za-z0-9_][A-Za-z0-9._-]*)*\s*$")


class TokenBoundaryV476Tests(unittest.TestCase):
    """v4.7.6: identifier patterns start only at a token start."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_tags_bound_")
        self._prev_home = os.environ.get("GOWTH_MEM_HOME")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        self.t = load_module("gowth_tags_bound", SCRIPTS / "_tags.py")

    def tearDown(self):
        if self._prev_home is None:
            os.environ.pop("GOWTH_MEM_HOME", None)
        else:
            os.environ["GOWTH_MEM_HOME"] = self._prev_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_capitalized_compounds_stay_whole(self):
        # Live-vault fragments: Stop-hook → #top-hook, Self-review → #elf-review,
        # Port-forwarded → #ort-forwarded, FTS5-only → #5-only.
        for text, whole, fragment in (
            ("Stop-hook path fires the directive", "stop-hook", "top-hook"),
            ("Self-review loop scores the session", "self-review", "elf-review"),
            ("Port-forwarded to the replica", "port-forwarded", "ort-forwarded"),
            ("FTS5-only index for recall", "fts5-only", "5-only"),
        ):
            tags = self.t.extract_tags(text)
            self.assertIn(whole, tags, (text, tags))
            self.assertNotIn(fragment, tags, (text, tags))

    def test_no_identifier_tag_starts_mid_word(self):
        lines = [
            "[exp] Pass-through guard uses find -newermt matching FLUSH_GRACE=300",
            "[exp] xiaozhi ESP32-S3 (native USB-JTAG) intermittently drops its serial port",
            "[decision] Rules-system target = the toc repo because 1tokenai is frozen",
            "[exp] LR-BC54 scorecard: APPROVED-WITH-CONDITIONS since B6 PASS",
            "[tool] fooBar-baz and 0xdead_beef in the 2fa_token path",
        ]
        checked = 0
        for ln in lines:
            low = ln.lower()
            for tag in self.t.extract_tags(ln):
                # Identifier tags appear verbatim; prose bigrams (xiaozhi-native)
                # join two separate words and are not identifiers.
                if not re.search(r"[-._]", tag) or tag not in low:
                    continue
                checked += 1
                self.assertRegex(low, r"(?<![a-z0-9])" + re.escape(tag),
                                 f"{tag!r} starts inside a word of {ln!r}")
        self.assertGreaterEqual(checked, 8)  # the property was actually exercised

    def test_dotfiles_and_digit_led_filenames_stay_whole(self):
        self.assertIn("claude.json", self.t.extract_tags("`teammateDefaultModel` lives in `.claude.json`"))
        self.assertIn("gitlab-ci.yml", self.t.extract_tags("edit .gitlab-ci.yml before the deploy stage"))
        self.assertIn("01-db-findings.md", self.t.extract_tags("constraints from cleanup/01-db-findings.md"))

    def test_leading_underscore_identifier_keeps_its_name(self):
        self.assertIn("run_forget_daily", self.t.extract_tags("_run_forget_daily archives journals"))

    def test_version_wildcards_are_not_tags(self):
        tags = self.t.extract_tags("pin requests v2.x or urllib3 1.x for the vendored client")
        self.assertFalse({"v2.x", "2.x", "1.x"} & set(tags), tags)
        self.assertIn("urllib3", tags)

    def test_numbered_steps_and_extensions_are_not_identifiers(self):
        # Review L3: a digit start made `1.Install` an identifier and the
        # wildcard filter ate `2.xlsx` — HEAD tagged the plain words.
        self.assertFalse({"1.install", "2.restart"} & set(self.t.extract_tags(
            "[exp] 1.Install the plugin then 2.Restart Claude Code")))
        tags = self.t.extract_tags("[exp] export to 2.xlsx and 1.xml then diff")
        self.assertIn("xlsx", tags)
        self.assertIn("xml", tags)
        self.assertNotIn("4.dist-info", self.t.extract_tags("[exp] pin numpy-1.26.4.dist-info in the lock"))

    def test_long_identifiers_are_tagged_whole(self):
        # Review M4: router aspect filenames reach 74 chars and FQNs / env vars
        # run past 80 — a 64-char cap dropped them AND (already blanked) their
        # words, so the entry lost its most specific tags.
        for text, whole in (
            ("[exp] see 2026-09-28-checkpoints-overflowed-precompact-regression-transcript.md for it",
             "2026-09-28-checkpoints-overflowed-precompact-regression-transcript.md"),
            ("[exp] org.apache.kafka.clients.consumer.internals.ConsumerCoordinatorRebalanceListener leaks",
             "org.apache.kafka.clients.consumer.internals.consumercoordinatorrebalancelistener"),
            ("[exp] GOWTH_MEM_DISABLE_CAPTURE_FOR_HEADLESS_SUBAGENT_TEAMMATE_SESSIONS_ONLY_V2 disables it",
             "gowth_mem_disable_capture_for_headless_subagent_teammate_sessions_only_v2"),
        ):
            self.assertGreater(len(whole), self.t.MAX_TAG_LEN)
            self.assertIn(whole, self.t.extract_tags(text), text)

    def test_an_over_long_identifier_leaves_its_words_to_prose(self):
        blob = "Zm9vYmFy" * 40          # CamelCase-shaped 320-char run: not an identifier
        tags = self.t.extract_tags(f"[ref] cache blob {blob} expires nightly")
        self.assertLessEqual(max(map(len, tags)), self.t.MAX_TAG_LEN)
        self.assertTrue({"cache", "expires", "nightly"} & set(tags), tags)

    def test_many_distinct_words_stay_fast(self):
        # Pre-fix: substring collapse was O(k²) over every candidate — 88k chars
        # of distinct words took 2.4 s, quadrupling per doubling.
        text = "[exp] " + " ".join(f"word{i:05d}q" for i in range(16000))
        t0 = time.perf_counter()
        tags = self.t.extract_tags(text)
        self.assertLess(time.perf_counter() - t0, 2.0)
        self.assertTrue(tags)

    def test_long_runs_are_linear(self):
        # Pre-fix: 52.6 s for one 40k-char run (each regex re-scanned the run
        # from every start). Linear now: milliseconds. Generous bound, no flake.
        for run in ("a" * 40000, ("Zm9vYmFy" * 5000), ("a1" * 20000), ("a-" * 20000) + "ấ"):
            t0 = time.perf_counter()
            tags = self.t.extract_tags("[ref] blob " + run + " end")
            self.assertLess(time.perf_counter() - t0, 2.0, run[:12])
            # The rest of the entry is still tagged (alone or as a bigram)...
            self.assertTrue(any("blob" in t for t in tags), tags)
            # ...and the run itself never becomes a giant tag.
            self.assertLessEqual(max(map(len, tags)), self.t.MAX_TAG_LEN, run[:12])

    def test_real_write_entry_point_tags_the_whole_compound(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            _scaffold(home)
            code = (
                "import sys; sys.path.insert(0,'hooks/scripts')\n"
                "from _topic import append_entry\n"
                "from _tags import _tags_from_frontmatter\n"
                "c='[exp] Stop-hook path: the Self-review block fires because the counter resets'\n"
                "p,w=append_entry(c, ws='w1')\n"
                "assert w, 'write should succeed'\n"
                "txt=p.read_text()\n"
                "line=[l for l in txt.splitlines() if l.startswith('[exp] Stop-hook')][0]\n"
                "print('LINE', line)\n"
                "print('FM', _tags_from_frontmatter(txt))\n"
            )
            out = _run_in_home(code, home, {"GOWTH_WORKSPACE": "w1"}).stdout
            line = [ln for ln in out.splitlines() if ln.startswith("LINE ")][0]
            self.assertIn("#stop-hook", line)
            self.assertIn("#self-review", line)
            self.assertNotIn("#top-hook", line)
            self.assertNotIn("#elf-review", line)
            fm = [ln for ln in out.splitlines() if ln.startswith("FM ")][0]
            self.assertIn("'stop-hook'", fm)


class StripTagsLinearV476Tests(unittest.TestCase):
    """v4.7.6: strip_tags matches the reversed line — linear, same output."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_tags_strip476_")
        self._prev_home = os.environ.get("GOWTH_MEM_HOME")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        self.t = load_module("gowth_tags_strip476", SCRIPTS / "_tags.py")

    def tearDown(self):
        if self._prev_home is None:
            os.environ.pop("GOWTH_MEM_HOME", None)
        else:
            os.environ["GOWTH_MEM_HOME"] = self._prev_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_identical_to_the_old_regex(self):
        rnd = random.Random(4760)
        alphabet = [" ", "\t", "\n", "\r", "\x1c", " ", " ", "#", "#", "#",
                    "a", "Z", "0", "_", ".", "-", ",", "ấ", "/"]
        for _ in range(40000):
            s = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 24)))
            self.assertEqual(self.t.strip_tags(s), _OLD_STRIP_RE.sub("", s), repr(s))
        for _ in range(20000):
            body = "".join(rnd.choice("ab #.-_ \t") for _ in range(rnd.randint(0, 12)))
            tags = "".join(rnd.choice([" ", "  ", "\t"]) + "#"
                           + "".join(rnd.choice("ab0_.-") for _ in range(rnd.randint(0, 4)))
                           for _ in range(rnd.randint(0, 4)))
            s = body + tags + rnd.choice(["", " ", "\n", " x", "#", "\r\n"])
            self.assertEqual(self.t.strip_tags(s), _OLD_STRIP_RE.sub("", s), repr(s))

    def test_long_whitespace_runs_are_linear(self):
        # Pre-fix: 2.3 s for 40k spaces (quadratic); 80k would take ~9 s.
        cases = (
            (" " * 80000 + "x", " " * 80000 + "x"),          # no tags: untouched
            ("x" + " " * 80000 + "#a #b", "x"),              # tags after a huge gap: stripped
            (" " * 80000 + "#a", ""),
            ("x " + "#a " * 26000 + "y", "x " + "#a " * 26000 + "y"),  # tag run not at the end
        )
        for line, expected in cases:
            t0 = time.perf_counter()
            self.assertEqual(self.t.strip_tags(line), expected)
            self.assertLess(time.perf_counter() - t0, 1.0)


if __name__ == "__main__":
    unittest.main()
