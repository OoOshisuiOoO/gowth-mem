#!/usr/bin/env python3
"""Deep-research workflow: scaffold raw notes, distill them, run quality gates.

Layout (per workspace):
  ~/.gowth-mem/workspaces/<ws>/research/
  ├── _index.md                    Optional TOC (user-maintained)
  └── <topic>/
      ├── raw/
      │   ├── _locate.md           Source-code map (entry-point + file table)
      │   └── <file_basename>.md   Line-by-line notes per source file
      └── distilled.md             1-page synthesis (TL;DR / Architecture / Key facts /
                                   Code anchors / Delta vs current / Open questions)

Commands:
  _research.py --start <topic> [--ws <ws>]    Scaffold raw/_locate.md template
  _research.py --distill <topic> [--ws <ws>]  Scaffold distilled.md template + run quality gate
  _research.py --status [--ws <ws>]           List topics + their state (pending/in-progress/distilled)
  _research.py --lint <topic> [--ws <ws>]     Quality gate only (no scaffold side-effect)

Quality gate:
  - distilled.md word count < 800
  - Every raw note has ≥1 source ref (frontmatter `source_file:` OR body `<word>:<path>:<digit>` OR
    `Source:` line)

Pure stdlib. Handles missing ~/.gowth-mem/ gracefully (exit 0).
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import atomic_write  # type: ignore
from _home import active_workspace, gowth_home, workspace_dir  # type: ignore


SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
# Source ref forms accepted:
#   - prefix:path/file.ext:123   (e.g. openclaw:src/foo.ts:123)  — 2-colon
#   - file.ext:123               (e.g. dreaming.ts:593)          — 1-colon
REF_RE = re.compile(r"\b[\w./-]+\.\w+:\d+")
# Frontmatter fields that count as source attribution (for TOC-style raw notes)
ATTR_FIELDS_RE = re.compile(r"^(source_file|source_files|repo|clone_path):", re.MULTILINE)
SOURCE_LINE_RE = re.compile(r"^Source:", re.MULTILINE)
WORD_LIMIT = 800
RAW_NOTE_MIN = 1  # need ≥1 raw note before distilling


def _validate_slug(slug: str) -> str:
    s = slug.strip().lower()
    if not SLUG_RE.match(s):
        raise SystemExit(f"invalid topic slug: {slug!r} (use [a-z0-9_-])")
    return s


def research_root(ws: str) -> Path:
    return workspace_dir(ws) / "research"


def topic_dir(ws: str, topic: str) -> Path:
    return research_root(ws) / topic


def raw_dir(ws: str, topic: str) -> Path:
    return topic_dir(ws, topic) / "raw"


def distilled_path(ws: str, topic: str) -> Path:
    return topic_dir(ws, topic) / "distilled.md"


def locate_path(ws: str, topic: str) -> Path:
    return raw_dir(ws, topic) / "_locate.md"


# ─── templates ──────────────────────────────────────────────────────────

def _locate_template(topic: str, today: str) -> str:
    return f"""---
type: locate
topic: {topic}
generated: {today}
repo: <owner/repo>
clone_path: <local_clone_path>
---

# {topic} — source code map

## Top-level package
<package_path> — <1-line role>

## Source layout

| File | Lines | Role |
|---|---:|---|
| `<file>` | ? | <role> |

## Open questions
- <what we still need to read>

## Next reads (priority order)
1. <file>
2. <file>
"""


def _distilled_template(topic: str, today: str, raw_files: list[Path]) -> str:
    sources = ", ".join(p.name for p in raw_files) or "<TBD>"
    return f"""---
type: distilled
topic: {topic}
status: draft
distilled_at: {today}
sources: {sources}
---

# {topic} — distilled

## TL;DR
1. <core insight 1>
2. <core insight 2>
3. <core insight 3>

## Architecture

```
<ASCII diagram>
```

## Key facts
- [ref] <claim>. Source: <repo:file:line> — checked {today}

## Code anchors

| Symbol | File | Line | Purpose |
|---|---|---|---|
| `<symbol>` | `<file>` | <line> | <purpose> |

## Delta vs current

| Upstream | Ours | Gap |
|---|---|---|
| <upstream pattern> | <our pattern> | <gap> |

