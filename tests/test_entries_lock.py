"""v4.9 (spec §2.5, §2.6): `_topic.py --append --topic`, `rejected:bad_ws`,
and the shared `entries-<ws>` lock around every topic-file read-modify-write.

Before v4.9 the append path read an aspect, appended, wrote it, then re-read
it for the tag merge and again for `fix_aspect` — three unlocked writes. Two
sessions (or a teammate and a lesson, a backfill, a `--fix`) appending to the
same file could interleave and silently drop each other's entries.

Every test drives a real entry point (`_topic.py` / `_lesson.py` /
`_tags.py` / `_validate.py` CLIs, or the library functions those CLIs call, in
their own processes) against a scratch GOWTH_MEM_HOME.
"""
from __future__ import annotations

import json
import os
import re
import shutil
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
_SCRUB = ("AI_AGENT", "GOWTH_WORKSPACE", "CLAUDE_SUBAGENT", "GOWTH_MEM_OFF", "CLAUDE_CODE_ENTRYPOINT")
# Five long words shared by every concurrent entry: the aspect slug comes from
# the longest keywords, so all 100 entries land in ONE file (maximum contention).
COMMON = "migrationflowing zebraquartzite nebulalanterns cachewarmingup harborsignaled"


def _env(home: Path, **extra: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in _SCRUB}
    env["GOWTH_MEM_HOME"] = str(home)
    env.update(extra)
    return env


