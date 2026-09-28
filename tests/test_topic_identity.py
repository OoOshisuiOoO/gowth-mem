"""v4.7.6 — topic identity is the FOLDER a file lives in, never its frontmatter.

`route()` / `derive_topic_slug()` took the topic slug from the best-matching
file's frontmatter `slug:`. A dated aspect carries `slug: <topic>-<aspect>`
(`_validate.fix_aspect`), so every time an aspect out-scored its folder's
README, `ensure_topic_folder(<topic>-<aspect>)` minted a README-only sibling
folder (31 in the live vault: devops 16, trade 13, personal 2) and `_lesson`
filed a real lesson inside one. Nested topics got a top-level twin the same
way, and under a symlinked GOWTH_MEM_HOME a legacy flat `<ws>/<name>.md`
match wrote the new aspect loose at the workspace root plus a `<ws>/<ws>/`
folder.

Every test drives a REAL write entry point (`_topic.py --append`, the CLI the
memory teammate calls, or `_lesson.append_lesson`) and asserts both where the
entry landed (positive) and that no sibling appeared.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "hooks" / "scripts"
TODAY = date.today().isoformat()


def _env(home: Path, **extra: str) -> dict:
    env = dict(os.environ, GOWTH_MEM_HOME=str(home))
    for k in ("GOWTH_WORKSPACE", "AI_AGENT", "CLAUDE_SUBAGENT"):
        env.pop(k, None)
    env.update(extra)
    return env


def _vault(home: Path, ws: str = "w1") -> Path:
    wsd = home / "workspaces" / ws
    wsd.mkdir(parents=True)
    (wsd / "workspace.json").write_text("{}")
    (home / "settings.json").write_text(json.dumps({
        "topic_routing": {"min_keyword_overlap": 3, "default_topic": "misc"},
    }))
    return wsd


def _write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def _readme(slug: str) -> str:
    return (f"---\nslug: {slug}\ntitle: {slug.title()}\ntype: misc\nstatus: active\n---\n\n"
            f"# {slug.title()}\n")


def _aspect(topic: str, aspect: str, body: str) -> str:
    # Exactly what `_validate.fix_aspect` stamps on a dated aspect.
    return (f"---\nslug: {topic}-{aspect}\ntitle: {aspect}\ntype: aspect\ndate: 2026-09-01\n"
            f"topic: {topic}\naspect: {aspect}\n---\n\n{body}\n")


def _append_status(home: Path, entry: str, ws: str = "w1", **env: str) -> tuple[Path, str]:
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "_topic.py"), "--append", entry, "--ws", ws],
        env=_env(home, **env), capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, r.stderr
    path, status = r.stdout.strip().split("\t")
    return Path(path), status


def _append(home: Path, entry: str, ws: str = "w1", **env: str) -> Path:
    path, status = _append_status(home, entry, ws, **env)
    assert status == "written", status
    return path


def _lesson(home: Path, *args: str) -> str:
    r = subprocess.run([sys.executable, str(SCRIPTS / "_lesson.py"), *args, "--ws", "w1"],
                       env=_env(home), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout


def _dirs(wsd: Path) -> list[str]:
    return sorted(str(p.relative_to(wsd)) for p in wsd.rglob("*") if p.is_dir())


def _tree(home: Path) -> list[str]:
    """Every path in the vault except the debug log (a refusal is logged)."""
    return sorted(str(p.relative_to(home)) for p in home.rglob("*")
                  if p.relative_to(home).parts[0] != "logs")


class TopicIdentityTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.home = Path(self._td.name)
        self.wsd = _vault(self.home)

    def tearDown(self):
        self._td.cleanup()

    def test_aspect_frontmatter_slug_never_mints_sibling_folder(self):
        _write(self.wsd / "alpha" / "00-README.md", _readme("alpha"))
        _write(self.wsd / "alpha" / "2026-09-01-zebra-quartz-nebula.md", _aspect(
            "alpha", "zebra-quartz-nebula",
            "- [exp] zebra quartz nebula lantern migration because the lantern cache went stale"))
        p = _append(self.home, "[exp] zebra quartz nebula lantern retry: flush the lantern cache "
                               "because stale entries return")
        # Positive: the aspect out-scored the README and the entry went to ITS folder.
        self.assertEqual(p.parent.name, "alpha")
        self.assertTrue(p.name.startswith(f"{TODAY}-"), p.name)
        self.assertIn("lantern retry", p.read_text())
        # The pre-fix junk: `<topic>-<aspect>/00-README.md`.
        self.assertEqual(_dirs(self.wsd), ["alpha"])

    def test_nested_topic_match_writes_in_place_with_no_top_level_twin(self):
        nested = self.wsd / "domain" / "orchid"
        _write(nested / "00-README.md", _readme("orchid"))
        _write(nested / "2026-09-02-harbor-signal.md", _aspect(
            "orchid", "harbor-signal",
            "- [exp] harbor signal beacon relay failed because the relay token expired"))
        p = _append(self.home, "[exp] harbor signal beacon relay retried: rotate the relay token "
                               "because it expires hourly")
        self.assertEqual(p.parent, nested)
        self.assertEqual(_dirs(self.wsd), ["domain", "domain/orchid"])

    def test_auto_routed_lesson_lands_in_the_matched_nested_folder(self):
        nested = self.wsd / "domain" / "orchid"
        _write(nested / "00-README.md", _readme("orchid"))
        _write(nested / "2026-09-02-harbor-signal.md", _aspect(
            "orchid", "harbor-signal",
            "- [exp] harbor signal beacon relay failed because the relay token expired"))
        code = (
            "import sys; sys.path.insert(0, sys.argv[1])\n"
            "from _lesson import append_lesson\n"
            "print(append_lesson('harbor signal beacon relay dropped', 'restarted the harbor relay',"
            " 'relay token expired because rotation stopped', 'rotate the harbor relay token hourly',"
            " 'runbook', ws='w1'))\n"
        )
        r = subprocess.run([sys.executable, "-c", code, str(SCRIPTS)], env=_env(self.home),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        lessons = nested / "lessons.md"
        self.assertTrue(lessons.is_file(), r.stdout)
        self.assertIn("harbor signal beacon relay dropped", lessons.read_text())
        self.assertEqual(_dirs(self.wsd), ["domain", "domain/orchid"])

    def test_matched_folder_without_landing_gets_readme_in_place(self):
        # Aspects but no README (e.g. README lost in a sync) → README added
        # INSIDE that folder, not in a slug-derived sibling.
        _write(self.wsd / "beta" / "2026-09-01-copper-valve.md", _aspect(
            "beta", "copper-valve",
            "- [exp] copper valve pressure spike because the copper seal wore out"))
        p = _append(self.home, "[exp] copper valve pressure spike again: replace the copper seal "
                               "because wear returns")
        self.assertEqual(p.parent.name, "beta")
        self.assertTrue((self.wsd / "beta" / "00-README.md").is_file())
        self.assertEqual(_dirs(self.wsd), ["beta"])

    def test_legacy_folder_note_is_not_shadowed_by_a_skeleton_readme(self):
        note = _write(self.wsd / "gamma" / "gamma.md",
                      "# Gamma\n\n- [exp] amber prism refraction drift because the amber mount flexed\n")
        p = _append(self.home, "[exp] amber prism refraction drift again: stiffen the amber mount "
                               "because flex returns")
        self.assertEqual(p.parent.name, "gamma")          # it matched the legacy topic
        self.assertIn("stiffen the amber mount", p.read_text())
        # topic_landing() prefers 00-README.md; a skeleton would hide the real note.
        self.assertFalse((self.wsd / "gamma" / "00-README.md").exists())
        self.assertIn("amber prism", note.read_text())

    def test_reserved_nested_segment_still_writes_the_entry(self):
        docs = self.wsd / "domain" / "docs"
        _write(docs / "2026-09-03-violet-comet.md", _aspect(
            "docs", "violet-comet",
            "- [exp] violet comet trajectory drifted because the comet telemetry lagged"))
        p = _append(self.home, "[exp] violet comet trajectory recomputed because comet telemetry "
                               "lag skews the fit")
        self.assertEqual(p.parent, docs)
        self.assertIn("recomputed", p.read_text())
        self.assertEqual(_dirs(self.wsd), ["domain", "domain/docs"])

    def test_route_is_deterministic_across_hash_seeds(self):
        # Equal-length keywords used to be ordered by randomised str hashing.
        entry = "[exp] alphaaaa betaaaaa gammaaaa deltaaaa epsilonn zetaaaaa"
        code = ("import sys; sys.path.insert(0, sys.argv[1])\n"
                "from _topic import route\n"
                "slug, p, _ = route(sys.argv[2], ws='w1')\n"
                "print(slug, p.name)\n")
        outs = set()
        for seed in ("1", "2", "3", "4"):
            r = subprocess.run([sys.executable, "-c", code, str(SCRIPTS), entry],
                               env=_env(self.home, PYTHONHASHSEED=seed),
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            outs.add(r.stdout.strip())
        self.assertEqual(len(outs), 1, outs)
        # Equal lengths keep their order of first appearance in the text.
        self.assertEqual(outs.pop(), f"alphaaaa-betaaaaa {TODAY}-alphaaaa-betaaaaa-gammaaaa-deltaaaa-epsilonn.md")

    def test_gate_mandated_because_does_not_name_new_topics(self):
        # Round-2 review L7: an alphabetical tie-break made `because` (the
        # gate requires a rationale) a systematic slug word: `because-cooling`.
        p = _append(self.home, "[exp] granite furnace ember kiln relit: preheat the kiln because "
                               "uneven cooling cracks it")
        self.assertEqual(p.parent.name, "granite-furnace")

    def _list(self) -> str:
        r = subprocess.run([sys.executable, str(SCRIPTS / "_topic.py"), "--list", "--ws", "w1"],
                           env=_env(self.home), capture_output=True, text=True, timeout=60)
        return r.stdout

    def test_domain_folder_is_not_turned_into_a_topic(self):
        # A loose aspect inside a DOMAIN (a folder of topic folders — here a
        # grandchild): a README there would make it a topic and hide `orchid`
        # from iter_topic_landings / list_topics / the MOC (review L1, L2),
        # and writing "into its folder" would pile entries where no MOC or
        # list looks (L4) — so the loose file is no match candidate at all.
        _write(self.wsd / "domain" / "sub" / "orchid" / "00-README.md", _readme("orchid"))
        _write(self.wsd / "domain" / "2026-09-03-beacon-relay.md", _aspect(
            "domain", "beacon-relay",
            "- [exp] beacon relay handshake stalls because the relay buffer fills"))
        p = _append(self.home, "[exp] beacon relay handshake stalls again: drain the relay buffer "
                               "because it fills")
        self.assertNotIn(self.wsd / "domain", (p.parent, p.parent.parent))
        self.assertTrue((p.parent / "00-README.md").is_file())   # landed in a real topic
        self.assertFalse((self.wsd / "domain" / "00-README.md").exists())
        self.assertIn("orchid", self._list())

    def test_loose_root_level_aspect_is_not_promoted_to_a_date_named_folder(self):
        # Artifact of the symlinked-home bug: a dated aspect loose at the root.
        _write(self.wsd / "2026-09-01-granite-kiln.md",
               "- [exp] granite furnace ember kiln cracked because the kiln cooled unevenly\n")
        p = _append(self.home, "[exp] granite furnace ember kiln relit: preheat the kiln because "
                               "uneven cooling cracks it")
        self.assertFalse((self.wsd / "2026-09-01-granite-kiln").exists(), _dirs(self.wsd))
        self.assertEqual(p.parent.name, "granite-furnace")        # a real, keyword-named topic

    def test_reserved_word_as_new_topic_does_not_crash_the_write(self):
        # "research" is a reserved workspace subdir: the new-slug path used to
        # hand it to ensure_topic_folder → ValueError, and the entry was lost.
        code = ("import sys; sys.path.insert(0, sys.argv[1])\n"
                "from _topic import route\n"
                "slug, p, _ = route('[exp] see the research', ws='w1')\n"
                "print(slug, p.parent.name)\n")
        r = subprocess.run([sys.executable, "-c", code, str(SCRIPTS)], env=_env(self.home),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)                # was: ValueError (reserved)
        self.assertEqual(r.stdout.split(), ["misc", "misc"])
        p = _append(self.home, "[exp] the research, and the research, and all of the research")
        self.assertEqual(p.parent.name, "misc")
        _write(self.wsd / "research.md", "# r\n\n- [exp] cobalt ledger drift because cobalt rounding\n")
        p = _append(self.home, "[exp] cobalt ledger drift again because cobalt rounding repeats")
        self.assertNotEqual(p.parent.name, "research")

    def test_gate_rejected_entry_creates_nothing_and_says_why(self):
        before = _dirs(self.wsd)
        p, status = _append_status(self.home, "[ref] kraken ledger reconciliation uses nightly snapshots")
        self.assertTrue(status.startswith("rejected:"), status)   # was reported as "duplicate"
        self.assertFalse(p.exists())
        self.assertEqual(_dirs(self.wsd), before)                 # no README-only folder left behind

    def test_gate_rejected_lesson_creates_nothing_and_says_so(self):
        before = _tree(self.home)
        out = _lesson(self.home, "--symptom", "x", "--tried", "y", "--root", "z", "--fix", "w")
        self.assertIn("not appended (rejected:", out)            # was "appended: <path>"
        # No file at all — the MOC refresh used to run anyway and create _MAP.md files.
        self.assertEqual(_tree(self.home), before)

    def test_route_preview_creates_nothing(self):
        before = _tree(self.home)
        r = subprocess.run([sys.executable, str(SCRIPTS / "_topic.py"), "--route",
                            "[ref] kraken ledger reconciliation uses nightly snapshots", "--ws", "w1"],
                           env=_env(self.home), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        slug, path, _ = r.stdout.rstrip("\n").split("\t")
        self.assertTrue(slug and path.endswith(".md"), r.stdout)
        self.assertEqual(_tree(self.home), before)

    def test_explicit_topic_naming_a_domain_or_an_ambiguous_name_is_refused(self):
        for t in ("redis", "pg"):
            _write(self.wsd / "infra" / t / "00-README.md", _readme(t))
        _write(self.wsd / "a" / "orchid" / "00-README.md", _readme("orchid"))
        _write(self.wsd / "b" / "orchid" / "00-README.md", _readme("orchid"))
        for topic, expect in (("infra", "is a domain folder"), ("orchid", "is ambiguous")):
            before = _tree(self.home)
            r = subprocess.run([sys.executable, str(SCRIPTS / "_lesson.py"), "--topic", topic,
                                "--symptom", "cache stampede on cold start", "--tried", "raised the pool size",
                                "--root", "no request coalescing because the cache has no lock",
                                "--fix", "coalesce misses behind a per-key lock", "--source", "incident",
                                "--ws", "w1"], env=_env(self.home), capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn(expect, r.stdout)
            self.assertEqual(_tree(self.home), before)
        self.assertIn("redis", self._list())

    def test_explicit_topic_lesson_uses_an_existing_nested_topic(self):
        nested = self.wsd / "domain" / "orchid"
        _write(nested / "00-README.md", _readme("orchid"))
        out = _lesson(self.home, "--topic", "orchid", "--symptom", "harbor relay dropped the beacon",
                      "--tried", "restarted the harbor relay twice", "--root",
                      "relay token expired because rotation stopped", "--fix",
                      "rotate the harbor relay token hourly", "--source", "runbook")
        self.assertIn("appended:", out)
        self.assertTrue((nested / "lessons.md").is_file())
        self.assertFalse((self.wsd / "orchid").exists(), _dirs(self.wsd))

    def test_equal_overlap_ties_break_by_path_not_filesystem_order(self):
        for name in ("zulu-topic", "alpha-topic"):   # created in reverse order
            _write(self.wsd / name / "00-README.md",
                   _readme(name) + "\nmagnet lattice resonance damping\n")
        p = _append(self.home, "[exp] magnet lattice resonance damping fails because the damper saturates")
        self.assertEqual(p.parent.name, "alpha-topic")

    def test_explicit_topic_through_a_symlink_out_of_the_vault_is_refused(self):
        # Round-3 review H1: `_ensure_landing` returned early for a folder that
        # already had a landing, skipping the path-escape guard — the lesson
        # went to the symlink's target, never synced or indexed, "appended:".
        outside = Path(self._td.name) / "outside"
        _write(outside / "orchid" / "00-README.md", _readme("orchid"))
        _write(outside / "nested-orchid" / "00-README.md", _readme("nested-orchid"))
        (self.wsd / "orchid").symlink_to(outside / "orchid", target_is_directory=True)
        (self.wsd / "domain").mkdir()
        (self.wsd / "domain" / "nested-orchid").symlink_to(outside / "nested-orchid",
                                                          target_is_directory=True)
        for topic in ("orchid", "nested-orchid"):
            r = subprocess.run([sys.executable, str(SCRIPTS / "_lesson.py"), "--topic", topic,
                                "--symptom", "harbor relay dropped the beacon", "--tried", "restarted it",
                                "--root", "relay token expired because rotation stopped",
                                "--fix", "rotate the relay token hourly", "--source", "runbook",
                                "--ws", "w1"], env=_env(self.home), capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("outside the workspace", r.stdout)
        self.assertEqual(sorted(p.name for p in outside.rglob("lessons.md")), [])

    def test_new_or_default_topic_never_lands_on_a_domain(self):
        # Round-3 review L2: `ensure_topic_folder(slug)` has no domain guard, so
        # a new topic named like an existing DOMAIN gave it a README and hid
        # its topics (`misc/` holding topic folders, the default topic).
        _write(self.wsd / "misc" / "redis-tuning" / "00-README.md", _readme("redis-tuning"))
        code = ("import sys; sys.path.insert(0, sys.argv[1])\n"
                "from _topic import route\n"
                "slug, p, _ = route('[exp] a b c', ws='w1')\n"          # no keyword → default topic
                "print(slug, p.parent.name)\n")
        r = subprocess.run([sys.executable, "-c", code, str(SCRIPTS)], env=_env(self.home),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.split(), ["misc-notes", "misc-notes"])
        self.assertFalse((self.wsd / "misc" / "00-README.md").exists())
        _write(self.wsd / "granite-furnace" / "kiln-log" / "00-README.md", _readme("kiln-log"))
        p = _append(self.home, "[exp] granite furnace ember kiln relit: preheat the kiln because "
                               "uneven cooling cracks it")
        self.assertEqual(p.parent.name, "granite-furnace-notes")
        self.assertFalse((self.wsd / "granite-furnace" / "00-README.md").exists())
        listed = self._list()
        self.assertIn("redis-tuning", listed)
        self.assertIn("kiln-log", listed)

    def _route(self, text: str, **env: str) -> list[str]:
        code = ("import sys; sys.path.insert(0, sys.argv[1])\n"
                "from _topic import route\n"
                "slug, p, _ = route(sys.argv[2], ws='w1')\n"
                "print(slug, p.parent.name)\n")
        r = subprocess.run([sys.executable, "-c", code, str(SCRIPTS), text], env=_env(self.home, **env),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.split()

    def test_domain_avoidance_is_complete(self):
        # Round-4 review L1: legacy flat promotion bypassed it, the `-notes`
        # fallback was checked once, and a 60-char slug came back unchanged.
        for t in ("redis", "pg"):
            _write(self.wsd / "infra" / t / "00-README.md", _readme(t))
        _write(self.wsd / "infra.md", "# infra\n\n- [exp] cobalt ledger drift because cobalt rounding\n")
        p = _append(self.home, "[exp] cobalt ledger drift again because cobalt rounding repeats")
        self.assertEqual(p.parent.name, "infra-notes")
        self.assertFalse((self.wsd / "infra" / "00-README.md").exists())
        _write(self.wsd / "misc" / "redis-tuning" / "00-README.md", _readme("redis-tuning"))
        _write(self.wsd / "misc-notes" / "pg-vacuum" / "00-README.md", _readme("pg-vacuum"))
        self.assertEqual(self._route("[exp] a b c"), ["misc-notes-2", "misc-notes-2"])
        listed = self._list()
        for t in ("redis", "pg", "redis-tuning", "pg-vacuum"):
            self.assertIn(t, listed)
        long = "a" * 55 + "-bcde"                                   # 60 chars
        _write(self.wsd / long / "inner" / "00-README.md", _readme("inner"))
        code = ("import sys; sys.path.insert(0, sys.argv[1])\n"
                "from pathlib import Path\nimport _topic as T\n"
                "print(T._avoid_domain(sys.argv[2], Path(sys.argv[3])))\n")
        r = subprocess.run([sys.executable, "-c", code, str(SCRIPTS), long, str(self.wsd)],
                           env=_env(self.home), capture_output=True, text=True, timeout=60)
        cand = r.stdout.strip()
        self.assertNotEqual(cand, long)
        self.assertLessEqual(len(cand), 60)
        self.assertTrue(cand.endswith("-notes"), cand)

    def test_unusable_default_topic_setting_falls_back_to_misc(self):
        # Round-4 review L2: `../../..` made planning scan the vault's parent;
        # `docs` / `Misc` / `misc notes` made keyword-less entries unroutable.
        for bad in ("../../..", "docs", "Misc", "misc notes"):
            (self.home / "settings.json").write_text(json.dumps(
                {"topic_routing": {"min_keyword_overlap": 3, "default_topic": bad}}))
            self.assertEqual(self._route("[exp] a b c"), ["misc", "misc"], bad)

    def _status_with(self, patch: str) -> subprocess.CompletedProcess:
        code = ("import sys; sys.path.insert(0, sys.argv[1])\n"
                "import _topic as T\n" + patch +
                "print(T.append_entry_status('[exp] harbor relay token rotation fails because the relay '"
                "'clock drifts', ws='w1')[1])\n")
        return subprocess.run([sys.executable, "-c", code, str(SCRIPTS)], env=_env(self.home),
                              capture_output=True, text=True, timeout=60)

    def test_unroutable_is_reported_not_raised(self):
        # Both stages: materialising (an escaping folder) and planning (no
        # free name beside a domain — round-5 review L2: that mapping had no test).
        for patch in ("def refuse(*a, **k): raise T.UnroutableError('resolves outside the workspace')\n"
                      "T._materialise = refuse\n",
                      "def refuse(*a, **k): raise T.UnroutableError('no free topic name')\n"
                      "T._avoid_domain = refuse\n"):
            r = self._status_with(patch)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), "rejected:unroutable")

    def test_a_plain_value_error_is_a_bug_not_a_refusal(self):
        # Round-5 review L1: catching every ValueError turned a settings typo
        # into a silent "rejected:unroutable" the teammate never retries.
        r = self._status_with("def broken(*a, **k): raise ValueError('a real bug')\n"
                              "T._materialise = broken\n")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("a real bug", r.stderr)

    def test_malformed_min_keyword_overlap_falls_back(self):
        (self.home / "settings.json").write_text(json.dumps(
            {"topic_routing": {"min_keyword_overlap": "three"}}))
        p = _append(self.home, "[exp] harbor relay token rotation fails because the relay clock drifts")
        self.assertIn("harbor relay token rotation", p.read_text())

    def test_moc_rebuild_never_writes_through_a_symlinked_topic(self):
        # Round-4 review L4 (pre-existing): the MOC rebuild rewrote a README
        # that lives in another repo, through a symlinked topic folder.
        outside = Path(self._td.name) / "other-repo" / "orchid"
        _write(outside / "00-README.md", _readme("orchid") + "\nhand-written notes that live in another repo\n")
        before = (outside / "00-README.md").read_bytes()
        (self.wsd / "orchid").symlink_to(outside, target_is_directory=True)
        r = subprocess.run([sys.executable, str(SCRIPTS / "_moc.py"), "--ws", "w1"], env=_env(self.home),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((outside / "00-README.md").read_bytes(), before)


class SymlinkedHomeTests(unittest.TestCase):
    def test_flat_legacy_match_promotes_to_a_folder_under_a_symlinked_home(self):
        with tempfile.TemporaryDirectory() as real_td, tempfile.TemporaryDirectory() as link_td:
            real = Path(real_td)
            link = Path(link_td) / "home"
            link.symlink_to(real)
            wsd = _vault(real)
            _write(wsd / "legacyflat.md",
                   "# legacy\n\n- [exp] granite furnace ember kiln cracked because the kiln cooled unevenly\n")
            p = _append(link, "[exp] granite furnace ember kiln relit: preheat the kiln because "
                              "uneven cooling cracks it")
            # Pre-fix: the aspect landed loose at the workspace root and a
            # `<ws>/<ws>/` folder appeared (resolved root != walk paths).
            self.assertEqual(p.parent.name, "legacyflat")
            self.assertTrue((wsd / "legacyflat" / "00-README.md").is_file())
            self.assertEqual(sorted(x.name for x in wsd.iterdir()),
                             ["legacyflat", "legacyflat.md", "workspace.json"])


if __name__ == "__main__":
    unittest.main()
