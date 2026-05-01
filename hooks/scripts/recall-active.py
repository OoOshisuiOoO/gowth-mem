#!/usr/bin/env python3
"""UserPromptSubmit hook: contextual recall with MMR diversity.

Improvements over v0.3:
- **Contextual retrieval (Anthropic)**: each match line is prepended with the
  nearest preceding `## ` / `### ` heading, so the model sees WHERE the snippet
  lives. This was reported to cut retrieval failures by 35-67%.
- **MMR diversity (Maximal Marginal Relevance)**: when multiple hits would
  otherwise come from the same file/section, prefer hits from distinct files
  to maximize informational coverage.
- **Per-layer score boost**: docs/journal/today gets a recency boost; docs/*
  curated files get a quality boost; wiki/* files are reachable but lower priority.

Scans:
  - docs/**/*.md   (gowth-mem: journal + curated)
  - wiki/**/*.md   (claude-obsidian, if vault exists)

Returns up to 3 matching files (top 3 lines each) as additional context.
Silent if nothing matches.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

MAX_KEYWORDS = 8
MAX_FILES = 3
MAX_LINES_PER_FILE = 3
MAX_PROMPT_CHARS = 1500
MAX_CANDIDATE_FILES = 80

HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")


def collect_candidates(workspace: Path) -> list[Path]:
    out: list[Path] = []
    docs_dir = workspace / "docs"
    if docs_dir.is_dir():
        out.extend(p for p in docs_dir.rglob("*.md") if p.is_file())
    wiki_dir = workspace / "wiki"
    if wiki_dir.is_dir():
        out.extend(p for p in wiki_dir.rglob("*.md") if p.is_file())
    return sorted(out, key=lambda p: p.stat().st_mtime, reverse=True)[:MAX_CANDIDATE_FILES]


def find_heading(lines: list[str], idx: int) -> str:
    """Walk backward from line index to find the nearest preceding markdown heading."""
    for i in range(idx, -1, -1):
        m = HEADING_RE.match(lines[i])
        if m:
            return m.group(1).strip()
    return ""


def layer_score(workspace: Path, p: Path) -> int:
    """Higher score = preferred. journal/today > curated docs > wiki."""
    try:
        rel = p.relative_to(workspace)
    except ValueError:
        return 0
    parts = rel.parts
    today = date.today().isoformat()
    yday = (date.today() - timedelta(days=1)).isoformat()
    if len(parts) >= 3 and parts[0] == "docs" and parts[1] == "journal":
        if today in parts[-1]:
            return 100
        if yday in parts[-1]:
            return 80
        return 50
    if len(parts) >= 2 and parts[0] == "docs":
        return 60
    if len(parts) >= 2 and parts[0] == "wiki":
        return 30
    return 10


def jaccard_words(a: str, b: str) -> float:
    sa = set(re.findall(r"\w{4,}", a.lower()))
    sb = set(re.findall(r"\w{4,}", b.lower()))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    prompt = (data.get("prompt") or data.get("user_prompt") or "")[:MAX_PROMPT_CHARS]
    if not prompt.strip():
        return 0

    workspace = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    candidates = collect_candidates(workspace)
    if not candidates:
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

    # Collect all candidate hits with metadata for MMR scoring
    raw_hits: list[tuple[Path, list[tuple[int, str, str]], int]] = []
    for f in candidates:
        try:
            text = f.read_text(errors="ignore")
        except Exception:
            continue
        lines = text.splitlines()
        matched: list[tuple[int, str, str]] = []
        for idx, ln in enumerate(lines):
            ln_stripped = ln.strip()
            if pattern.search(ln_stripped):
                heading = find_heading(lines, idx)
                matched.append((idx, heading, ln_stripped))
        if matched:
            raw_hits.append((f, matched[:MAX_LINES_PER_FILE * 2], layer_score(workspace, f)))

    if not raw_hits:
        return 0

    # MMR-style selection: pick highest-scored file first, then files with low
    # word-overlap to already-selected files (diversity).
    raw_hits.sort(key=lambda h: -h[2])
    selected: list[tuple[Path, list[tuple[int, str, str]]]] = []
    selected_text: list[str] = []
    for f, matches, score in raw_hits:
        joined = " ".join(m[2] for m in matches)
        if selected_text:
            max_overlap = max(jaccard_words(joined, s) for s in selected_text)
            if max_overlap > 0.6:
                continue  # too redundant with already selected
        selected.append((f, matches[:MAX_LINES_PER_FILE]))
        selected_text.append(joined)
        if len(selected) >= MAX_FILES:
            break

    if not selected:
        return 0

    parts = ["[openclaw-bridge:recall] Có thể relevant (contextual+MMR):"]
    for f, matches in selected:
        rel = f.relative_to(workspace)
        parts.append(f"\n--- {rel} ---")
        for _idx, heading, line in matches:
            prefix = f"§ {heading} | " if heading else ""
            parts.append(f"{prefix}{line}")
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
