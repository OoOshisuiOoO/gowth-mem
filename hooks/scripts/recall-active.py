#!/usr/bin/env python3
"""UserPromptSubmit hook (v2.2): hybrid recall scoped to the active workspace.

Sources: workspaces/<active>/{topics,docs}/**/*.md + shared/*.md
  (excludes journal/, _MAP.md, and shared/skills/ from primary recall)

Honors `recall.cross_workspace`: when true, search every workspace.

Decision tree per prompt:
  1. Try vector hybrid (sqlite-vec + embedding) → if works, use it
  2. Try FTS5-only on existing index → use BM25
  3. Fall back to grep walk over candidates
  All paths apply temporal filter, MMR diversity, tier scoring, SRS bump.
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
from _atomic import atomic_write  # type: ignore
from _home import (  # type: ignore
    RESERVED_SUBDIRS,
    active_workspace,
    docs_dir,
    gowth_home,
    index_db,
    iter_topic_files,
    list_workspaces,
    read_settings,
    shared_dir,
    state_path,
)
from _lock import file_lock  # type: ignore
from _wikilink import resolve as wikilink_resolve  # type: ignore

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
WIKILINK_RE = re.compile(r"\[\[([^\[\]\|#]+)(#[^\[\]\|]+)?(\|[^\[\]]+)?\]\]")


def _allowed_workspaces(settings: dict) -> list[str] | None:
    """Return None to mean "all", else list of allowed ws names (active + shared virtual)."""
    cross = settings.get("recall", {}).get("cross_workspace", False)
    if cross:
        return None  # no scoping
    return [active_workspace()]


def collect_candidates(allowed_ws: list[str] | None) -> list[Path]:
    """Walk the candidate corpus, scoped if `allowed_ws` is given."""
    out: list[Path] = []
    sd = shared_dir()
    if sd.is_dir():
        # Top-level shared files only — skip subfolders like skills/
        out.extend(p for p in sd.glob("*.md") if p.is_file())

    targets = list_workspaces() if allowed_ws is None else allowed_ws
    for ws in targets:
        # Topic files (workspace root + non-reserved subdirs)
        out.extend(iter_topic_files(ws))
        # Cross-cutting docs (handoff/exp/ref/tools/files)
        dd = docs_dir(ws)
        if dd.is_dir():
            out.extend(p for p in dd.glob("*.md") if p.is_file() and p.name != "_MAP.md")

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


def layer_score(p: Path) -> int:
    """Tier score by path layout (v2.2)."""
    gh = gowth_home()
    try:
        rel = p.relative_to(gh)
    except ValueError:
        return 10
    parts = rel.parts
    if not parts:
        return 10
    if parts[0] == "shared":
        if len(parts) > 1 and parts[1] == "skills":
            return 40
        return 60  # secrets/tools/files
    if parts[0] == "workspaces" and len(parts) >= 3:
        sub = parts[2]
        if sub == "docs":
            return 60
        if sub == "skills":
            return 40
        if sub == "journal":
            today = date.today().isoformat()
            yday = (date.today() - timedelta(days=1)).isoformat()
            if today in p.name:
                return 100
            if yday in p.name:
                return 70
            return 30
        # v2.4+: workspace root IS the topic tree. Anything not a reserved
        # subdir is a topic file (landing, sub-aspect, lessons.md, or domain MOC).
        if sub not in RESERVED_SUBDIRS:
            return 80
    return 10


def jaccard_words(a: str, b: str) -> float:
    sa = set(re.findall(r"\w{4,}", a.lower()))
    sb = set(re.findall(r"\w{4,}", b.lower()))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def load_srs_state() -> dict:
    p = state_path()
    if not p.is_file():
        return {"version": 2, "files": {}}
    try:
        d = json.loads(p.read_text())
        d.setdefault("files", {})
        return d
    except Exception:
        return {"version": 2, "files": {}}


def save_srs_state(state: dict) -> None:
    try:
        gowth_home().mkdir(parents=True, exist_ok=True)
        atomic_write(state_path(), json.dumps(state, indent=2))
    except Exception:
        pass


def serialize_vec(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _path_in_scope(rel: str, allowed_ws: list[str] | None) -> bool:
    if allowed_ws is None:
        return True
    if rel.startswith("shared/"):
        return True
    if not rel.startswith("workspaces/"):
        return False
    parts = rel.split("/")
    if len(parts) < 2:
        return False
    return parts[1] in allowed_ws


def index_recall(prompt: str, kws: list[str], allowed_ws: list[str] | None
                 ) -> list[tuple[str, str, str]] | None:
    db_path = index_db()
    if not db_path.is_file():
        return None
    try:
        db = sqlite3.connect(db_path)
        db.execute("PRAGMA busy_timeout=5000")
    except sqlite3.Error:
        return None
    try:
        try:
            db.execute("SELECT 1 FROM chunks_fts LIMIT 1")
        except sqlite3.OperationalError:
            return None

        fts_q = " OR ".join(f'"{k}"' for k in kws)
        bm25_rows = db.execute(
            "SELECT c.id, c.path, c.heading, c.content, bm25(chunks_fts) "
            "FROM chunks_fts JOIN chunks c ON chunks_fts.rowid = c.id "
            "WHERE chunks_fts MATCH ? "
            "ORDER BY bm25(chunks_fts) "
            "LIMIT ?",
            (fts_q, INDEX_TOP_K),
        ).fetchall()

        vec_rows: list[tuple] = []
        if HAS_VEC and HAS_EMBED:
            try:
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

        scores: dict[int, float] = {}
        meta: dict[int, tuple[str, str, str]] = {}
        for rank, row in enumerate(bm25_rows):
            cid, path = row[0], row[1]
            if not _path_in_scope(path, allowed_ws):
                continue
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
            meta[cid] = (path, row[2] or "", row[3])
        for rank, row in enumerate(vec_rows):
            cid, path = row[0], row[1]
            if not _path_in_scope(path, allowed_ws):
                continue
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
            meta[cid] = (path, row[2] or "", row[3])

        if not scores:
            return []

        ranked = sorted(scores.items(), key=lambda x: -x[1])
        return [meta[cid] for cid, _ in ranked]
    finally:
        db.close()


def grep_recall(candidates: list[Path], pattern: re.Pattern, today_iso: str
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
            raw_hits.append((f, matched[:MAX_LINES_PER_FILE * 2], layer_score(f)))
    return raw_hits


def find_due_resurface(state, excluded_paths, candidates, pattern, today_iso):
    gh = gowth_home()
    now = time.time()
    threshold = now - SRS_RESURFACE_DAYS * 86400
    files_meta = state.get("files", {})
    eligible: list[tuple[Path, float]] = []
    for f in candidates:
        try:
            rel = str(f.relative_to(gh))
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


def follow_wikilinks(top_file: Path, current_ws: str, pattern: re.Pattern, today_iso: str
                     ) -> tuple[Path, list[tuple[int, str, str]]] | None:
    """Follow first wikilink on the top hit (one hop). Cross-ws OK if explicit."""
    try:
        text = top_file.read_text(errors="ignore")
    except Exception:
        return None
    m = WIKILINK_RE.search(text)
    if not m:
        return None
    target = wikilink_resolve(m.group(1), current_ws=current_ws)
    if target is None or target == top_file or not target.is_file():
        return None
    try:
        ttext = target.read_text(errors="ignore")
    except Exception:
        return None
    lines = ttext.splitlines()
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
    if not matched:
        return None
    return target, matched


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    prompt = (data.get("prompt") or data.get("user_prompt") or "")[:MAX_PROMPT_CHARS]
    if not prompt.strip():
        return 0

    settings = read_settings()
    allowed_ws = _allowed_workspaces(settings)
    current_ws = active_workspace()

    candidates = collect_candidates(allowed_ws)
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
    gh = gowth_home()

    index_results = index_recall(prompt, kws, allowed_ws)
    backend_label = "grep"
    raw_hits: list[tuple[Path, list[tuple[int, str, str]], int]] = []

    if index_results is not None and len(index_results) > 0:
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
            full = gh / path
            if not full.is_file():
                full = Path(path)
                if not full.is_file():
                    continue
            raw_hits.append((full, matches[:MAX_LINES_PER_FILE * 2], layer_score(full)))

    if not raw_hits:
        backend_label = "grep"
        raw_hits = grep_recall(candidates, pattern, today_iso)

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

    wikilink_extra: tuple[Path, list[tuple[int, str, str]]] | None = None
    if settings.get("recall", {}).get("wikilink_follow", True) and selected:
        wikilink_extra = follow_wikilinks(selected[0][0], current_ws, pattern, today_iso)

    state = load_srs_state()
    prob_seed = hashlib.sha1((today_iso + prompt).encode()).digest()[0]
    resurfaced = None
    if prob_seed % SRS_RESURFACE_PROB == 0:
        excluded = set()
        for f, _ in selected:
            try:
                excluded.add(str(f.relative_to(gh)))
            except ValueError:
                pass
        resurfaced = find_due_resurface(state, excluded, candidates, pattern, today_iso)

    parts = [f"[gowth-mem:recall:{backend_label} ws={current_ws}] Có thể relevant:"]
    for f, matches in selected:
        try:
            rel = f.relative_to(gh)
            label = f"~/.gowth-mem/{rel}"
        except ValueError:
            label = str(f)
        parts.append(f"\n--- {label} ---")
        for _idx, heading, line in matches:
            prefix = f"§ {heading} | " if heading else ""
            parts.append(f"{prefix}{line}")
    if wikilink_extra:
        wf, wmatches = wikilink_extra
        try:
            rel = wf.relative_to(gh)
            label = f"~/.gowth-mem/{rel}"
        except ValueError:
            label = str(wf)
        parts.append(f"\n--- {label} (wikilink follow) ---")
        for _idx, heading, line in wmatches:
            prefix = f"§ {heading} | " if heading else ""
            parts.append(f"{prefix}{line}")
    if resurfaced:
        rf, rmatches = resurfaced
        try:
            rel = rf.relative_to(gh)
            label = f"~/.gowth-mem/{rel}"
        except ValueError:
            label = str(rf)
        parts.append(f"\n--- {label} (resurfaced — chưa seen ≥{SRS_RESURFACE_DAYS}d) ---")
        for _idx, heading, line in rmatches:
            prefix = f"§ {heading} | " if heading else ""
            parts.append(f"{prefix}{line}")

    try:
        with file_lock("state", timeout=5.0):
            state = load_srs_state()
            now = time.time()
            files_meta = state.setdefault("files", {})
            surfaced_paths = []
            for f, _ in selected:
                try:
                    surfaced_paths.append(str(f.relative_to(gh)))
                except ValueError:
                    pass
            if resurfaced:
                try:
                    surfaced_paths.append(str(resurfaced[0].relative_to(gh)))
                except ValueError:
                    pass
            for rel in surfaced_paths:
                rec = files_meta.setdefault(rel, {})
                rec["last_seen"] = now
                rec["count"] = rec.get("count", 0) + 1
            save_srs_state(state)
    except TimeoutError:
        pass

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
