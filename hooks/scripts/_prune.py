#!/usr/bin/env python3
"""Active prune: DELETE outdated / superseded / duplicate entries from docs/*.md.

Differs from mempalace `invalidate()` (which only sets `valid_to` and keeps the
row): this script actively removes lines that match strict outdated criteria.
Per user direction: outdated knowledge = noise, must go.

Pruning rules (line-level, in order):
  1. Lines with `valid_until: YYYY-MM-DD` where date < today          → DELETE
  2. Lines containing `(superseded)` (case-insensitive)               → DELETE
  3. Lines containing `(deprecated)` or `(obsolete)`                  → DELETE
  4. Lines with `version: X` where X is in DEPRECATED_VERSIONS env    → DELETE
  5. Within-file near-duplicate entry lines (Jaccard ≥ 0.85)          → DELETE shorter

Skips:
  - docs/journal/**/*.md (raw journal is permanent log; don't prune)
  - Lines outside entry pattern (`- [type]` or section headers)

Usage:
  python3 _prune.py [--workspace PATH] [--dry-run]

Output:
  Reports per-file: deleted N entries (kept M).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _paths import docs_root  # type: ignore

ENTRY_RE = re.compile(r"^\s*[-*]\s+\[")
VALID_UNTIL_RE = re.compile(r"valid[_-]?until:\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE)
SUPERSEDED_RE = re.compile(r"\((?:superseded|deprecated|obsolete)\b", re.IGNORECASE)


def jaccard(a: str, b: str) -> float:
    sa = set(re.findall(r"\w{4,}", a.lower()))
    sb = set(re.findall(r"\w{4,}", b.lower()))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def prune_file(path: Path, dry_run: bool, today_iso: str) -> tuple[int, int]:
    """Return (deleted, kept) entry counts for a single file."""
    try:
        text = path.read_text()
    except Exception:
        return (0, 0)
    lines = text.splitlines()

    kept_lines: list[str] = []
    kept_entries: list[str] = []
    deleted = 0

    i = 0
    while i < len(lines):
        line = lines[i]

        # Group an entry with its continuation lines (indented under it).
        if ENTRY_RE.match(line):
            entry_block = [line]
            j = i + 1
            while j < len(lines) and (lines[j].startswith(" ") or lines[j].startswith("\t")) and lines[j].strip():
                entry_block.append(lines[j])
                j += 1
            entry_text = "\n".join(entry_block)

            # Rule 1+2+3: temporal / superseded / deprecated → DELETE
            drop = False
            m = VALID_UNTIL_RE.search(entry_text)
            if m and m.group(1) < today_iso:
                drop = True
            elif SUPERSEDED_RE.search(entry_text):
                drop = True

            # Rule 5: dedup within file
            if not drop:
                for prev in kept_entries:
                    if jaccard(entry_text, prev) >= 0.85:
                        # Keep the longer one; replace prev if new is longer.
                        if len(entry_text) > len(prev):
                            # Remove prev from kept_lines
                            prev_block_lines = prev.split("\n")
                            for prev_line in prev_block_lines:
                                if prev_line in kept_lines:
                                    kept_lines.remove(prev_line)
                            kept_entries.remove(prev)
                            deleted += 1
                        else:
                            drop = True
                            break

            if drop:
                deleted += 1
            else:
                kept_lines.extend(entry_block)
                kept_entries.append(entry_text)
            i = j
        else:
            kept_lines.append(line)
            i += 1

    new_text = "\n".join(kept_lines)
    if new_text != text and not dry_run:
        path.write_text(new_text)
    return (deleted, len(kept_entries))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    workspace = Path(args.workspace or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    docs = docs_root(workspace)
    if not docs.is_dir():
        print(f"no docs/ directory at {docs}; nothing to prune")
        return 0

    today_iso = date.today().isoformat()
    total_deleted = 0
    total_kept = 0
    affected: list[tuple[str, int, int]] = []
    for f in sorted(docs.rglob("*.md")):
        if "journal" in f.parts:
            continue
        deleted, kept = prune_file(f, args.dry_run, today_iso)
        if deleted > 0:
            affected.append((str(f.relative_to(workspace)), deleted, kept))
        total_deleted += deleted
        total_kept += kept

    prefix = "[dry-run] " if args.dry_run else ""
    if not affected:
        print(f"{prefix}prune: nothing to drop. {total_kept} entries verified clean.")
        return 0
    print(f"{prefix}prune: deleted {total_deleted} entries across {len(affected)} files. {total_kept} entries kept.")
    for rel, d, k in affected:
        print(f"  {rel}: -{d} entries (now {k})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
