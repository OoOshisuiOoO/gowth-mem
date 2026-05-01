#!/usr/bin/env python3
"""SessionStart hook: assemble AGENTS.md + docs/ working memory + recent journal + skills index.

Path resolution (v1.0 centralized vs v0.9 legacy):
- If `.gowth-mem/AGENTS.md` or `.gowth-mem/docs/` exists → v1.0; load from there.
- Else → v0.9 fallback; load from workspace root.

Files loaded (in order):
  1. AGENTS.md            — operating rules
  2. docs/handoff.md      — session state
  3. docs/exp.md          — episodic curated
  4. docs/ref.md          — verified facts
  5. docs/tools.md        — tool registry
  6. docs/secrets.md      — resource pointers
  7. docs/files.md        — project structure
  8. docs/journal/<today>.md
  9. docs/journal/<yesterday>.md
 10. docs/skills/_index   — synthesized 1-line index of available skills

Caps: 12k char/file, 60k total. Skips blanks, marks truncations.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _paths import resolve_root, docs_root  # type: ignore

MAX_PER_FILE = 12_000
MAX_TOTAL = 60_000
SKILL_INDEX_MAX_CHARS = 2_000


def build_skills_index(workspace: Path) -> str:
    skills_dir = docs_root(workspace) / "skills"
    if not skills_dir.is_dir():
        return ""
    entries: list[str] = []
    for f in sorted(skills_dir.glob("*.md")):
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


def main() -> int:
    workspace = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    root = resolve_root(workspace)
    docs = docs_root(workspace)
    today = date.today()
    yesterday = today - timedelta(days=1)

    candidates = [
        root / "AGENTS.md",
        docs / "handoff.md",
        docs / "exp.md",
        docs / "ref.md",
        docs / "tools.md",
        docs / "secrets.md",
        docs / "files.md",
        docs / "journal" / f"{today.isoformat()}.md",
        docs / "journal" / f"{yesterday.isoformat()}.md",
    ]

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
            rel = f.relative_to(workspace)
        except ValueError:
            rel = f
        marker = f"\n[truncated, see {rel}]" if (truncated_file or truncated_total) else ""
        parts.append(f"\n=== {rel} ===\n{chunk}{marker}")
        total += len(chunk)

    skills_index = build_skills_index(workspace)
    if skills_index and total + len(skills_index) + 100 < MAX_TOTAL:
        parts.append(f"\n=== docs/skills/ (index) ===\n{skills_index}")
        total += len(skills_index)

    if not parts:
        return 0

    out = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "[openclaw-bridge:bootstrap]" + "".join(parts),
        }
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
