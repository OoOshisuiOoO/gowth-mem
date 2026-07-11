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
import datetime as _dt
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import atomic_write  # type: ignore
from _home import active_workspace, docs_dir, gowth_home, list_workspaces  # type: ignore
from _lock import file_lock  # type: ignore

DEFAULT_KEEP = 10
DEFAULT_MAX_AGE_DAYS = 14   # v4.1 bullet rotation: archive [done] bullets older than this
DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})([a-z]?)")
H2_RE = re.compile(r"^##\s+", re.MULTILINE)
# v4.1 — flat-list handoff format: `- host:<machine> ... YYYY-MM-DD ... [status]`.
BULLET_RE = re.compile(r"^- host:\S+")
# Statuses that mark a bullet as LIVE state (never age-archived): open threads,
# blockers, in-progress work. `[done+blocker]` etc. match via substring.
LIVE_STATUS_RE = re.compile(r"\[[^\]\n]*(?:doing|blocker|thread|next)[^\]\n]*\]")


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


def _bullet_date(item: str):
    """Return datetime.date parsed from a bullet's first line, or None."""
    first = item.splitlines()[0] if item else ""
    m = DATE_RE.search(first)
    if not m:
        return None
    try:
        return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _split_bullet_items(section: str) -> tuple[str, list[str], str]:
    """Split a section into (head, items, tail).

    An item is a `- host:` line plus its continuation lines (subsequent
    non-blank lines that don't start a new bullet or heading). `head` is
    everything before the first bullet; `tail` is everything after the last
    item's continuation block (e.g. trailing blank lines).
    """
    lines = section.splitlines(keepends=True)
    head: list[str] = []
    items: list[list[str]] = []
    tail: list[str] = []
    cur: list[str] | None = None
    for ln in lines:
        if BULLET_RE.match(ln):
            cur = [ln]
            items.append(cur)
        elif cur is not None and ln.strip() and not ln.startswith(("#", "- ")):
            cur.append(ln)            # continuation line
        elif cur is None:
            head.append(ln)
        else:
            cur = None                # blank / new non-host bullet ends the item
            tail.append(ln)
    return "".join(head), ["".join(i) for i in items], "".join(tail)


def _rotate_stale_bullets(sections: list[str], cutoff: "_dt.date") -> tuple[list[str], list[str]]:
    """Archive `- host:` bullets dated before cutoff, unless their status is
    live ([doing]/[blocker]/[thread]/[next] — including compounds). Returns
    (new_sections, archived_items)."""
    out_sections: list[str] = []
    archived: list[str] = []
    for s in sections:
        head, items, tail = _split_bullet_items(s)
        if not items:
            out_sections.append(s)
            continue
        kept_items = []
        for it in items:
            d = _bullet_date(it)
            first = it.splitlines()[0]
            if d is not None and d < cutoff and not LIVE_STATUS_RE.search(first):
                archived.append(it)
            else:
                kept_items.append(it)
        if len(kept_items) == len(items):
            out_sections.append(s)
        else:
            out_sections.append(head + "".join(kept_items) + tail)
    return out_sections, archived


def rotate_handoff(ws: str, keep: int, dry_run: bool,
                   max_age_days: int = DEFAULT_MAX_AGE_DAYS,
                   today: str | None = None) -> dict:
    hp = docs_dir(ws) / "handoff.md"
    if not hp.is_file():
        return {"ws": ws, "kept": 0, "archived": 0, "skipped": "no handoff"}
    text = hp.read_text(errors="ignore")
    preamble, sections = _split_sections(text)

    dated = [(i, s, _section_date_key(s)) for i, s in enumerate(sections)]
    dated_only = [t for t in dated if t[2] is not None]

    # Pass 1 (v3.6): rotate whole dated `## <snapshot>` sections beyond `keep`.
    archived_sections: list[str] = []
    if len(dated_only) > keep:
        keep_idx = {t[0] for t in sorted(dated_only, key=lambda t: t[2], reverse=True)[:keep]}
        kept_sections = []
        for i, s in enumerate(sections):
            key = _section_date_key(s)
            if key is None or i in keep_idx:
                kept_sections.append(s)       # structural OR recent dated → keep
            else:
                archived_sections.append(s)   # old dated → archive
    else:
        kept_sections = list(sections)

    # Pass 2 (v4.1): rotate stale `- host:` bullets INSIDE structural sections.
    # Real-world handoffs are flat bullet lists under `## Entries` — pass 1
    # never touches them, so a 57 KB file stays bootstrap-loaded forever.
    archived_bullets: list[str] = []
    if max_age_days and max_age_days > 0:
        today_d = _dt.date.fromisoformat(today) if today else _dt.date.today()
        cutoff = today_d - _dt.timedelta(days=max_age_days)
        kept_sections, archived_bullets = _rotate_stale_bullets(kept_sections, cutoff)

    if not archived_sections and not archived_bullets:
        return {"ws": ws, "kept": len(sections), "archived": 0, "bullets_archived": 0,
                "skipped": f"{len(dated_only)} dated ≤ keep={keep}, no stale bullets"}

    if dry_run:
        return {"ws": ws, "kept": len(kept_sections),
                "archived": len(archived_sections),
                "bullets_archived": len(archived_bullets)}

    new_handoff = preamble.rstrip() + "\n\n" + "".join(kept_sections).strip() + "\n"
    arc_path = docs_dir(ws) / "handoff-archive.md"
    arc_header = (
        "# Handoff archive\n\n"
        "_Older handoff snapshots rotated out of `handoff.md` by `_handoff.py` to keep "
        "bootstrap cheap. Searchable; NOT loaded at SessionStart._\n\n"
    )
    add = ""
    if archived_sections:
        add += "".join(archived_sections).strip() + "\n\n"   # newest-of-old batch, file order
    if archived_bullets:
        stamp = today or _dt.date.today().isoformat()
        add += (f"## rotated bullets {stamp}\n\n"
                + "".join(archived_bullets).strip() + "\n\n")

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

    return {"ws": ws, "kept": len(kept_sections), "archived": len(archived_sections),
            "bullets_archived": len(archived_bullets)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Rotate docs/handoff.md — archive old dated snapshots and stale bullets.")
    ap.add_argument("--ws")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    ap.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS,
                    help="archive [done] `- host:` bullets older than N days (0 = disable)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not gowth_home().is_dir():
        print("no ~/.gowth-mem directory")
        return 0
    wss = list_workspaces() if args.all else [args.ws or active_workspace()]
    prefix = "[dry-run] " if args.dry_run else ""
    for ws in wss:
        r = rotate_handoff(ws, args.keep, args.dry_run, max_age_days=args.max_age_days)
        if r.get("archived") or r.get("bullets_archived"):
            print(f"{prefix}[{ws}] kept {r['kept']} sections, archived {r['archived']} snapshot(s) "
                  f"+ {r.get('bullets_archived', 0)} stale bullet(s) → docs/handoff-archive.md")
        elif r.get("skipped"):
            print(f"[{ws}] no rotation ({r['skipped']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
