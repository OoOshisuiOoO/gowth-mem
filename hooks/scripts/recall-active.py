#!/usr/bin/env python3
"""UserPromptSubmit hook: hybrid recall (vector + FTS5 → grep fallback).

v0.6 additions over v0.5:
- **Vector + FTS5 hybrid** via SQLite + sqlite-vec when `.gowth-mem/index.db` exists.
  Uses RRF (Reciprocal Rank Fusion) k=60 to merge BM25 and vector ranks.
  Graceful fallback: if sqlite-vec / embedding key missing, FTS5 only.
  Graceful fallback: if FTS5 / index missing, full v0.5 grep path.

Preserved v0.5:
- Contextual heading prefix (§ heading | line)
- MMR-style word-overlap penalty
- Tier-based file score
- Temporal facts skip (`(superseded)`, expired `valid_until:`)
- SM-2-lite spaced resurfacing in `.gowth-mem/state.json`

Decision tree per prompt:
  1. Try vector hybrid (sqlite-vec + embedding) → if works, use it
  2. Try FTS5-only on existing index → if index has BM25, use it
  3. Fall back to grep walk over docs/** and wiki/**
  All paths apply the same temporal filter, MMR diversity, tier scoring, SRS bump.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import struct
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

# Optional vector deps
try:
    import sqlite_vec  # type: ignore
    HAS_VEC = True
except ImportError:
    HAS_VEC = False

sys.path.insert(0, str(Path(__file__).parent))
try:
    from _embed import embed_one  # type: ignore
    HAS_EMBED = True
except ImportError:
    HAS_EMBED = False
from _paths import docs_root, resolve_root  # type: ignore

MAX_KEYWORDS = 8
MAX_FILES = 3
MAX_LINES_PER_FILE = 3
MAX_PROMPT_CHARS = 1500
MAX_CANDIDATE_FILES = 80
SRS_RESURFACE_DAYS = 7
SRS_RESURFACE_PROB = 4
RRF_K = 60
INDEX_TOP_K = 30

HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
SUPERSEDED_RE = re.compile(r"\(superseded\b", re.IGNORECASE)
VALID_UNTIL_RE = re.compile(r"valid[_-]?until:\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE)


def collect_candidates(workspace: Path) -> list[Path]:
    out: list[Path] = []
    docs = docs_root(workspace)
    if docs.is_dir():
        out.extend(p for p in docs.rglob("*.md") if p.is_file())
    wiki = workspace / "wiki"
    if wiki.is_dir():
        out.extend(p for p in wiki.rglob("*.md") if p.is_file())
    return sorted(out, key=lambda p: p.stat().st_mtime, reverse=True)[:MAX_CANDIDATE_FILES]


def find_heading(lines: list[str], idx: int) -> str:
    for i in range(idx, -1, -1):
        m = HEADING_RE.match(lines[i])
        if m:
            return m.group(1).strip()
    return ""


def line_is_temporal_invalid(line: str, today_iso: str) -> bool:
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
    # Strip leading ".gowth-mem" if v1.0 layout, so logic is unchanged
    if parts and parts[0] == ".gowth-mem":
        parts = parts[1:]
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
        pass


def serialize_vec(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def index_recall(workspace: Path, prompt: str, kws: list[str]) -> Optional[list[tuple[str, str, str]]]:
    """Try SQLite FTS5 + (optional) vector hybrid. Return None if index missing.

    Returns list of (path, heading, content_snippet) ranked by RRF.
    """
    db_path = workspace / ".gowth-mem" / "index.db"
    if not db_path.is_file():
        return None
    try:
        db = sqlite3.connect(db_path)
    except sqlite3.Error:
        return None
    try:
        # Verify FTS5 exists in this index
        try:
            db.execute("SELECT 1 FROM chunks_fts LIMIT 1")
        except sqlite3.OperationalError:
            return None

        # FTS5 BM25 query
        fts_q = " OR ".join(f'"{k}"' for k in kws)
        bm25_rows = db.execute(
            "SELECT c.id, c.path, c.heading, c.content, bm25(chunks_fts) "
            "FROM chunks_fts JOIN chunks c ON chunks_fts.rowid = c.id "
            "WHERE chunks_fts MATCH ? "
            "ORDER BY bm25(chunks_fts) "
            "LIMIT ?",
            (fts_q, INDEX_TOP_K),
        ).fetchall()

        # Optional vector path
        vec_rows: list[tuple] = []
        if HAS_VEC and HAS_EMBED:
            try:
                # Check chunks_vec exists
                db.execute("SELECT 1 FROM chunks_vec LIMIT 1")
                sqlite_vec.load(db)  # type: ignore
                qvec = embed_one(prompt)
                if qvec is not None:
                    vec_rows = db.execute(
                        "SELECT c.id, c.path, c.heading, c.content, distance "
                        "FROM chunks_vec v JOIN chunks c ON v.id = c.id "
                        "WHERE v.embedding MATCH ? AND k = ? "
                        "ORDER BY distance",
                        (serialize_vec(qvec), INDEX_TOP_K),
                    ).fetchall()
            except (sqlite3.OperationalError, AttributeError):
                vec_rows = []

        # RRF fusion
        scores: dict[int, float] = {}
        meta: dict[int, tuple[str, str, str]] = {}
        for rank, row in enumerate(bm25_rows):
            cid = row[0]
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
            meta[cid] = (row[1], row[2] or "", row[3])
        for rank, row in enumerate(vec_rows):
            cid = row[0]
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
            meta[cid] = (row[1], row[2] or "", row[3])

        if not scores:
            return []

        ranked = sorted(scores.items(), key=lambda x: -x[1])
        out: list[tuple[str, str, str]] = []
        for cid, _s in ranked:
            path, heading, content = meta[cid]
            out.append((path, heading, content))
        return out
    finally:
        db.close()


def grep_recall(workspace: Path, candidates: list[Path], pattern: re.Pattern, today_iso: str
                ) -> list[tuple[Path, list[tuple[int, str, str]], int]]:
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
    return raw_hits


def find_due_resurface(workspace, state, excluded_paths, candidates, pattern, today_iso):
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
    eligible.sort(key=lambda x: x[1])
    for f, _ in eligible:
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

    # Try index path first
    index_results = index_recall(workspace, prompt, kws)
    backend_label = "grep"
    raw_hits: list[tuple[Path, list[tuple[int, str, str]], int]] = []

    if index_results is not None and len(index_results) > 0:
        # Convert (path, heading, content) into per-file matches
        backend_label = "index" + ("+vec" if HAS_VEC and HAS_EMBED else "+fts5")
        per_file: dict[str, list[tuple[int, str, str]]] = {}
        for path, heading, content in index_results:
            for idx, ln in enumerate(content.splitlines()):
                ln_stripped = ln.strip()
                if not pattern.search(ln_stripped):
                    continue
                if line_is_temporal_invalid(ln_stripped, today_iso):
                    continue
                per_file.setdefault(path, []).append((idx, heading, ln_stripped))
                if len(per_file[path]) >= MAX_LINES_PER_FILE * 2:
                    break
        for path, matches in per_file.items():
            full = workspace / path
            if not full.is_file():
                continue
            raw_hits.append((full, matches[:MAX_LINES_PER_FILE * 2], layer_score(workspace, full)))

    if not raw_hits:
        # Fallback to grep
        backend_label = "grep"
        raw_hits = grep_recall(workspace, candidates, pattern, today_iso)

    if not raw_hits:
        return 0

    raw_hits.sort(key=lambda h: (-h[2], -h[0].stat().st_mtime))
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

    state = load_srs_state(workspace)
    prob_seed = hashlib.sha1((today_iso + prompt).encode()).digest()[0]
    resurfaced = None
    if prob_seed % SRS_RESURFACE_PROB == 0:
        excluded = {str(f.relative_to(workspace)) for f, _ in selected}
        resurfaced = find_due_resurface(workspace, state, excluded, candidates, pattern, today_iso)

    parts = [f"[openclaw-bridge:recall:{backend_label}] Có thể relevant:"]
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