## Open questions
- <unresolved question>
"""


# ─── operations ─────────────────────────────────────────────────────────

def cmd_start(topic: str, ws: str, today: str | None = None) -> Path:
    """Scaffold research/<topic>/raw/_locate.md if missing. Returns the locate path."""
    topic = _validate_slug(topic)
    today = today or date.today().isoformat()
    rdir = raw_dir(ws, topic)
    rdir.mkdir(parents=True, exist_ok=True)
    locate = locate_path(ws, topic)
    if not locate.is_file():
        atomic_write(locate, _locate_template(topic, today))
    return locate


def list_raw_notes(ws: str, topic: str) -> list[Path]:
    rdir = raw_dir(ws, topic)
    if not rdir.is_dir():
        return []
    return sorted(p for p in rdir.glob("*.md") if p.is_file())


def has_source_ref(text: str) -> bool:
    """Return True if a raw note has at least one source citation.

    Accepts: (1) frontmatter source attribution (source_file, source_files, repo,
    clone_path), (2) body `Source:` line, (3) inline `file.ext:LINE` ref
    (1- or 2-colon form).
    """
    if ATTR_FIELDS_RE.search(text):
        return True
    if SOURCE_LINE_RE.search(text):
        return True
    if REF_RE.search(text):
        return True
    return False


def word_count(text: str) -> int:
    """Word count over markdown body (frontmatter excluded)."""
    body = text
    if body.startswith("---"):
        parts = body.split("---", 2)
        if len(parts) >= 3:
            body = parts[2]
    return len(body.split())


def quality_gate(ws: str, topic: str) -> dict:
    """Run quality checks. Returns dict with pass/fail + reasons."""
    topic = _validate_slug(topic)
    raw_notes = list_raw_notes(ws, topic)
    distilled = distilled_path(ws, topic)

    issues: list[str] = []
    missing_refs: list[str] = []

    if len(raw_notes) < RAW_NOTE_MIN:
        issues.append(f"need ≥{RAW_NOTE_MIN} raw note(s); found {len(raw_notes)}")

    for note in raw_notes:
        try:
            text = note.read_text(errors="ignore")
        except Exception:
            continue
        if not has_source_ref(text):
            missing_refs.append(note.name)

    if missing_refs:
        issues.append(f"raw notes missing source ref: {', '.join(missing_refs)}")

    distilled_words = 0
    if distilled.is_file():
        text = distilled.read_text(errors="ignore")
        distilled_words = word_count(text)
        if distilled_words >= WORD_LIMIT:
            issues.append(f"distilled.md is {distilled_words} words (limit {WORD_LIMIT})")
    else:
        issues.append("distilled.md missing")

    return {
        "topic": topic,
        "raw_notes": [p.name for p in raw_notes],
        "raw_count": len(raw_notes),
        "distilled_exists": distilled.is_file(),
        "distilled_words": distilled_words,
        "missing_refs": missing_refs,
        "issues": issues,
        "passed": len(issues) == 0,
    }


def cmd_distill(topic: str, ws: str, today: str | None = None) -> dict:
    """Scaffold distilled.md if missing, then run quality gate. Returns gate result."""
    topic = _validate_slug(topic)
    today = today or date.today().isoformat()
    raw_notes = list_raw_notes(ws, topic)
    if len(raw_notes) < RAW_NOTE_MIN:
        return {
            "topic": topic,
            "passed": False,
            "issues": [f"no raw notes yet — run /mem-research-start {topic} first"],
            "raw_count": 0,
        }
    distilled = distilled_path(ws, topic)
    if not distilled.is_file():
        atomic_write(distilled, _distilled_template(topic, today, raw_notes))
    return quality_gate(ws, topic)


def list_topics(ws: str) -> list[str]:
    rroot = research_root(ws)
    if not rroot.is_dir():
        return []
    return sorted(p.name for p in rroot.iterdir() if p.is_dir() and not p.name.startswith("."))


def status_for(ws: str, topic: str) -> dict:
    """Return concise status: pending|in-progress|distilled."""
    raw_notes = list_raw_notes(ws, topic)
    distilled = distilled_path(ws, topic)
    if not raw_notes:
        state = "pending"
    elif not distilled.is_file():
        state = "in-progress"
    else:
        state = "distilled"
    return {
        "topic": topic,
        "state": state,
        "raw_count": len(raw_notes),
        "distilled_words": word_count(distilled.read_text(errors="ignore")) if distilled.is_file() else 0,
    }


# ─── CLI ─────────────────────────────────────────────────────────────────

def _print_gate(result: dict) -> None:
    icon = "PASS" if result.get("passed") else "FAIL"
    print(f"[{icon}] topic={result.get('topic')}")
    print(f"  raw_notes: {result.get('raw_count', 0)} files")
    if result.get("distilled_exists"):
        print(f"  distilled.md: {result.get('distilled_words', 0)} words (limit {WORD_LIMIT})")
    if result.get("issues"):
        print("  issues:")
        for i in result["issues"]:
            print(f"    - {i}")


def _cli() -> int:
    p = argparse.ArgumentParser(prog="_research.py")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--start", metavar="TOPIC", help="Scaffold raw/_locate.md for new topic")
    g.add_argument("--distill", metavar="TOPIC", help="Scaffold distilled.md + run quality gate")
    g.add_argument("--lint", metavar="TOPIC", help="Run quality gate only (no scaffold)")
    g.add_argument("--status", action="store_true", help="List all research topics + state")
    p.add_argument("--ws", help="Workspace (default: active)")
    args = p.parse_args()

    # Bail out gracefully if gowth-mem home doesn't exist
    if not gowth_home().is_dir():
        print("(no ~/.gowth-mem/ — run /mem-install first)")
        return 0

    ws = args.ws or active_workspace()

    if args.start:
        loc = cmd_start(args.start, ws)
        print(f"scaffolded: {loc}")
        return 0

    if args.distill:
        result = cmd_distill(args.distill, ws)
        _print_gate(result)
        return 0 if result.get("passed") else 1

    if args.lint:
        result = quality_gate(ws, args.lint)
        _print_gate(result)
        return 0 if result.get("passed") else 1

    if args.status:
        topics = list_topics(ws)
        if not topics:
            print(f"(no research topics in workspace {ws!r})")
            return 0
        print(f"research topics in workspace {ws!r}:")
        for t in topics:
            s = status_for(ws, t)
            extra = f" [{s['distilled_words']}w]" if s["distilled_words"] else ""
            print(f"  {s['state']:13s} {t}  ({s['raw_count']} raw){extra}")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(_cli())
