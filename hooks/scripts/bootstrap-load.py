#!/usr/bin/env python3
"""SessionStart hook (v2.0): load global ~/.gowth-mem/ memory.

Files loaded (in order):
  1. AGENTS.md                   — operating rules
  2. topics/_index.md            — topic registry
  3. docs/handoff.md             — session state (per-machine prefixes)
  4. docs/secrets.md             — resource pointers
  5. docs/tools.md               — cross-topic tool registry
  6. top-3 most-recently-touched topics/*.md   — recent context
  7. journal/<today>.md / <yesterday>.md
  8. skills/_index               — synthesized 1-line skill index

Caps: 12k char/file, 60k total. Skips blanks, marks truncations.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _home import (  # type: ignore
    agents_md,
    docs_dir,
    gowth_home,
    journal_dir,
    skills_dir,
    topics_dir,
)

MAX_PER_FILE = 12_000
MAX_TOTAL = 60_000
SKILL_INDEX_MAX_CHARS = 2_000
RECENT_TOPICS = 3


def build_skills_index() -> str:
    sd = skills_dir()
    if not sd.is_dir():
        return ""
    entries: list[str] = []
    for f in sorted(sd.glob("*.md")):
        if f.name == "_index.md":
            continue
        try:
            text = f.read_text(errors="ignore")
        except Exception:
            continue
        name = f.stem
        desc = ""
        m = re.search(r"^---\s*$(.*?)^---\s*$", text, re.DOTALL | re.MULTILINE)
        if m:
            front = m.group(1)
            d = re.search(r"^description:\s*(.+)$", front, re.MULTILINE)
            if d:
                desc = d.group(1).strip().strip("\"'")
            n = re.search(r"^name:\s*(.+)$", front, re.MULTILINE)
            if n:
                name = n.group(1).strip().strip("\"'")
        entries.append(f"- **{name}** — {desc}" if desc else f"- **{name}**")
    if not entries:
        return ""
    return "\n".join(entries)[:SKILL_INDEX_MAX_CHARS]


def recent_topic_files() -> list[Path]:
    td = topics_dir()
    if not td.is_dir():
        return []
    files = [f for f in td.glob("*.md") if f.name != "_index.md"]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:RECENT_TOPICS]


def main() -> int:
    gh = gowth_home()
    docs = docs_dir()
    today = date.today()
    yesterday = today - timedelta(days=1)

    candidates: list[Path] = [
        agents_md(),
        topics_dir() / "_index.md",
        docs / "handoff.md",
        docs / "secrets.md",
        docs / "tools.md",
    ]
    candidates.extend(recent_topic_files())
    candidates.extend([
        journal_dir() / f"{today.isoformat()}.md",
        journal_dir() / f"{yesterday.isoformat()}.md",
    ])

    parts: list[str] = []
    total = 0
    stop = False
    for f in candidates:
        if stop:
            break
        if not f.is_file():
            continue
        try:
            raw = f.read_text(errors="ignore")
        except Exception:
            continue
        if not raw.strip():
            continue
        truncated_file = len(raw) > MAX_PER_FILE
        chunk = raw[:MAX_PER_FILE]
        room = MAX_TOTAL - total
        if room <= 200:
            break
        truncated_total = False
        if len(chunk) > room:
            chunk = chunk[:room]
            truncated_total = True
            stop = True
        try:
            rel = f.relative_to(gh)
            label = f"~/.gowth-mem/{rel}"
        except ValueError:
            label = str(f)
        marker = f"\n[truncated, see {label}]" if (truncated_file or truncated_total) else ""
        parts.append(f"\n=== {label} ===\n{chunk}{marker}")
        total += len(chunk)

    skills_index = build_skills_index()
    if skills_index and total + len(skills_index) + 100 < MAX_TOTAL:
        parts.append(f"\n=== ~/.gowth-mem/skills/ (index) ===\n{skills_index}")
        total += len(skills_index)

    if not parts:
        return 0

    out = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "[gowth-mem:bootstrap]" + "".join(parts),
        }
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
