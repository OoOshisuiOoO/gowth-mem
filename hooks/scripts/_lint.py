#!/usr/bin/env python3
"""Contradiction detection lint pass (v2.9): find conflicting entries.

Walks topic files and docs, extracts typed entries (default: [ref] only),
and flags pairs with high keyword overlap but different content.

Usage:
  python3 _lint.py                  # report contradictions in [ref] entries
  python3 _lint.py --json           # JSON output
  python3 _lint.py --all            # check all entry types, not just [ref]

Pure stdlib Python 3.9+. No pip deps.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _home import (  # type: ignore
    active_workspace,
    docs_dir,
    gowth_home,
    iter_topic_files,
)

ENTRY_RE = re.compile(r"^\s*[-*]\s+\[(\w[\w-]*)\]\s+(.+)")
SOURCE_RE = re.compile(r"Source:\s*(.+?)(?:\s*$|\s*\|)", re.IGNORECASE)

SIMILARITY_THRESHOLD = 0.4
DUPLICATE_THRESHOLD = 0.85
DIFFERENCE_THRESHOLD = 0.15


def jaccard(a: str, b: str) -> float:
    sa = set(re.findall(r"\w{4,}", a.lower()))
    sb = set(re.findall(r"\w{4,}", b.lower()))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def extract_entries(path: Path, types: set[str] | None = None) -> list[dict]:
    try:
        text = path.read_text(errors="ignore")
    except Exception:
        return []

    entries = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = ENTRY_RE.match(line)
        if not m:
            continue
        entry_type = m.group(1).lower()
        if types and entry_type not in types:
            continue
        entry_text = m.group(2).strip()

        j = i + 1
        while j < len(lines) and lines[j].startswith(("  ", "\t")) and lines[j].strip():
            entry_text += " " + lines[j].strip()
            j += 1

        source_m = SOURCE_RE.search(entry_text)
        source = source_m.group(1).strip() if source_m else None

        entries.append({
            "type": entry_type,
            "text": entry_text,
            "source": source,
            "file": str(path),
            "line": i + 1,
        })

    return entries


def find_contradictions(entries: list[dict]) -> list[dict]:
    contradictions = []

    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            a, b = entries[i], entries[j]

            sim = jaccard(a["text"], b["text"])
            if sim < SIMILARITY_THRESHOLD:
                continue
            if sim > DUPLICATE_THRESHOLD:
                continue

            diff = 1.0 - sim
            if diff < DIFFERENCE_THRESHOLD:
                continue

            contradictions.append({
                "similarity": round(sim, 3),
                "entry_a": {
                    "file": a["file"],
                    "line": a["line"],
                    "type": a["type"],
                    "text": a["text"][:200],
                    "source": a.get("source"),
                },
                "entry_b": {
                    "file": b["file"],
                    "line": b["line"],
                    "type": b["type"],
                    "text": b["text"][:200],
                    "source": b.get("source"),
                },
            })

    contradictions.sort(key=lambda c: -c["similarity"])
    return contradictions


def collect_all_entries(check_types: set[str] | None = None) -> list[dict]:
    ws = active_workspace()
    entries: list[dict] = []

    for path in iter_topic_files(ws):
        entries.extend(extract_entries(path, check_types))

    dd = docs_dir(ws)
    if dd.is_dir():
        for p in dd.glob("*.md"):
            if p.is_file():
                entries.extend(extract_entries(p, check_types))

    return entries


def format_report(contradictions: list[dict], entries_checked: int) -> str:
    if not contradictions:
        return f"lint: no contradictions detected ({entries_checked} entries checked)."

    gh = gowth_home()
    parts = [f"lint: {len(contradictions)} potential contradiction(s) "
             f"found ({entries_checked} entries checked):\n"]

    for i, c in enumerate(contradictions, 1):
        a, b = c["entry_a"], c["entry_b"]
        try:
            file_a = str(Path(a["file"]).relative_to(gh))
        except ValueError:
            file_a = a["file"]
        try:
            file_b = str(Path(b["file"]).relative_to(gh))
        except ValueError:
            file_b = b["file"]

        parts.append(f"  {i}. Similarity={c['similarity']}")
        parts.append(f"     A: [{a['type']}] {a['text'][:120]}")
        parts.append(f"        @ {file_a}:{a['line']}")
        parts.append(f"     B: [{b['type']}] {b['text'][:120]}")
        parts.append(f"        @ {file_b}:{b['line']}")
        if a.get("source") and b.get("source") and a["source"] != b["source"]:
            parts.append(f"     Sources differ: {a['source']} vs {b['source']}")
        parts.append("")

    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description="Contradiction detection lint pass")
    ap.add_argument("--json", action="store_true", help="Output JSON")
    ap.add_argument("--all", action="store_true", help="Check all entry types, not just [ref]")
    args = ap.parse_args()

    gh = gowth_home()
    if not gh.is_dir():
        print("lint: no ~/.gowth-mem directory.")
        return 0

    check_types = None if args.all else {"ref"}
    entries = collect_all_entries(check_types)

    if not entries:
        print("lint: no entries found to check.")
        return 0

    contradictions = find_contradictions(entries)

    if args.json:
        print(json.dumps({
            "entries_checked": len(entries),
            "contradictions": contradictions,
        }, indent=2))
    else:
        print(format_report(contradictions, len(entries)))

    return 0


if __name__ == "__main__":
    sys.exit(main())
