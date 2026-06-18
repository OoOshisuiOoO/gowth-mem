#!/usr/bin/env python3
"""Handoff rotation (v3.6) — keep docs/handoff.md small (bootstrap-loaded).

handoff.md is read at EVERY SessionStart, so its size is a per-session token
tax. In practice it accumulates dozens of dated `## <snapshot>` sections
(observed: 32 snapshots / 52 KB in one workspace). The canon caps an
always-loaded file at ~200 lines.

This rotates the file: KEEP the preamble + every non-dated (structural) H2
section + the `keep` most-recent DATED sections; MOVE older dated sections
to `docs/handoff-archive.md` (tracked + searchable, but NOT bootstrap-loaded).
Nothing is deleted — fully reversible (archive file + memory-repo git history).

CLI:
  python3 _handoff.py [--ws X | --all] [--keep N] [--dry-run]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import atomic_write  # type: ignore
from _home import active_workspace, docs_dir, gowth_home, list_workspaces  # type: ignore
from _lock import file_lock  # type: ignore

DEFAULT_KEEP = 10
DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})([a-z]?)")
H2_RE = re.compile(r"^##\s+", re.MULTILINE)


def _split_sections(text: str) -> tuple[str, list[str]]:
    """Return (preamble, [section_blocks]) where each block starts at an H2."""
    matches = list(H2_RE.finditer(text))
    if not matches:
        return text, []
    preamble = text[: matches[0].start()]
    sections = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append(text[m.start():end])
    return preamble, sections


def _section_date_key(section: str):
    """Sort key for a section's date (year, month, day, suffix-letter) from its
    H2 header line. Non-dated sections return None."""
    header = section.splitlines()[0] if section else ""
    m = DATE_RE.search(header)
    if not m:
        return None
    y, mo, d, suf = m.group(1), m.group(2), m.group(3), m.group(4) or ""
    return (int(y), int(mo), int(d), suf)


def rotate_handoff(ws: str, keep: int, dry_run: bool) -> dict:
    hp = docs_dir(ws) / "handoff.md"
    if not hp.is_file():
        return {"ws": ws, "kept": 0, "archived": 0, "skipped": "no handoff"}
    text = hp.read_text(errors="ignore")
    preamble, sections = _split_sections(text)

    dated = [(i, s, _section_date_key(s)) for i, s in enumerate(sections)]
    dated_only = [t for t in dated if t[2] is not None]
    if len(dated_only) <= keep:
        return {"ws": ws, "kept": len(sections), "archived": 0, "skipped": f"{len(dated_only)} dated ≤ keep={keep}"}

    # Newest `keep` dated sections survive; older dated sections are archived.
    keep_idx = {t[0] for t in sorted(dated_only, key=lambda t: t[2], reverse=True)[:keep]}
    kept_sections, archived_sections = [], []
    for i, s in enumerate(sections):
        key = _section_date_key(s)
        if key is None or i in keep_idx:
            kept_sections.append(s)       # structural OR recent dated → keep
        else:
            archived_sections.append(s)   # old dated → archive

    if not archived_sections:
        return {"ws": ws, "kept": len(sections), "archived": 0, "skipped": "nothing old"}

    if dry_run:
        return {"ws": ws, "kept": len(kept_sections), "archived": len(archived_sections)}

    new_handoff = preamble.rstrip() + "\n\n" + "".join(kept_sections).strip() + "\n"
    arc_path = docs_dir(ws) / "handoff-archive.md"
    arc_header = (
        "# Handoff archive\n\n"
        "_Older handoff snapshots rotated out of `handoff.md` by `_handoff.py` to keep "
        "bootstrap cheap. Searchable; NOT loaded at SessionStart._\n\n"
    )
    add = "".join(archived_sections).strip() + "\n\n"   # newest-of-old batch, file order

    # Strip any prior header/blurb so we don't duplicate it; keep prior entries below.
    prior = arc_path.read_text(errors="ignore") if arc_path.is_file() else ""
    prior_body = ""
    if prior.strip():
        if prior.startswith("# Handoff archive"):
            parts = prior.split("\n\n", 2)          # [header, blurb, body]
            prior_body = parts[2] if len(parts) == 3 else ""
        else:
            prior_body = prior

    with file_lock(f"handoff-{ws}", timeout=5.0):
        atomic_write(arc_path, arc_header + add + prior_body.lstrip())
        atomic_write(hp, new_handoff)

    return {"ws": ws, "kept": len(kept_sections), "archived": len(archived_sections)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Rotate docs/handoff.md — archive old dated snapshots.")
    ap.add_argument("--ws")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not gowth_home().is_dir():
        print("no ~/.gowth-mem directory")
        return 0
    wss = list_workspaces() if args.all else [args.ws or active_workspace()]
    prefix = "[dry-run] " if args.dry_run else ""
    for ws in wss:
        r = rotate_handoff(ws, args.keep, args.dry_run)
        if r.get("archived"):
            print(f"{prefix}[{ws}] kept {r['kept']} sections, archived {r['archived']} old snapshot(s) → docs/handoff-archive.md")
        elif r.get("skipped"):
            print(f"[{ws}] no rotation ({r['skipped']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
