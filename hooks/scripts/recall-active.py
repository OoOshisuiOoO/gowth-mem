#!/usr/bin/env python3
"""UserPromptSubmit hook: grep-based active memory recall.

Lite version of OpenClaw's active-memory blocking sub-agent. Extracts ≥5-char
alphanumeric keywords from the prompt, greps memory/*.md (newest first),
returns up to 3 matching files (top 3 lines each) as additional context.
Silent if nothing matches or no memory dir exists.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

MAX_KEYWORDS = 8
MAX_FILES = 3
MAX_LINES_PER_FILE = 3
MAX_PROMPT_CHARS = 1500
MAX_CANDIDATE_FILES = 50


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    prompt = (data.get("prompt") or data.get("user_prompt") or "")[:MAX_PROMPT_CHARS]
    if not prompt.strip():
        return 0

    workspace = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    mem_dir = workspace / "memory"
    if not mem_dir.is_dir():
        return 0

    words = re.findall(r"\b\w{5,}\b", prompt.lower())
    seen: set[str] = set()
    kws: list[str] = []
    for w in words:
        if w in seen:
            continue
        seen.add(w)
        kws.append(w)
        if len(kws) >= MAX_KEYWORDS:
            break
    if not kws:
        return 0

    pattern = re.compile("|".join(re.escape(k) for k in kws), re.IGNORECASE)

    candidates = sorted(
        (p for p in mem_dir.glob("*.md") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:MAX_CANDIDATE_FILES]

    hits: list[tuple[Path, list[str]]] = []
    for f in candidates:
        try:
            text = f.read_text(errors="ignore")
        except Exception:
            continue
        matched = [ln.strip() for ln in text.splitlines() if pattern.search(ln)]
        if matched:
            hits.append((f, matched[:MAX_LINES_PER_FILE]))
        if len(hits) >= MAX_FILES:
            break
    if not hits:
        return 0

    parts = ["[openclaw-bridge:recall] Có thể relevant:"]
    for f, lines in hits:
        rel = f.relative_to(workspace)
        parts.append(f"\n--- {rel} ---")
        parts.extend(lines)
    out = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "\n".join(parts),
        }
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
