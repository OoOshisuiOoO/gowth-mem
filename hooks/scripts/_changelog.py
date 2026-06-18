#!/usr/bin/env python3
"""Themed memory changelog (v3.8) — learned from supremor's auto CHANGE LOG.

supremor regenerates a themed "what changed" digest daily via an LLM job that
groups commits by theme. gowth-mem can do the same **deterministically** —
because v3.6 descriptive commits already carry structured trailers
(`Workspace:`, `Topics:`, `Entries:`, `Context:`). This parses the memory-repo
git log over a window and rolls it up BY THEME (workspace → change type →
topics + entry deltas) instead of a flat commit timeline. No LLM.

CLI:
  python3 _changelog.py [--days N] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _home import gowth_home  # type: ignore

SUBJECT_RE = re.compile(r"^(add|update|prune|archive|consolidate|sync|chore|feat|fix|docs)\(([^)]+)\):\s*(.*)$")
TRAILER_RE = re.compile(r"^([A-Z][a-zA-Z]+):\s*(.+)$")


def _log(gh: Path, days: int) -> list[dict]:
    """Return parsed commits in the window. Each: {type, scope, subject, trailers}."""
    sep = "\x1e"
    fmt = f"%H%x1f%s%x1f%b{sep}"
    try:
        out = subprocess.run(
            ["git", "-C", str(gh), "log", f"--since={days} days ago", f"--pretty=format:{fmt}"],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except Exception:
        return []
    commits = []
    for raw in out.split(sep):
        raw = raw.strip()
        if not raw:
            continue
        parts = raw.split("\x1f")
        if len(parts) < 2:
            continue
        h, subject = parts[0], parts[1]
        body = parts[2] if len(parts) > 2 else ""
        m = SUBJECT_RE.match(subject)
        ctype = m.group(1) if m else "other"
        scope = m.group(2) if m else ""
        trailers: dict[str, str] = {}
        for line in body.splitlines():
            tm = TRAILER_RE.match(line.strip())
            if tm:
                trailers[tm.group(1)] = tm.group(2)
        commits.append({"hash": h[:8], "type": ctype, "scope": scope,
                        "subject": subject, "trailers": trailers})
    return commits


def build_changelog(gh: Path, days: int) -> dict:
    commits = _log(gh, days)
    # Theme = workspace; within it, group by change type + collect topics + entry deltas.
    by_ws: dict[str, dict] = defaultdict(lambda: {"types": defaultdict(int), "topics": set(), "entries": defaultdict(int), "commits": 0})
    type_total: dict[str, int] = defaultdict(int)
    for c in commits:
        wss = [w.strip() for w in c["trailers"].get("Workspace", c["scope"] or "vault").split(",")]
        topics = [t.strip() for t in c["trailers"].get("Topics", "").split(",") if t.strip()]
        entries = c["trailers"].get("Entries", "")
        for ws in wss or ["vault"]:
            slot = by_ws[ws]
            slot["types"][c["type"]] += 1
            slot["topics"].update(topics)
            slot["commits"] += 1
            for em in re.finditer(r"([+-]\d+)\s*([a-z-]+)", entries):
                slot["entries"][em.group(2)] += int(em.group(1))
        type_total[c["type"]] += 1
    # serialize
    out = {"days": days, "commits": len(commits), "type_total": dict(type_total), "workspaces": {}}
    for ws, slot in sorted(by_ws.items()):
        out["workspaces"][ws] = {
            "commits": slot["commits"],
            "types": dict(slot["types"]),
            "topics": sorted(slot["topics"]),
            "entries": {k: v for k, v in sorted(slot["entries"].items()) if v},
        }
    return out


def render(cl: dict) -> str:
    if not cl["commits"]:
        return f"# Memory changelog (last {cl['days']}d)\n\nNo memory changes in the window."
    lines = [f"# Memory changelog (last {cl['days']}d)", ""]
    tt = ", ".join(f"{n} {t}" for t, n in sorted(cl["type_total"].items(), key=lambda x: -x[1]))
    lines.append(f"**{cl['commits']} commits** — {tt}")
    lines.append("")
    for ws, slot in cl["workspaces"].items():
        types = " ".join(f"{t}×{n}" for t, n in sorted(slot["types"].items(), key=lambda x: -x[1]))
        lines.append(f"## {ws}  ({slot['commits']} commits: {types})")
        if slot["topics"]:
            lines.append(f"- Topics: {', '.join(slot['topics'][:12])}" + (" …" if len(slot["topics"]) > 12 else ""))
        if slot["entries"]:
            ent = " ".join(f"{'+' if v > 0 else ''}{v} {k}" for k, v in slot["entries"].items())
            lines.append(f"- Entries: {ent}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Themed memory changelog from descriptive commits.")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    gh = gowth_home()
    if not gh.is_dir() or not (gh / ".git").is_dir():
        print("no ~/.gowth-mem git repo")
        return 0
    cl = build_changelog(gh, args.days)
    print(json.dumps(cl, indent=2) if args.json else render(cl))
    return 0


if __name__ == "__main__":
    sys.exit(main())
