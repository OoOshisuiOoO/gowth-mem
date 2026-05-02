#!/usr/bin/env python3
"""SessionStart hook (v2.2): load global ~/.gowth-mem/ memory, scoped to the
active workspace.

Files loaded (in order):
  1. AGENTS.md                                — global rules
  2. shared/files.md                          — top-level tree
  3. shared/secrets.md                        — env-var pointers
  4. shared/tools.md                          — system-wide tools
  5. workspaces/<ws>/AGENTS.md                — optional workspace override
  6. workspaces/<ws>/_MAP.md                  — root topic MOC
  7. workspaces/<ws>/docs/handoff.md          — session state
  8. workspaces/<ws>/docs/{exp,ref,tools,files}.md
  9. top-3 most-recently-touched topics/**.md (frontmatter.last_touched desc, mtime fallback)
  10. workspaces/<ws>/journal/<today>.md, <yesterday>.md
  11. shared/skills/_index + workspaces/<ws>/skills/_index   — synthesized

Caps: 12k char/file, 60k total. Skips blanks, marks truncations.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _frontmatter import parse_file  # type: ignore
from _home import (  # type: ignore
    active_workspace,
    agents_md,
    docs_dir,
    gowth_home,
    journal_dir,
    secrets_md,
    shared_dir,
    shared_files_md,
    shared_skills_dir,
    shared_tools_md,
    skills_dir,
    topics_dir,
    workspace_agents_md,
    workspace_dir,
    workspace_moc,
)

MAX_PER_FILE = 12_000
MAX_TOTAL = 60_000
SKILL_INDEX_MAX_CHARS = 2_000
RECENT_TOPICS = 3


def _build_skills_index(d: Path) -> str:
    if not d.is_dir():
        return ""
    entries: list[str] = []
    for f in sorted(d.glob("*.md")):
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
            dm = re.search(r"^description:\s*(.+)$", front, re.MULTILINE)
            if dm:
                desc = dm.group(1).strip().strip("\"'")
            nm = re.search(r"^name:\s*(.+)$", front, re.MULTILINE)
            if nm:
                name = nm.group(1).strip().strip("\"'")
        entries.append(f"- **{name}** — {desc}" if desc else f"- **{name}**")
    if not entries:
        return ""
    return "\n".join(entries)[:SKILL_INDEX_MAX_CHARS]


def _recent_topic_files(ws: str) -> list[Path]:
    td = topics_dir(ws)
    if not td.is_dir():
        return []
    files = [p for p in td.rglob("*.md") if p.name not in ("_MAP.md", "_index.md")]

    def sort_key(p: Path):
        fm, _ = parse_file(p)
        last = fm.get("last_touched") or ""
        return (last, p.stat().st_mtime)

    files.sort(key=sort_key, reverse=True)
    return files[:RECENT_TOPICS]


def main() -> int:
    gh = gowth_home()
    ws = active_workspace()
    today = date.today()
    yesterday = today - timedelta(days=1)

    candidates: list[Path] = [
        agents_md(),
        shared_files_md(),
        secrets_md(),
        shared_tools_md(),
        workspace_agents_md(ws),
        workspace_moc(ws),
        docs_dir(ws) / "handoff.md",
        docs_dir(ws) / "exp.md",
        docs_dir(ws) / "ref.md",
        docs_dir(ws) / "tools.md",
        docs_dir(ws) / "files.md",
    ]
    candidates.extend(_recent_topic_files(ws))
    candidates.extend([
        journal_dir(ws) / f"{today.isoformat()}.md",
        journal_dir(ws) / f"{yesterday.isoformat()}.md",
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

    # Skills indexes (shared first, then workspace override) — honor cap from prior loop
    if not stop:
        for label, idx_dir in (
            ("shared/skills/", shared_skills_dir()),
            (f"workspaces/{ws}/skills/", skills_dir(ws)),
        ):
            text = _build_skills_index(idx_dir)
            if not text:
                continue
            if total + len(text) + 100 >= MAX_TOTAL:
                break
            parts.append(f"\n=== ~/.gowth-mem/{label} (index) ===\n{text}")
            total += len(text)

    if not parts:
        return 0

    out = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": f"[gowth-mem:bootstrap workspace={ws}]" + "".join(parts),
        }
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
