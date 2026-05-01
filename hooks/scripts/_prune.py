#!/usr/bin/env python3
"""Active prune (v2.0): DELETE outdated/superseded/duplicate entries from
~/.gowth-mem/topics/**/*.md and docs/*.md.

Skips:
  - journal/**  (raw log is permanent)
  - _index.md   (auto-regenerated)

Rules (line-level, in order):
  1. Lines with `valid_until: YYYY-MM-DD` past today  → DELETE
  2. Lines with `(superseded|deprecated|obsolete)`     → DELETE
  3. Within-file Jaccard ≥ 0.85 duplicates             → DELETE shorter

All writes use atomic_write.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import atomic_write  # type: ignore
from _home import docs_dir, gowth_home, topics_dir  # type: ignore

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
        if ENTRY_RE.match(line):
            entry_block = [line]
            j = i + 1
            while j < len(lines) and (lines[j].startswith(" ") or lines[j].startswith("\t")) and lines[j].strip():
                entry_block.append(lines[j])
                j += 1
            entry_text = "\n".join(entry_block)

            drop = False
            m = VALID_UNTIL_RE.search(entry_text)
            if m and m.group(1) < today_iso:
                drop = True
            elif SUPERSEDED_RE.search(entry_text):
                drop = True

            if not drop:
                for prev in kept_entries:
                    if jaccard(entry_text, prev) >= 0.85:
                        if len(entry_text) > len(prev):
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
        atomic_write(path, new_text)
    return (deleted, len(kept_entries))


def collect_files() -> list[Path]:
    out: list[Path] = []
    td = topics_dir()
    if td.is_dir():
        out.extend(p for p in td.rglob("*.md") if p.is_file() and p.name != "_index.md")
    dd = docs_dir()
    if dd.is_dir():
        out.extend(p for p in dd.glob("*.md") if p.is_file())
    return sorted(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    gh = gowth_home()
    if not gh.is_dir():
        print(f"no ~/.gowth-mem directory; nothing to prune")
        return 0

    today_iso = date.today().isoformat()
    total_deleted = 0
    total_kept = 0
    affected: list[tuple[str, int, int]] = []
    for f in collect_files():
        deleted, kept = prune_file(f, args.dry_run, today_iso)
        if deleted > 0:
            try:
                rel = str(f.relative_to(gh))
            except ValueError:
                rel = str(f)
            affected.append((rel, deleted, kept))
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
