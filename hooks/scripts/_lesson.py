#!/usr/bin/env python3
"""Append an experience entry (lesson / postmortem / troubleshooting) to <topic>/lessons.md.

5-field schema (all cited canonical sources):
  - Symptom    — observable error/behavior   (AWS EKS heading + Beads TROUBLESHOOTING)
  - Tried      — what was attempted, in order (Stack Overflow + GitHub bug-report)
  - Root cause — 1-line answer (5 Whys / man-pages ERRORS)
  - Fix        — working command/patch/config (Stripe Solutions + Beads Fix)
  - Source     — commit | file:line | URL    (Stripe doc_url + AI-trade [ref] rule)

Storage: one `lessons.md` per topic folder (NOT per sub-aspect file).
  - Match landing  workspaces/<ws>/<topic>/<topic>.md  → lessons go to workspaces/<ws>/<topic>/lessons.md
  - Match sub-aspect workspaces/<ws>/<topic>/<aspect>.md → lessons go to workspaces/<ws>/<topic>/lessons.md
  - Legacy flat workspaces/<ws>/<topic>.md → lessons go to workspaces/<ws>/<topic>-lessons.md
    (we don't auto-promote flat files to folder; user can /mem-restructure later)

Format per entry: H2 heading "## [YYYY-MM-DD] <symptom truncated>" + 5 bold-prefix bullets.
Newest entries appended at TOP under "## Entries" section so most-recent-first reading
without scrolling. (Mirrors Logseq journal newest-on-top convention.)

CLI:
  _lesson.py --symptom "..." --tried "..." --root "..." --fix "..." [--source "..."] [--topic <slug>] [--ws <name>]
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import atomic_write  # type: ignore
from _home import (  # type: ignore
    active_workspace,
    is_topic_folder,
    topic_landing,
    workspace_dir,
)
from _topic import route as topic_route  # type: ignore


HEADER = "# Lessons & Troubleshooting\n\n> Append-only ledger. Newest-first under `## Entries`. Schema cited from NASA LLIS / Army AAR / AWS EKS / Stripe / 5 Whys.\n\n## Entries\n\n"


def _truncate(s: str, n: int = 60) -> str:
    s = s.strip().splitlines()[0]
    return (s[:n] + "…") if len(s) > n else s


def _resolve_target(ws: str, topic: str | None, content: str) -> Path:
    """Return path to lessons.md for the routed topic.

    Routing rules:
      - explicit --topic <slug>: ensure_topic if missing, then put lessons next to landing
      - else: _topic.route(content) → matched file → lessons.md in matched file's parent folder
        (parent folder is the topic folder for landing/sub-aspect; or workspace root for legacy flat)
    """
    ws_root = workspace_dir(ws).resolve()

    if topic:
        from _topic import ensure_topic  # type: ignore
        landing = ensure_topic(topic, ws=ws)
        return landing.parent / "lessons.md"

    slug, matched, _section = topic_route(content, ws=ws)
    target_dir = matched.parent
    if target_dir == ws_root:
        # Legacy flat topic file at ws root → use sibling <slug>-lessons.md to avoid clobbering
        return ws_root / f"{matched.stem}-lessons.md"
    return target_dir / "lessons.md"


def append_lesson(
    symptom: str,
    tried: str,
    root_cause: str,
    fix: str,
    source: str = "",
    *,
    topic: str | None = None,
    ws: str | None = None,
    today: str | None = None,
) -> Path:
    """Append a 5-field lesson entry to the topic's lessons.md. Returns the path written."""
    ws = ws or active_workspace()
    today = today or date.today().isoformat()

    routing_text = " ".join(filter(None, [symptom, tried, root_cause, fix]))
    target = _resolve_target(ws, topic, routing_text)

    heading = f"## [{today}] {_truncate(symptom)}\n"
    body_lines = [
        f"**Symptom:** {symptom.strip()}",
        f"**Tried:** {tried.strip()}",
        f"**Root cause:** {root_cause.strip()}",
        f"**Fix:** {fix.strip()}",
    ]
    if source.strip():
        body_lines.append(f"**Source:** {source.strip()}")
    entry = heading + "\n".join(body_lines) + "\n\n"

    if target.is_file():
        existing = target.read_text(errors="ignore")
        if "## Entries" in existing:
            head, _, rest = existing.partition("## Entries\n")
            new = head + "## Entries\n\n" + entry + rest.lstrip("\n")
        else:
            new = existing.rstrip() + "\n\n## Entries\n\n" + entry
    else:
        new = HEADER + entry

    atomic_write(target, new)
    return target


def _cli() -> int:
    p = argparse.ArgumentParser(prog="_lesson.py")
    p.add_argument("--symptom", required=True)
    p.add_argument("--tried", required=True)
    p.add_argument("--root", required=True, help="Root cause (1 line)")
    p.add_argument("--fix", required=True)
    p.add_argument("--source", default="")
    p.add_argument("--topic", help="Force topic slug (skip auto-routing)")
    p.add_argument("--ws", help="Workspace (default: active)")
    args = p.parse_args()
    written = append_lesson(
        symptom=args.symptom,
        tried=args.tried,
        root_cause=args.root,
        fix=args.fix,
        source=args.source,
        topic=args.topic,
        ws=args.ws,
    )
    print(f"appended: {written}")
    # Trigger MOC refresh — best-effort
    try:
        import subprocess
        scripts = Path(__file__).parent
        subprocess.run(
            ["python3", str(scripts / "_moc.py"), "--ws", args.ws or active_workspace()],
            check=False, timeout=10,
        )
    except Exception:
        pass
    return 0


# memL one-liner parser: "symptom -- tried -- root -- fix [-- source]"
DELIM = re.compile(r"\s+--\s+")


def parse_oneliner(text: str) -> dict | None:
    """Parse `symptom -- tried -- root -- fix [-- source]`. Returns dict or None if malformed."""
    parts = DELIM.split(text.strip())
    if len(parts) < 4 or len(parts) > 5:
        return None
    return {
        "symptom": parts[0],
        "tried": parts[1],
        "root_cause": parts[2],
        "fix": parts[3],
        "source": parts[4] if len(parts) == 5 else "",
    }


if __name__ == "__main__":
    sys.exit(_cli())