def _w(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _skeleton(slug: str, summary: str = "S") -> str:
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


def _prune_takes_entries_lock() -> bool:
    """WP-A3 (spec §3.3) makes `_prune.prune_file` hold `entries-<ws>`; until
    that lands, a prune rewrites every file it reads (it drops the trailing
    newline) and races any appender — the §2.6 prune leg cannot pass."""
    src = (SCRIPTS / "_prune.py").read_text(encoding="utf-8")
    return bool(re.search(r"entries_lock\(|file_lock\(\s*f[\"']entries-", src))


class _Vault(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_entries_")
        self.home = Path(self.tmp)
        self.wsd = self.home / "workspaces" / "w1"
        _w(self.wsd / "workspace.json", "{}")
        (self.home / "settings.json").write_text(json.dumps({"layout_version": 3}))
        self.topic = self.wsd / "redis-cache"
        _w(self.topic / "00-README.md", _skeleton("redis-cache"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_py(self, *args: str, **kw) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, *args], capture_output=True, text=True,
                              timeout=kw.pop("timeout", 120), env=kw.pop("env", _env(self.home)), **kw)

    def append(self, entry: str, *extra: str) -> tuple:
        r = self.run_py(str(SCRIPTS / "_topic.py"), "--append", entry, *extra)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        path, status = r.stdout.strip().split("\t")
        return path, status


class TopicFlagTests(_Vault):
    def test_forced_topic_writes_into_that_existing_topic(self):
        # The entry's words match `other` strongly; --topic overrides routing.
        other = _w(self.wsd / "other" / "00-README.md", _skeleton("other"))
        _w(other.parent / "2026-10-01-relay.md", "---\n---\n- [exp] beacon relay harbor lattice drifts\n")
        entry = "[exp] beacon relay harbor lattice drifts again because the cache evicts keys"
        path, status = self.append(entry, "--ws", "w1")
        self.assertEqual((Path(path).parent.name, status), ("other", "written"))     # control
        entry2 = entry.replace("again", "twice")
        path, status = self.append(entry2, "--ws", "w1", "--topic", "redis-cache")
        self.assertEqual(status, "written")
        p = Path(path)
        self.assertEqual(p.parent, self.topic)
        self.assertTrue(p.name.startswith(f"{TODAY}-"), p.name)
        text = p.read_text()
        first = next(ln for ln in text.splitlines() if ln.startswith("[exp] beacon relay"))
        self.assertIn("twice because the cache evicts keys", first)
        self.assertIn(" #", first)                                   # inline tags applied
        for field in ("type: aspect", "topic: redis-cache", f"date: {TODAY}"):
            self.assertIn(field, text)                               # fix_aspect ran
        # The gate still applies to a forced topic.
        path, status = self.append("[ref] redis maxmemory is four gigabytes on prod", "--ws", "w1",
                                   "--topic", "redis-cache")
        self.assertEqual(status, "rejected:ref_without_source")

    def test_forced_nested_topic_and_dedup(self):
        nested = _w(self.wsd / "infra" / "orchid" / "00-README.md", _skeleton("orchid")).parent
        r = self.run_py(str(SCRIPTS / "_index.py"), "--no-archive")            # dedup needs index.db
        self.assertEqual(r.returncode, 0, r.stderr)
        entry = "[exp] orchid relay drops the first packet because the arp cache is cold"
        snaps, statuses = [], []
        for _ in range(3):
            path, status = self.append(entry, "--ws", "w1", "--topic", "orchid")
            statuses.append(status)
            snaps.append(Path(path).read_bytes())
            self.assertEqual(Path(path).parent, nested)
        self.assertEqual(statuses, ["written", "duplicate", "duplicate"])
        self.assertEqual(snaps[0], snaps[1])
        self.assertEqual(snaps[1], snaps[2])
        self.assertFalse((self.wsd / "orchid").exists())                     # no top-level twin

    def _seed_refusals(self, outside: Path) -> None:
        """HEAD's `--topic` refusal fixtures plus the reserved workspace dirs
        (review cards#3). The `<dir>/<dir>.md` legacy landings make `journal`,
        `skills` and `research` look like topics on EVERY filesystem (so the
        case-sensitive CI fails without the guard too); `memory/MEMORY.md` is
        Claude Code's auto-memory file — the landing case-insensitive APFS
        reads as `memory/memory.md`."""
        for t in ("redis", "pg"):
            _w(self.wsd / "infra" / t / "00-README.md", _skeleton(t))
        _w(self.wsd / "a" / "orchid" / "00-README.md", _skeleton("orchid"))
        _w(self.wsd / "b" / "orchid" / "00-README.md", _skeleton("orchid"))
        _w(outside / "lily" / "00-README.md", _skeleton("lily"))
        _w(outside / "nested-lily" / "00-README.md", _skeleton("nested-lily"))
        (self.wsd / "lily").symlink_to(outside / "lily", target_is_directory=True)
        (self.wsd / "dom").mkdir()
        (self.wsd / "dom" / "nested-lily").symlink_to(outside / "nested-lily", target_is_directory=True)
        (self.wsd / "docs").mkdir()
        _w(self.wsd / "bare" / "2026-01-01-x.md", "- [exp] an aspect with no landing\n")
        _w(self.wsd / "memory" / "MEMORY.md", "# Claude Code auto-memory\n- [Note](note.md)\n")
        _w(self.wsd / "journal" / "journal.md", "# Journal\n")
        _w(self.wsd / "skills" / "skills.md", "# Skills\n")
        _w(self.wsd / "research" / "research.md", "# Research\n")
        # A topic NAME symlinked into a reserved dir.
        (self.wsd / "jlink").symlink_to(self.wsd / "journal", target_is_directory=True)

    RESERVED = ("memory", "journal", "skills", "research", "docs", "jlink")

    def test_forced_topic_refusals_replay_the_lesson_guards(self):
        """HEAD's `_lesson.py --topic` refusals (domain, ambiguous, symlink out
        of the vault — top-level and nested — invalid slug), plus what
        `--topic` adds: no creation (missing name, a folder with no landing),
        plus the reserved workspace dirs — by name or through a symlink — that
        v4.9 refuses before every other guard. Each → `rejected:no_topic`,
        nothing written anywhere."""
        outside = Path(tempfile.mkdtemp(prefix="gowth_outside_"))
        try:
            self._seed_refusals(outside)
            before = _tree(self.home)
            outside_before = _tree(outside)
            entry = "[exp] cache stampede on cold start because nothing coalesces misses"
            for topic in ("infra", "orchid", "lily", "nested-lily", "missing", "bare",
                          "../w1", "Bad Slug", "a/orchid", "") + self.RESERVED:
                path, status = self.append(entry, "--ws", "w1", "--topic", topic)
                self.assertEqual(status, "rejected:no_topic", topic)
            self.assertEqual(_tree(self.home), before)
            self.assertEqual(_tree(outside), outside_before)
            # Positive control: the same command writes a real topic.
            self.assertEqual(self.append(entry, "--ws", "w1", "--topic", "redis-cache")[1], "written")
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_lesson_topic_refuses_reserved_dirs_and_replays_head_guards(self):
        """`_lesson.py --topic` wrote `lessons.md` into ANY existing reserved
        dir (its `ensure` path swallowed `ensure_topic_folder`'s refusal) —
        `memory/lessons.md`, `journal/lessons.md` on every filesystem, and a
        skeleton README through a symlinked name. Now `not appended`, rc 2,
        tree unchanged; HEAD's refusals still refuse through the same CLI."""
        outside = Path(tempfile.mkdtemp(prefix="gowth_outside_"))
        try:
            self._seed_refusals(outside)
            before = _tree(self.home)
            outside_before = _tree(outside)
            for topic in self.RESERVED + ("infra", "orchid", "lily", "nested-lily", "../w1", "Bad Slug"):
                r = self.run_py(str(SCRIPTS / "_lesson.py"), "--ws", "w1", "--topic", topic,
                                "--symptom", "cache stampede on a cold start", "--tried", "warming by hand",
                                "--root", "nothing coalesces misses because the lock is per key",
                                "--fix", "single-flight the loader", "--source", "runbook.md:12")
                self.assertEqual(r.returncode, 2, (topic, r.stdout, r.stderr))
                self.assertTrue(r.stdout.startswith("not appended: "), (topic, r.stdout))
                self.assertNotIn("Traceback", r.stderr)
            for topic in ("memory", "journal", "skills", "research"):
                self.assertIn(f"'{topic}' is a reserved workspace directory",
                              self.run_py(str(SCRIPTS / "_lesson.py"), "--ws", "w1", "--topic", topic,
                                          "--symptom", "s long enough", "--tried", "t", "--root", "r because x",
                                          "--fix", "f").stdout)
            self.assertEqual(_tree(self.home), before)
            self.assertEqual(_tree(outside), outside_before)
            self.assertFalse((self.wsd / "memory" / "lessons.md").exists())
            self.assertFalse((self.wsd / "journal" / "00-README.md").exists())
            # Positive control: the same CLI appends to a real topic.
            r = self.run_py(str(SCRIPTS / "_lesson.py"), "--ws", "w1", "--topic", "redis-cache",
                            "--symptom", "cache stampede on a cold start", "--tried", "warming by hand",
                            "--root", "nothing coalesces misses because the lock is per key",
                            "--fix", "single-flight the loader", "--source", "runbook.md:12")
            self.assertIn("appended: ", r.stdout, r.stderr)
            self.assertIn("cache stampede on a cold start", (self.topic / "lessons.md").read_text())
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_side_channel_entries_keep_their_file(self):
        path, status = self.append("[secret-ref] REDIS_PASSWORD env var holds the prod cache password",
                                   "--ws", "w1", "--topic", "redis-cache")
        self.assertEqual(status, "written")
        self.assertEqual(Path(path), self.home / "shared" / "secrets.md")

    def test_bad_ws_is_a_refusal_not_a_traceback(self):
        before = _tree(self.home)
        entry = "[exp] cache stampede on cold start because nothing coalesces misses"
        for ws in ("../w1", "Bad WS", "w1/x", "", ".hidden", "a" * 70):
            for args in (("--append", entry, "--ws", ws), ("--append", entry, "--ws", ws, "--topic", "redis-cache"),
                         ("--route", entry, "--ws", ws), ("--list", "--ws", ws), ("--ensure", "zz", "--ws", ws)):
                r = self.run_py(str(SCRIPTS / "_topic.py"), *args)
                self.assertEqual((r.returncode, r.stdout), (0, "-\trejected:bad_ws\n"), (ws, args, r.stderr))
                self.assertNotIn("Traceback", r.stderr)
        r = self.run_py(str(SCRIPTS / "_topic.py"), "--append", entry,
                        env=_env(self.home, GOWTH_WORKSPACE="../../etc"))
        self.assertEqual(r.stdout, "-\trejected:bad_ws\n")
        self.assertEqual(_tree(self.home), before)
        self.assertFalse((self.home.parent / "etc").exists())


class EntriesLockTests(_Vault):
    def setUp(self):
        super().setUp()
        self._saved_home = os.environ.get("GOWTH_MEM_HOME")

    def _hold(self):
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        from _lock import file_lock  # type: ignore
        return file_lock("entries-w1", timeout=5)

    def tearDown(self):
        if self._saved_home is None:
            os.environ.pop("GOWTH_MEM_HOME", None)
        else:
            os.environ["GOWTH_MEM_HOME"] = self._saved_home
        super().tearDown()

    def test_every_writer_refuses_or_skips_while_the_lock_is_held(self):
        aspect = _w(self.topic / "2026-01-02-alpha.md", "- [exp] alpha beta gamma delta epsilon entry\n")
        code = (
            "import json, sys; sys.path.insert(0, sys.argv[1])\n"
            "import _topic\n_topic.ENTRIES_LOCK_TIMEOUT = 0.3\n"
            "import _lesson, _tags, _validate\n"
            "from pathlib import Path\n"
            "out = {}\n"
            "out['append'] = _topic.append_entry_status('[exp] brand new topic words because tests', ws='w1')[1]\n"
            "out['append_topic'] = _topic.append_entry_status('[exp] forced topic entry because tests', ws='w1',"
            " topic='redis-cache')[1]\n"
            "out['lesson'] = _lesson.append_lesson_status('symptom long enough', 'tried', 'root because x',"
            " 'fix it', 'src', ws='w1')[1]\n"
            "res = _tags.backfill(ws='w1', apply=True)\n"
            "out['backfill_changed'] = res['changed']; out['backfill_skipped'] = res['skipped']\n"
            "out['fix'] = _validate._fix_locked('w1', Path(sys.argv[2]))\n"
            "print(json.dumps(out))\n"
        )
        before = _tree(self.home)
        with self._hold():
            r = self.run_py("-c", code, str(SCRIPTS), str(aspect))
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["append"], "rejected:lock_timeout")
        self.assertEqual(out["append_topic"], "rejected:lock_timeout")
        self.assertEqual(out["lesson"], "rejected:lock_timeout")
        self.assertEqual(out["backfill_changed"], 1)                  # it WOULD change the file …
        self.assertEqual(out["backfill_skipped"], [str(aspect)])      # … but skipped it
        self.assertIsNone(out["fix"])
        self.assertEqual(_tree(self.home), before)                    # nothing created, nothing written
        # Positive control: the same calls succeed once the lock is free.
        r = self.run_py("-c", code, str(SCRIPTS), str(aspect))
        out = json.loads(r.stdout)
        self.assertEqual((out["append"], out["append_topic"], out["lesson"]),
                         ("written", "written", "written"))
        self.assertTrue("tags:" in aspect.read_text() and "type: aspect" in aspect.read_text())

    def test_append_clis_report_lock_timeout(self):
        """The CLIs the teammate and /mem-lesson call, with the real 5 s timeout."""
        entry = "[exp] forced topic entry while locked because tests"
        lesson_args = [str(SCRIPTS / "_lesson.py"), "--topic", "redis-cache", "--ws", "w1",
                       "--symptom", "lock held during a lesson", "--tried", "waited",
                       "--root", "another writer because tests", "--fix", "retry later"]
        before = _tree(self.home)
        with self._hold():
            t = time.monotonic()
            p1 = subprocess.Popen([sys.executable, str(SCRIPTS / "_topic.py"), "--append", entry, "--ws", "w1",
                                   "--topic", "redis-cache"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, env=_env(self.home))
            p2 = subprocess.Popen([sys.executable, *lesson_args], stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, env=_env(self.home))
            o1, e1 = p1.communicate(timeout=60)
            o2, e2 = p2.communicate(timeout=60)
            waited = time.monotonic() - t
        self.assertGreaterEqual(waited, 4.5)                          # they really waited for the lock
        self.assertEqual(o1.strip().split("\t")[1], "rejected:lock_timeout", e1)
        self.assertIn("not appended (rejected:lock_timeout)", o2, e2)
        self.assertEqual(_tree(self.home), before)
        self.assertEqual(self.append(entry, "--ws", "w1", "--topic", "redis-cache")[1], "written")
        r = self.run_py(*lesson_args)
        self.assertIn("appended:", r.stdout, r.stderr)

    def test_the_lock_is_released_when_the_work_raises(self):
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        import _topic  # type: ignore
        with self.assertRaises(RuntimeError):
            with _topic.entries_lock("w1"):
                raise RuntimeError("boom")
        t = time.monotonic()
        with _topic.entries_lock("w1", timeout=0.5):
            pass
        self.assertLess(time.monotonic() - t, 0.4)
        # A TimeoutError raised BY the work is not a lock timeout.
        with self.assertRaises(TimeoutError):
            with _topic.entries_lock("w1"):
                raise TimeoutError("from the work")
        for bad in ("", ".", "..", "a/b"):
            with self.assertRaises(ValueError):
                with _topic.entries_lock(bad):
                    pass


_APPENDER = r"""
import json, sys, time
sys.path.insert(0, sys.argv[1])
from pathlib import Path
import _topic
go, proc, n, common = Path(sys.argv[2]), sys.argv[3], int(sys.argv[4]), sys.argv[5]
while not go.exists():
    time.sleep(0.005)
t0 = time.time()
out = []
for i in range(n):
    tok = f"{proc}{i:03d}"
    entry = (f"[exp] {common} u{tok}x v{tok}y w{tok}z q{tok}k because the relay drifts")
    out.append(_topic.append_entry_status(entry, ws="w1", topic="redis-cache")[1])
print(json.dumps({"statuses": out, "t0": t0, "t1": time.time()}))
"""

_CHURN = r"""
import json, sys, time
sys.path.insert(0, sys.argv[1])
from pathlib import Path
import _tags, _validate
go, stop = Path(sys.argv[2]), Path(sys.argv[3])
while not go.exists():
    time.sleep(0.005)
rounds = 0
while not stop.exists() and rounds < 400:
    _tags.backfill(ws="w1", apply=True)
    for p in sorted(Path(sys.argv[4]).glob("20*-*.md")):
        _validate._fix_locked("w1", p)
    rounds += 1
print(json.dumps({"rounds": rounds}))
"""

_PRUNE_LOOP = r"""
import subprocess, sys, time
from pathlib import Path
go, stop = Path(sys.argv[2]), Path(sys.argv[3])
while not go.exists():
    time.sleep(0.005)
rounds = 0
while not stop.exists() and rounds < 200:
    subprocess.run([sys.executable, sys.argv[1] + "/_prune.py", "--all-workspaces"],
                   capture_output=True, timeout=60)
    rounds += 1
print(rounds)
"""


class ConcurrencyTests(_Vault):
    N = 50
    LESSONS = 12
    STALE = "- [exp] stale lantern cache note kept for the prune leg (superseded by the eviction policy)"

    def _run(self, with_prune: bool, scripts: Path = SCRIPTS) -> None:
        go = self.home.parent / f"{self.home.name}.go"
        stop = self.home.parent / f"{self.home.name}.stop"
        env = _env(self.home)
        if with_prune:
            # A deletable entry in the very file the appenders grow, so the
            # prune really rewrites it (under the lock) while they append.
            import _topic  # type: ignore
            slug = _topic.derive_aspect_slug(f"[exp] {COMMON} ua000x va000y wa000z qa000k because the relay drifts")
            _w(self.topic / f"{TODAY}-{slug}.md", self.STALE + "\n")
        procs = [subprocess.Popen([sys.executable, "-c", _APPENDER, str(scripts), str(go), p, str(self.N), COMMON],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
                 for p in ("a", "b")]
        side = [subprocess.Popen([sys.executable, "-c", _CHURN, str(scripts), str(go), str(stop), str(self.topic)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)]
        if with_prune:
            side.append(subprocess.Popen([sys.executable, "-c", _PRUNE_LOOP, str(scripts), str(go), str(stop)],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env))
        try:
            go.write_text("go")
            lesson_out = []
            for i in range(self.LESSONS):                              # the `_lesson.py` loop
                r = self.run_py(str(scripts / "_lesson.py"), "--topic", "redis-cache", "--ws", "w1",
                                "--symptom", f"lesson symptom {i:02d} under contention",
                                "--tried", f"attempt {i:02d}", "--root", f"root cause {i:02d} because of load",
                                "--fix", f"fix number {i:02d}")
                lesson_out.append(r.stdout)
            results = [json.loads(p.communicate(timeout=300)[0]) for p in procs]
        finally:
            stop.write_text("stop")
            for p in side:
                try:
                    p.communicate(timeout=300)
                except Exception:
                    p.kill()
            go.unlink(missing_ok=True)
            stop.unlink(missing_ok=True)

        for res in results:
            self.assertEqual(res["statuses"], ["written"] * self.N)
        a, b = results
        self.assertLess(max(a["t0"], b["t0"]), min(a["t1"], b["t1"]), "the appenders never overlapped")
        text = "".join(p.read_text() for p in sorted(self.topic.glob("20*-*.md")))
        self.assertEqual(len(list(self.topic.glob("20*-*.md"))), 1, "all entries share one aspect")
        # The phrase (not a lone token: auto-tags repeat single words as #tags).
        missing = [f"{p}{i:03d}" for p in ("a", "b") for i in range(self.N)
                   if text.count(f"u{p}{i:03d}x v{p}{i:03d}y w{p}{i:03d}z") != 1]
        self.assertEqual(missing, [], f"{len(missing)} of {2 * self.N} entries lost or doubled")
        self.assertTrue(all("appended:" in o for o in lesson_out), lesson_out)
        lessons = (self.topic / "lessons.md").read_text()
        self.assertEqual([i for i in range(self.LESSONS) if f"lesson symptom {i:02d} under contention" not in lessons],
                         [])
        if with_prune:
            self.assertNotIn("stale lantern cache note", text, "the prune never rewrote the shared aspect")

    def test_two_appenders_a_lesson_loop_and_backfill_fix_churn_lose_nothing(self):
        self._run(with_prune=False)

    def test_spec_2_6_two_appenders_with_prune_and_lesson_loops(self):
        self.assertTrue(_prune_takes_entries_lock(),
                        "_prune.py must hold entries-<ws> while it rewrites a file (spec §3.3)")
        self._run(with_prune=True)


_SECRET_APPENDER = r"""
import json, sys, time
sys.path.insert(0, sys.argv[1])
from pathlib import Path
import _topic
go, ws, n = Path(sys.argv[2]), sys.argv[3], int(sys.argv[4])
while not go.exists():
    time.sleep(0.005)
t0 = time.time()
out = []
for i in range(n):
    tok = f"{ws}x{i:03d}"
    entry = f"[secret-ref] SVC_{tok.upper()}_TOKEN env var holds the {tok} service token"
    out.append(_topic.append_entry_status(entry, ws=ws)[1])
print(json.dumps({"statuses": out, "t0": t0, "t1": time.time()}))
"""

_ONE_APPEND = ("import sys; sys.path.insert(0, sys.argv[1])\n"
               "import _topic\n_topic.ENTRIES_LOCK_TIMEOUT = 0.3\n"
               "print(_topic.append_entry_status(sys.argv[2], ws=sys.argv[3])[1])\n")


class SharedEntriesLockTests(_Vault):
    """Review safety-sync#3: every workspace appends its `[secret-ref]` to the
    ONE `shared/secrets.md`; `entries-<ws>` never serialised two workspaces."""
    N = 50
    SECRET = "[secret-ref] PAGER_API_KEY env var holds the pager integration key"
    TOPIC_ENTRY = "[exp] cache stampede on cold start because nothing coalesces misses"

    def setUp(self):
        super().setUp()
        for ws in ("w2", "shared"):
            _w(self.home / "workspaces" / ws / "workspace.json", "{}")
        self._saved_home = os.environ.get("GOWTH_MEM_HOME")
        os.environ["GOWTH_MEM_HOME"] = self.tmp

    def tearDown(self):
        if self._saved_home is None:
            os.environ.pop("GOWTH_MEM_HOME", None)
        else:
            os.environ["GOWTH_MEM_HOME"] = self._saved_home
        super().tearDown()

    def one(self, entry: str, ws: str) -> str:
        r = self.run_py("-c", _ONE_APPEND, str(SCRIPTS), entry, ws)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_two_workspaces_appending_secret_refs_lose_nothing(self):
        go = self.home.parent / f"{self.home.name}.go"
        procs = [subprocess.Popen([sys.executable, "-c", _SECRET_APPENDER, str(SCRIPTS), str(go), ws, str(self.N)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=_env(self.home))
                 for ws in ("w1", "w2")]
        try:
            time.sleep(0.3)
            go.write_text("go")
            results = []
            for p in procs:
                out, err = p.communicate(timeout=300)
                self.assertEqual(p.returncode, 0, err)
                results.append(json.loads(out))
        finally:
            go.unlink(missing_ok=True)
        for res in results:
            self.assertEqual(res["statuses"], ["written"] * self.N)
        a, b = results
        self.assertLess(max(a["t0"], b["t0"]), min(a["t1"], b["t1"]), "the appenders never overlapped")
        text = (self.home / "shared" / "secrets.md").read_text()
        missing = [f"{ws}x{i:03d}" for ws in ("w1", "w2") for i in range(self.N)
                   if text.count(f"env var holds the {ws}x{i:03d} service token") != 1]
        self.assertEqual(missing, [], f"{len(missing)} of {2 * self.N} [secret-ref] entries lost or doubled")

    def test_secret_ref_takes_the_one_shared_lock(self):
        from _lock import file_lock  # type: ignore
        secrets = self.home / "shared" / "secrets.md"
        # Held `shared-entries` blocks a [secret-ref] from EVERY workspace —
        # and nothing else (a topic entry takes `entries-<ws>`).
        with file_lock("shared-entries", timeout=5):
            self.assertEqual(self.one(self.SECRET, "w1"), "rejected:lock_timeout")
            self.assertEqual(self.one(self.SECRET.replace("PAGER", "PAGER2"), "w2"), "rejected:lock_timeout")
            self.assertEqual(self.one(self.TOPIC_ENTRY, "w1"), "written")
        self.assertFalse(secrets.exists())
        # A workspace's own lock no longer guards (nor blocks) the shared file …
        with file_lock("entries-w1", timeout=5):
            self.assertEqual(self.one(self.SECRET, "w1"), "written")
        # … and the shared lock's name is no workspace lock: `entries-shared`
        # belongs to a workspace named `shared` (WS_RE accepts it), which
        # appends its own [secret-ref] without blocking on itself.
        with file_lock("entries-shared", timeout=5):
            self.assertEqual(self.one(self.SECRET.replace("PAGER", "PAGER3"), "w2"), "written")
        self.assertEqual(self.one(self.SECRET.replace("PAGER", "PAGER4"), "shared"), "written")
        text = secrets.read_text()
        for key in ("PAGER_API_KEY", "PAGER3_API_KEY", "PAGER4_API_KEY"):
            self.assertEqual(text.count(f"[secret-ref] {key} env var"), 1, (key, text))
        self.assertNotIn("PAGER2_API_KEY", text)
        import _topic  # type: ignore
        self.assertEqual(_topic.SHARED_ENTRIES_LOCK, "shared-entries")
        self.assertTrue(_topic._under_shared(secrets))
        self.assertFalse(_topic._under_shared(self.wsd / "redis-cache" / "x.md"))
        self.assertFalse(_topic._under_shared(self.home / "workspaces" / "shared" / "x.md"))


class RepeatedWriteTests(_Vault):
    def test_backfill_three_passes_are_byte_identical(self):
        p = _w(self.topic / "2026-01-02-alpha.md",
               "---\nslug: redis-cache-alpha\n---\n\n- [exp] PostgreSQL vacuum stalls because autovacuum_naptime is high\n")
        snaps = []
        for _ in range(3):
            r = self.run_py(str(SCRIPTS / "_tags.py"), "--backfill", "--apply", "--ws", "w1")
            self.assertEqual(r.returncode, 0, r.stderr)
            snaps.append(p.read_bytes())
        self.assertIn(b"tags: [", snaps[0])
        self.assertIn(b"autovacuum_naptime", snaps[0].split(b"\n---")[0])     # tag landed in frontmatter
        self.assertEqual(snaps[0], snaps[1])
        self.assertEqual(snaps[1], snaps[2])

    def test_validate_fix_three_passes_are_byte_identical(self):
        p = _w(self.topic / "2026-01-03-beta.md", "# Beta\n\n- [exp] beta entry body long enough\n")
        snaps = []
        for _ in range(3):
            r = self.run_py(str(SCRIPTS / "_validate.py"), "--fix", "--ws", "w1")
            self.assertEqual(r.returncode, 0, r.stderr)
            snaps.append(p.read_bytes())
        self.assertIn(b"type: aspect\n", snaps[0])
        self.assertIn(b"topic: redis-cache\n", snaps[0])
        self.assertEqual(snaps[0], snaps[1])
        self.assertEqual(snaps[1], snaps[2])


if __name__ == "__main__":
    unittest.main()
