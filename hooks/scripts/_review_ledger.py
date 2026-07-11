#!/usr/bin/env python3
"""Conversation review ledger (v4.1) — which conversations have been reviewed?

Every transcript under ~/.claude/projects/<project>/<sessionId>.jsonl is a
conversation. The v4.0 self-review loop only covers LIVE sessions at the
15-turn cadence — conversations that end early, or predate v4.0, are never
reviewed. This ledger closes the coverage gap:

  - `--scan`  lists unreviewed substantive conversations (oldest first)
  - `--next`  picks the single next candidate to review (reads its content
              to enforce min-turns; thin ones get auto-marked `skipped-thin`)
  - `--mark <sid> --status reviewed` records completion
  - `--stats` reviewed / unreviewed counts

Design constraints:
  - Metadata-first: scan touches only stat() — observed live scale is 1000+
    transcripts; full-content scans would be seconds, stat() is milliseconds.
    Content is read ONLY for the single --next candidate.
  - The ledger lives at ~/.gowth-mem/review-ledger.json and is MACHINE-LOCAL
    (gitignored): transcripts themselves never leave the machine, so a synced
    ledger would reference paths other machines don't have. Review OUTPUT
    (scores, reflections) still goes to the synced vault via /mem-review.

Driven by /mem-review-backlog; nudged from the Stop-hook self-review reason.

CLI:
  python3 _review_ledger.py --scan [--json] [--limit N]
  python3 _review_ledger.py --next [--json]
  python3 _review_ledger.py --mark SID --status reviewed|skipped [--note TEXT]
  python3 _review_ledger.py --stats
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import atomic_write  # type: ignore
from _home import gowth_home  # type: ignore
from _lock import file_lock  # type: ignore

DEFAULT_MIN_BYTES = 20_000     # thinner transcripts are trivial sessions
DEFAULT_IDLE_MINUTES = 60      # don't review conversations that may still be live
DEFAULT_MIN_TURNS = 10         # matches the v4.0 self-review `<10 turns → skip` rule


def default_projects_dir() -> Path:
    claude = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
    return claude / "projects"


def ledger_path() -> Path:
    return gowth_home() / "review-ledger.json"


def load_ledger() -> dict:
    try:
        d = json.loads(ledger_path().read_text())
        if isinstance(d, dict) and isinstance(d.get("sessions"), dict):
            return d
    except Exception:
        pass
    return {"version": 1, "sessions": {}}


def _save_ledger(d: dict) -> None:
    with file_lock("review-ledger", timeout=5.0):
        atomic_write(ledger_path(), json.dumps(d, indent=1, ensure_ascii=False))


# ------------------------------------------------------------------ scanning

def scan(projects_dir: Path | None = None, min_bytes: int = DEFAULT_MIN_BYTES,
         idle_minutes: int = DEFAULT_IDLE_MINUTES, limit: int = 0) -> list[dict]:
    """Unreviewed transcript candidates, oldest mtime first. stat()-only."""
    root = projects_dir or default_projects_dir()
    if not root.is_dir():
        return []
    seen = load_ledger()["sessions"]
    now = _dt.datetime.now().timestamp()
    out: list[dict] = []
    for proj in sorted(p for p in root.iterdir() if p.is_dir()):
        for t in proj.glob("*.jsonl"):
            sid = t.stem
            if sid in seen:
                continue
            try:
                st = t.stat()
            except OSError:
                continue
            if st.st_size < min_bytes:
                continue
            if (now - st.st_mtime) < idle_minutes * 60:
                continue
            out.append({"sid": sid, "project": proj.name, "path": str(t),
                        "bytes": st.st_size, "mtime": st.st_mtime,
                        "mtime_iso": _dt.datetime.fromtimestamp(st.st_mtime)
                        .isoformat(timespec="minutes")})
    out.sort(key=lambda c: c["mtime"])
    return out[:limit] if limit else out


def _count_turns(path: Path, cap: int = 500) -> tuple[int, int]:
    """Return (user_turns, assistant_turns) via cheap substring scan.

    `type` is NOT the first JSON key in live transcripts (lines start with
    parentUuid), so match `"type":"user"` anywhere in the line. Lines carrying
    tool_result payloads are ALSO type:user — exclude them so user_turns
    approximates real prompts. Early exit once assistant turns hit `cap`.
    """
    users = assistants = 0
    try:
        with open(path, errors="ignore") as fh:
            for line in fh:
                if '"type":"assistant"' in line:
                    assistants += 1
                    if assistants >= cap:
                        break
                elif '"type":"user"' in line and '"tool_result"' not in line:
                    users += 1
    except OSError:
        return 0, 0
    return users, assistants


def next_candidate(projects_dir: Path | None = None,
                   min_bytes: int = DEFAULT_MIN_BYTES,
                   idle_minutes: int = DEFAULT_IDLE_MINUTES,
                   min_turns: int = DEFAULT_MIN_TURNS) -> dict | None:
    """Oldest unreviewed candidate with enough substance. Substance is
    measured by ASSISTANT turns — tool-heavy autonomous sessions have few
    user prompts but plenty of reviewable work. Auto-marks too-thin
    candidates `skipped-thin` so they never re-surface."""
    for c in scan(projects_dir, min_bytes, idle_minutes):
        users, assistants = _count_turns(Path(c["path"]))
        if assistants < min_turns:
            mark(c["sid"], status="skipped-thin",
                 note=f"{assistants} assistant turns < {min_turns}")
            continue
        c["user_turns"] = users
        c["assistant_turns"] = assistants
        return c
    return None


def mark(sid: str, status: str = "reviewed", note: str = "") -> dict:
    d = load_ledger()
    d["sessions"][sid] = {
        "status": status,
        "at": _dt.datetime.now().isoformat(timespec="seconds"),
        "note": note,
    }
    _save_ledger(d)
    return d["sessions"][sid]


def stats(projects_dir: Path | None = None, min_bytes: int = DEFAULT_MIN_BYTES,
          idle_minutes: int = DEFAULT_IDLE_MINUTES) -> dict:
    sessions = load_ledger()["sessions"]
    by_status: dict[str, int] = {}
    for v in sessions.values():
        by_status[v.get("status", "?")] = by_status.get(v.get("status", "?"), 0) + 1
    return {
        "reviewed": by_status.get("reviewed", 0),
        "skipped": sum(n for s, n in by_status.items() if s.startswith("skipped")),
        "unreviewed": len(scan(projects_dir, min_bytes, idle_minutes)),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Conversation review coverage ledger.")
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--next", action="store_true")
    ap.add_argument("--mark")
    ap.add_argument("--status", default="reviewed")
    ap.add_argument("--note", default="")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--min-bytes", type=int, default=DEFAULT_MIN_BYTES)
    ap.add_argument("--idle-minutes", type=int, default=DEFAULT_IDLE_MINUTES)
    ap.add_argument("--min-turns", type=int, default=DEFAULT_MIN_TURNS)
    args = ap.parse_args()
    if not gowth_home().is_dir():
        print("no ~/.gowth-mem directory")
        return 0

    if args.mark:
        r = mark(args.mark, status=args.status, note=args.note)
        print(json.dumps({args.mark: r}, ensure_ascii=False))
    elif args.next:
        c = next_candidate(min_bytes=args.min_bytes, idle_minutes=args.idle_minutes,
                           min_turns=args.min_turns)
        if c is None:
            print("review backlog clean — no unreviewed substantive conversations.")
        elif args.json:
            print(json.dumps(c, ensure_ascii=False, indent=1))
        else:
            print(f"next: {c['path']}\n  project={c['project']} sid={c['sid']} "
                  f"turns={c['user_turns']}u/{c['assistant_turns']}a last-active={c['mtime_iso']}\n"
                  f"after reviewing: python3 {__file__} --mark {c['sid']} --status reviewed")
    elif args.stats:
        print(json.dumps(stats(min_bytes=args.min_bytes,
                               idle_minutes=args.idle_minutes), indent=1))
    else:  # --scan default
        cands = scan(min_bytes=args.min_bytes, idle_minutes=args.idle_minutes,
                     limit=args.limit)
        if args.json:
            print(json.dumps(cands, ensure_ascii=False, indent=1))
        else:
            for c in cands[:25]:
                print(f"{c['mtime_iso']}  {c['project']}/{c['sid'][:8]}  {c['bytes']//1024}KB")
            print(f"unreviewed: {len(cands)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
