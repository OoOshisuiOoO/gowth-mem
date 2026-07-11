#!/usr/bin/env python3
"""Tests for v4.1 _review_ledger.py — conversation review coverage ledger.

Every transcript in ~/.claude/projects is a conversation. The ledger marks
which ones have been self-reviewed; unreviewed substantive conversations are
surfaced oldest-first so `/mem-review-backlog` can work through them.
Metadata-first design: scan touches only stat() (1000+ transcripts observed
live); transcript CONTENT is read only for the single --next candidate.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "hooks" / "scripts"


def _load():
    sys.path.insert(0, str(SCRIPTS_DIR))
    import _review_ledger
    return _review_ledger


RL = _load()


def _mk_transcript(projects: Path, project: str, sid: str, user_turns: int,
                   pad_bytes: int = 0, age_minutes: float = 120.0,
                   assistant_turns: int | None = None) -> Path:
    """Realistic transcript lines: `type` is NOT the first JSON key (live files
    start with parentUuid) — a prefix-match turn counter must not pass here."""
    d = projects / project
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sid}.jsonl"
    lines = ['{"type":"mode","mode":"normal","sessionId":"%s"}' % sid]
    a_turns = user_turns if assistant_turns is None else assistant_turns
    for i in range(user_turns):
        lines.append('{"parentUuid":"u%d","type":"user","message":{"content":"turn %d"}}' % (i, i))
    for i in range(a_turns):
        lines.append('{"parentUuid":"a%d","type":"assistant","message":{"content":"reply %d"}}' % (i, i))
    body = "\n".join(lines) + "\n"
    if pad_bytes > len(body):
        body += '{"type":"mode","pad":"%s"}\n' % ("x" * (pad_bytes - len(body)))
    p.write_text(body)
    ts = time.time() - age_minutes * 60
    os.utime(p, (ts, ts))
    return p


class TestLedger(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.home = tmp / "gowth-home"
        self.home.mkdir()
        os.environ["GOWTH_MEM_HOME"] = str(self.home)
        self.projects = tmp / "projects"
        self.projects.mkdir()

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        self._tmp.cleanup()

    def _scan(self, **kw):
        kw.setdefault("min_bytes", 100)
        kw.setdefault("idle_minutes", 60)
        return RL.scan(projects_dir=self.projects, **kw)

    def test_scan_finds_unreviewed_transcripts(self):
        _mk_transcript(self.projects, "proj-a", "aaa11111-1111", 12, pad_bytes=500)
        _mk_transcript(self.projects, "proj-b", "bbb22222-2222", 15, pad_bytes=500)
        cands = self._scan()
        self.assertEqual(len(cands), 2)
        self.assertEqual({c["project"] for c in cands}, {"proj-a", "proj-b"})

    def test_thin_transcripts_filtered_by_size(self):
        _mk_transcript(self.projects, "proj-a", "thin1111-1111", 1)  # tiny
        _mk_transcript(self.projects, "proj-a", "big22222-2222", 12, pad_bytes=5000)
        cands = self._scan(min_bytes=2000)
        self.assertEqual([c["sid"] for c in cands], ["big22222-2222"])

    def test_active_sessions_filtered_by_idle(self):
        _mk_transcript(self.projects, "proj-a", "live1111-1111", 12, pad_bytes=500,
                       age_minutes=5)     # still active
        _mk_transcript(self.projects, "proj-a", "idle2222-2222", 12, pad_bytes=500,
                       age_minutes=180)
        cands = self._scan(idle_minutes=60)
        self.assertEqual([c["sid"] for c in cands], ["idle2222-2222"])

    def test_marked_sessions_excluded_from_scan(self):
        _mk_transcript(self.projects, "proj-a", "aaa11111-1111", 12, pad_bytes=500)
        _mk_transcript(self.projects, "proj-a", "bbb22222-2222", 12, pad_bytes=500)
        RL.mark("aaa11111-1111", status="reviewed", note="scored 4/3/4")
        cands = self._scan()
        self.assertEqual([c["sid"] for c in cands], ["bbb22222-2222"])

    def test_next_returns_oldest_and_enforces_min_turns(self):
        _mk_transcript(self.projects, "proj-a", "old-shallow-11", 3, pad_bytes=800,
                       age_minutes=500)   # oldest but only 3 turns → skip-marked
        _mk_transcript(self.projects, "proj-a", "old-deep-2222", 12, pad_bytes=800,
                       age_minutes=400)
        _mk_transcript(self.projects, "proj-a", "new-deep-3333", 12, pad_bytes=800,
                       age_minutes=100)
        nxt = RL.next_candidate(projects_dir=self.projects, min_bytes=100,
                                idle_minutes=60, min_turns=10)
        self.assertEqual(nxt["sid"], "old-deep-2222")
        # the shallow one got auto-marked skipped so it never re-surfaces
        ledger = RL.load_ledger()
        self.assertEqual(ledger["sessions"]["old-shallow-11"]["status"], "skipped-thin")

    def test_mark_persists_and_stats_count(self):
        _mk_transcript(self.projects, "proj-a", "aaa11111-1111", 12, pad_bytes=500)
        _mk_transcript(self.projects, "proj-a", "bbb22222-2222", 12, pad_bytes=500)
        RL.mark("aaa11111-1111", status="reviewed")
        s = RL.stats(projects_dir=self.projects, min_bytes=100, idle_minutes=60)
        self.assertEqual(s["reviewed"], 1)
        self.assertEqual(s["unreviewed"], 1)

    def test_missing_projects_dir_graceful(self):
        cands = RL.scan(projects_dir=Path(self._tmp.name) / "nope")
        self.assertEqual(cands, [])
        s = RL.stats(projects_dir=Path(self._tmp.name) / "nope")
        self.assertEqual(s["unreviewed"], 0)

    def test_next_none_when_all_reviewed(self):
        _mk_transcript(self.projects, "proj-a", "aaa11111-1111", 12, pad_bytes=500)
        RL.mark("aaa11111-1111", status="reviewed")
        nxt = RL.next_candidate(projects_dir=self.projects, min_bytes=100,
                                idle_minutes=60, min_turns=10)
        self.assertIsNone(nxt)

    def test_tool_heavy_autonomous_session_still_candidate(self):
        # Live pattern: autonomous run with FEW user prompts but MANY assistant
        # turns (349 KB / 7 user prompts observed). Substance = assistant work.
        _mk_transcript(self.projects, "proj-a", "auto1111-1111", 3, pad_bytes=800,
                       assistant_turns=40)
        nxt = RL.next_candidate(projects_dir=self.projects, min_bytes=100,
                                idle_minutes=60, min_turns=10)
        self.assertIsNotNone(nxt)
        self.assertEqual(nxt["sid"], "auto1111-1111")
        self.assertGreaterEqual(nxt["assistant_turns"], 40)


if __name__ == "__main__":
    unittest.main()
