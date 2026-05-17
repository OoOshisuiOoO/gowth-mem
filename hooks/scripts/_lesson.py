#!/usr/bin/env python3
"""Append an experience entry (lesson / postmortem / troubleshooting) to <topic>/lessons.md.

5-field schema (all cited canonical sources):
  - Symptom    — observable error/behavior   (AWS EKS heading + Beads TROUBLESHOOTING)
  - Tried      — what was attempted, in order (Stack Overflow + GitHub bug-report)
  - Root cause — 1-line answer (5 Whys / man-pages ERRORS)
  - Fix        — working command/patch/config (Stripe Solutions + Beads Fix)
  - Source     — commit | file:line | URL    (Stripe doc_url + AI-trade [ref] rule)

Storage v3.0: one `lessons.md` per topic folder.
  - Explicit --topic <slug>:  workspaces/<ws>/<slug>/lessons.md (ensure folder via F4)
  - Auto-route via _topic.derive_topic_slug: pick top-keyword slug, ensure folder,
    write lessons.md inside (NEVER spawn a dated-aspect file for lessons).

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
    TOPIC_LESSONS,
    active_workspace,
)
from _topic import derive_topic_slug, resolve_topic_folder  # type: ignore


HEADER = "# Lessons & Troubleshooting\n\n> Append-only ledger. Newest-first under `## Entries`. Schema cited from NASA LLIS / Army AAR / AWS EKS / Stripe / 5 Whys.\n\n## Entries\n\n"


def _truncate(s: str, n: int = 60) -> str:
    s = s.strip().splitlines()[0]
    return (s[:n] + "…") if len(s) > n else s


def _resolve_target(ws: str, topic: str | None, content: str) -> Path:
    """v3.0: return path to `<folder>/lessons.md` for the routed topic folder.

    Uses `resolve_topic_folder` (F4) so we never spawn a parasitic dated-aspect
    file just to figure out where lessons should live.
    """
    if topic:
        folder = resolve_topic_folder(topic, ws=ws)
    else:
        slug = derive_topic_slug(content, ws=ws)
        folder = resolve_topic_folder(slug, ws=ws)
    return folder / TOPIC_LESSONS


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
