#!/usr/bin/env python3
"""UserPromptSubmit hook: contextual recall with MMR + temporal facts + spaced resurfacing.

v0.5 additions over v0.4:
- **Temporal facts** (Zep pattern): skip match lines tagged `(superseded)` or
  with `valid_until: YYYY-MM-DD` in the past. Stale facts stop polluting recall.
- **SM-2-lite spaced resurfacing** (Anki / forgetting-curve): track last_seen
  per file in `.gowth-mem/state.json`. With deterministic probability
  (hash of today + prompt mod 4 == 0, ≈25%), append 1 entry from a file that
  hasn't been surfaced in ≥7 days. Keeps old knowledge from drifting out.

Existing v0.4 behavior preserved:
- Contextual heading prefix (§ heading | line)
- MMR-style word-overlap penalty (Jaccard >0.6 skipped)
- Tier-based file score (journal/today > docs/* > wiki/*)

Scans:
  - docs/**/*.md   (gowth-mem: journal + curated)
  - wiki/**/*.md   (claude-obsidian, if vault exists)

Silent if nothing matches.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

MAX_KEYWORDS = 8
MAX_FILES = 3
MAX_LINES_PER_FILE = 3
MAX_PROMPT_CHARS = 1500
MAX_CANDIDATE_FILES = 80
SRS_RESURFACE_DAYS = 7
SRS_RESURFACE_PROB = 4  # hash mod N == 0 → resurface

HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
SUPERSEDED_RE = re.compile(r"\(superseded\b", re.IGNORECASE)
VALID_UNTIL_RE = re.compile(r"valid[_-]?until:\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE)


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
    for i in range(idx, -1, -1):
        m = HEADING_RE.match(lines[i])
        if m:
            return m.group(1).strip()
    return ""


def line_is_temporal_invalid(line: str, today_iso: str) -> bool:
    """True if line is superseded or expired."""
    if SUPERSEDED_RE.search(line):
        return True
    m = VALID_UNTIL_RE.search(line)
    if m and m.group(1) < today_iso:
        return True
    return False


def layer_score(workspace: Path, p: Path) -> int:
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


def load_srs_state(workspace: Path) -> dict:
    p = workspace / ".gowth-mem" / "state.json"
    if not p.is_file():
        return {"version": 1, "files": {}}
    try:
        d = json.loads(p.read_text())
        d.setdefault("files", {})
        return d
    except Exception:
        return {"version": 1, "files": {}}


def save_srs_state(workspace: Path, state: dict) -> None:
    p = workspace / ".gowth-mem"
    try:
        p.mkdir(exist_ok=True)
        (p / "state.json").write_text(json.dumps(state, indent=2))
    except Exception:
        pass  # tracker is non-critical


def find_due_resurface(
    workspace: Path,
    state: dict,
    excluded_paths: set[str],
    candidates: list[Path],
    pattern: re.Pattern,
    today_iso: str,
) -> tuple[Path, list[tuple[int, str, str]]] | None:
    """Pick 1 file not seen in N days, with at least one matched line."""
    now = time.time()
    threshold = now - SRS_RESURFACE_DAYS * 86400
    files_meta = state.get("files", {})
    eligible: list[tuple[Path, float]] = []
    for f in candidates:
        try:
            rel = str(f.relative_to(workspace))
        except ValueError:
            continue
        if rel in excluded_paths:
            continue
        last_seen = files_meta.get(rel, {}).get("last_seen", 0)
        if last_seen >= threshold:
            continue
        eligible.append((f, last_seen))
    eligible.sort(key=lambda x: x[1])  # oldest first
    for f, _last in eligible:
        try:
            text = f.read_text(errors="ignore")
        except Exception:
            continue
        lines = text.splitlines()
        matched: list[tuple[int, str, str]] = []
        for idx, ln in enumerate(lines):
            ln_stripped = ln.strip()
            if not pattern.search(ln_stripped):
                continue
            if line_is_temporal_invalid(ln_stripped, today_iso):
                continue
            heading = find_heading(lines, idx)
            matched.append((idx, heading, ln_stripped))
            if len(matched) >= MAX_LINES_PER_FILE:
                break
        if matched:
            return f, matched
    return None


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
    today_iso = date.today().isoformat()

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
            if not pattern.search(ln_stripped):
                continue
            if line_is_temporal_invalid(ln_stripped, today_iso):
                continue
            heading = find_heading(lines, idx)
            matched.append((idx, heading, ln_stripped))
        if matched:
            raw_hits.append((f, matched[:MAX_LINES_PER_FILE * 2], layer_score(workspace, f)))

    if not raw_hits:
        return 0

    raw_hits.sort(key=lambda h: -h[2])
    selected: list[tuple[Path, list[tuple[int, str, str]]]] = []
    selected_text: list[str] = []
    for f, matches, _score in raw_hits:
        joined = " ".join(m[2] for m in matches)
        if selected_text:
            max_overlap = max(jaccard_words(joined, s) for s in selected_text)
            if max_overlap > 0.6:
                continue
        selected.append((f, matches[:MAX_LINES_PER_FILE]))
        selected_text.append(joined)
        if len(selected) >= MAX_FILES:
            break

    if not selected:
        return 0

    # SM-2-lite spaced resurfacing: with ~25% probability, append 1 stale file.
    state = load_srs_state(workspace)
    prob_seed = hashlib.sha1((today_iso + prompt).encode()).digest()[0]
    resurfaced = None
    if prob_seed % SRS_RESURFACE_PROB == 0:
        excluded = {str(f.relative_to(workspace)) for f, _ in selected}
        resurfaced = find_due_resurface(workspace, state, excluded, candidates, pattern, today_iso)

    parts = ["[openclaw-bridge:recall] Có thể relevant (contextual+MMR+temporal):"]
    for f, matches in selected:
        rel = f.relative_to(workspace)
        parts.append(f"\n--- {rel} ---")
        for _idx, heading, line in matches:
            prefix = f"§ {heading} | " if heading else ""
            parts.append(f"{prefix}{line}")
    if resurfaced:
        rf, rmatches = resurfaced
        rel = rf.relative_to(workspace)
        parts.append(f"\n--- {rel} (resurfaced — chưa seen ≥{SRS_RESURFACE_DAYS}d) ---")
        for _idx, heading, line in rmatches:
            prefix = f"§ {heading} | " if heading else ""
            parts.append(f"{prefix}{line}")

    # Update SRS tracker for surfaced paths.
    now = time.time()
    files_meta = state.setdefault("files", {})
    surfaced_paths = [str(f.relative_to(workspace)) for f, _ in selected]
    if resurfaced:
        surfaced_paths.append(str(resurfaced[0].relative_to(workspace)))
    for rel in surfaced_paths:
        rec = files_meta.setdefault(rel, {})
        rec["last_seen"] = now
        rec["count"] = rec.get("count", 0) + 1
    save_srs_state(workspace, state)

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
