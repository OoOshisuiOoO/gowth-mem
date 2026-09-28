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
  - Auto-route via _topic.plan_topic_folder: the best keyword-matched topic
    FOLDER (or a new one), created only after dedup + the gate pass; write
    lessons.md inside (NEVER spawn a dated aspect).

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
from _atomic import safe_write  # type: ignore
from _dedup import is_duplicate  # type: ignore
from _home import (  # type: ignore
    TOPIC_LESSONS,
    active_workspace,
    read_settings,
    workspace_dir,
)
from _tags import extract_tags, format_suffix, max_per_entry, tags_enabled  # type: ignore
from _topic import (  # type: ignore
    materialise_topic_folder, plan_topic_folder, resolve_topic_folder,
    validate_workspace,
)


HEADER = "# Lessons & Troubleshooting\n\n> Append-only ledger. Newest-first under `## Entries`. Schema cited from NASA LLIS / Army AAR / AWS EKS / Stripe / 5 Whys.\n\n## Entries\n\n"


def _truncate(s: str, n: int = 60) -> str:
    s = s.strip().splitlines()[0]
    return (s[:n] + "…") if len(s) > n else s


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
    """Append a 5-field lesson entry to the topic's lessons.md. Returns the path
    written (or, when refused, the path it would have gone to)."""
    return append_lesson_status(symptom, tried, root_cause, fix, source,
                                topic=topic, ws=ws, today=today)[0]


def append_lesson_status(
    symptom: str,
    tried: str,
    root_cause: str,
    fix: str,
    source: str = "",
    *,
    topic: str | None = None,
    ws: str | None = None,
    today: str | None = None,
) -> tuple[Path, str]:
    """`append_lesson` plus the outcome: `written`, `duplicate` or
    `rejected:<gate rule>`. v4.7.6: dedup and the gate run BEFORE the topic
    folder is created — a refused lesson used to leave a README-only folder
    behind, and the CLI printed "appended:" for it.

    Raises ValueError when an explicit `topic` names a domain folder, matches
    several nested topics, or resolves outside the workspace (a symlink) —
    the message names the candidates; nothing is written."""
    ws = ws or active_workspace()
    validate_workspace(ws)  # reject junk ws BEFORE any mkdir (resolve_topic_folder)
    today = today or date.today().isoformat()

    routing_text = " ".join(filter(None, [symptom, tried, root_cause, fix]))
    # Decide once (one vault walk), check, and only then create.
    if topic:
        planned = resolve_topic_folder(topic, ws=ws, ensure=False) / TOPIC_LESSONS
    else:
        t_slug, t_folder = plan_topic_folder(routing_text, ws=ws)
        planned = (t_folder if t_folder is not None
                   else workspace_dir(ws) / t_slug) / TOPIC_LESSONS

    # v3.4: cross-file dedup — skip if (tag, content) already indexed.
    # Lessons live under the [exp] tag in _index.py's chunking model.
    if is_duplicate(workspace_dir(ws), "exp", routing_text):
        return planned, "duplicate"

    # v3.6: hard write-rules gate on the 5-field lesson (canon §1; deterministic).
    try:
        from _home import setting as _setting  # type: ignore
        if _setting("gate.enabled", bool, True):
            from _gate import evaluate_lesson  # type: ignore
            _v = evaluate_lesson(symptom, tried, root_cause, fix, source)
            if not _v.ok:
                from _debug import log_debug  # type: ignore
                log_debug("lesson", f"gate reject [{_v.reason}]")
                return planned, f"rejected:{_v.reason or 'gate'}"
    except Exception:
        pass

    folder = (resolve_topic_folder(topic, ws=ws) if topic
              else materialise_topic_folder(t_slug, t_folder, ws=ws))
    target = folder / TOPIC_LESSONS

    # v4.0: deterministic auto-tags on the entry's first line (the heading).
    # lessons.md has no frontmatter — inline only (no frontmatter union).
    heading_line = f"## [{today}] {_truncate(symptom)}"
    if tags_enabled():
        try:
            tags = extract_tags(routing_text, max_per_entry())
            heading_line = heading_line + format_suffix(tags)
        except Exception:
            pass
    heading = heading_line + "\n"
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

    safe_write(target, new)

    # v4.3: refresh the index for this ledger so the lesson is recallable NOW
    # (see _index.reindex_paths — best-effort, never fails a write).
    try:
        from _index import reindex_paths  # type: ignore
        reindex_paths([target])
    except Exception:
        pass

    return target, "written"


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
    try:
        path, status = append_lesson_status(
            symptom=args.symptom,
            tried=args.tried,
            root_cause=args.root,
            fix=args.fix,
            source=args.source,
            topic=args.topic,
            ws=args.ws,
        )
    except ValueError as exc:   # --topic names a domain, an ambiguous or an invalid slug
        print(f"not appended: {exc}")
        return 2
    # v4.7.6: said "appended:" even when dedup or the gate refused the lesson.
    if status != "written":
        print(f"not appended ({status}): {path}")
        return 0
    print(f"appended: {path}")
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
