#!/usr/bin/env python3
"""Staged consolidation pipeline (v2.9): Light -> REM -> Deep.

Adapted from OpenClaw's dreaming architecture. Provides signal-ranked
consolidation data to enrich auto-journal's distill instruction.

- Light: gather candidate files from state.json activity data
- REM: group candidates by keyword theme
- Deep: rank by 6 weighted signals, output promotion/prune recommendations

Pure stdlib Python 3.9+. No pip deps.
"""
from __future__ import annotations

import json
import math
import re
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import atomic_write  # type: ignore
from _home import (  # type: ignore
    active_workspace,
    docs_dir,
    gowth_home,
    iter_topic_files,
    state_path,
)
from _lock import file_lock  # type: ignore

# Signal weights (OpenClaw deep-phase ranking)
W_FREQUENCY = 0.24
W_RELEVANCE = 0.30
W_DIVERSITY = 0.15
W_RECENCY = 0.15
W_CONSOLIDATION = 0.10
W_RICHNESS = 0.06

TYPE_RE = re.compile(r"\[(decision|exp|ref|tool|reflection|skill-ref|secret-ref)\]")
MIN_RECALL_COUNT = 2


def _load_state() -> dict:
    p = state_path()
    if not p.is_file():
        return {"version": 2, "files": {}, "session": {}}
    try:
        d = json.loads(p.read_text())
        d.setdefault("files", {})
        d.setdefault("session", {})
        return d
    except Exception:
        return {"version": 2, "files": {}, "session": {}}


def count_typed_entries(path: Path) -> int:
    try:
        text = path.read_text(errors="ignore")
    except Exception:
        return 0
    return sum(1 for line in text.splitlines() if TYPE_RE.search(line))


def extract_keywords(path: Path) -> set[str]:
    try:
        text = path.read_text(errors="ignore")
    except Exception:
        return set()
    return set(re.findall(r"\b\w{5,}\b", text.lower()))


# ── Signal computation ──────────────────────────────────────────────────


def compute_signals(rel: str, file_meta: dict, path: Path, now: float) -> dict:
    count = file_meta.get("count", 0)
    last_seen = file_meta.get("last_seen", 0)
    query_hashes = file_meta.get("query_hashes", [])
    days_seen = file_meta.get("days_seen", [])

    frequency = count
    days_active = len(days_seen) if days_seen else 1
    relevance = count / max(days_active, 1)
    diversity = len(set(query_hashes))

    if last_seen > 0:
        age_days = (now - last_seen) / 86400
        recency = math.exp(-0.693 * age_days / 7)
    else:
        try:
            mtime = path.stat().st_mtime
            age_days = (now - mtime) / 86400
            recency = math.exp(-0.693 * age_days / 7)
        except Exception:
            recency = 0.0

    consolidation = len(set(days_seen))
    richness = count_typed_entries(path)

    return {
        "frequency": frequency,
        "relevance": relevance,
        "diversity": diversity,
        "recency": recency,
        "consolidation": consolidation,
        "richness": richness,
    }


def normalize_signals(all_signals: list[dict]) -> list[dict]:
    if not all_signals:
        return []
    keys = ["frequency", "relevance", "diversity", "recency", "consolidation", "richness"]
    maxvals = {}
    for k in keys:
        vals = [s[k] for s in all_signals]
        maxvals[k] = max(vals) if vals and max(vals) > 0 else 1.0
    return [{k: s[k] / maxvals[k] for k in keys} for s in all_signals]


def weighted_score(signals: dict) -> float:
    return (
        W_FREQUENCY * signals.get("frequency", 0)
        + W_RELEVANCE * signals.get("relevance", 0)
        + W_DIVERSITY * signals.get("diversity", 0)
        + W_RECENCY * signals.get("recency", 0)
        + W_CONSOLIDATION * signals.get("consolidation", 0)
        + W_RICHNESS * signals.get("richness", 0)
    )


# ── Phases ───────────────────────────────────────────────────────────────


def light_phase(state: dict) -> list[tuple[str, dict]]:
    """Gather and dedup candidate files from state activity data."""
    gh = gowth_home()
    files_meta = state.get("files", {})
    now = time.time()
    candidates = []
    for rel, meta in files_meta.items():
        if meta.get("count", 0) < MIN_RECALL_COUNT:
            continue
        path = gh / rel
        if not path.is_file():
            continue
        signals = compute_signals(rel, meta, path, now)
        candidates.append((rel, signals))
    return candidates


def rem_phase(candidates: list[tuple[str, dict]]) -> dict[str, list[str]]:
    """Group candidates by keyword theme (Jaccard clustering)."""
    gh = gowth_home()
    themes: dict[str, list[str]] = defaultdict(list)
    file_keywords: dict[str, set[str]] = {}
    for rel, _ in candidates:
        file_keywords[rel] = extract_keywords(gh / rel)

    assigned: set[str] = set()
    for rel, kws in file_keywords.items():
        if rel in assigned:
            continue
        cluster = [rel]
        assigned.add(rel)
        for other_rel, other_kws in file_keywords.items():
            if other_rel in assigned:
                continue
            if kws and other_kws:
                overlap = len(kws & other_kws) / len(kws | other_kws)
                if overlap > 0.3:
                    cluster.append(other_rel)
                    assigned.add(other_rel)
        all_kws: set[str] = set()
        for r in cluster:
            all_kws |= file_keywords.get(r, set())
        label = "-".join(sorted(all_kws)[:3]) or "misc"
        themes[label] = cluster

    return dict(themes)


def deep_phase(candidates: list[tuple[str, dict]]) -> dict:
    """Rank candidates by 6 weighted signals; split into promote/maintain/prune."""
    if not candidates:
        return {"promote": [], "maintain": [], "prune_candidates": []}

    all_signals = [s for _, s in candidates]
    normalized = normalize_signals(all_signals)

    scored = []
    for (rel, raw), norm in zip(candidates, normalized):
        score = weighted_score(norm)
        scored.append({
            "path": rel,
            "score": round(score, 3),
            "raw": raw,
            "normalized": {k: round(v, 3) for k, v in norm.items()},
        })
    scored.sort(key=lambda x: -x["score"])

    return {
        "promote": [s for s in scored if s["score"] >= 0.6],
        "maintain": [s for s in scored if 0.3 <= s["score"] < 0.6],
        "prune_candidates": [s for s in scored if s["score"] < 0.3],
    }


# ── Pipeline ─────────────────────────────────────────────────────────────


def run_pipeline() -> dict:
    try:
        with file_lock("state", timeout=5.0):
            state = _load_state()
    except TimeoutError:
        state = _load_state()

    candidates = light_phase(state)
    if not candidates:
        return {"status": "no_candidates", "candidates": 0}

    themes = rem_phase(candidates)
    rankings = deep_phase(candidates)

    try:
        with file_lock("state", timeout=5.0):
            state = _load_state()
            cons = state.setdefault("consolidation", {})
            cons["last_run"] = date.today().isoformat()
            log = cons.setdefault("log", [])
            log.append({
                "date": date.today().isoformat(),
                "candidates": len(candidates),
                "promoted": len(rankings["promote"]),
                "maintained": len(rankings["maintain"]),
                "prune_candidates": len(rankings["prune_candidates"]),
            })
            cons["log"] = log[-30:]
            atomic_write(state_path(), json.dumps(state, indent=2))
    except (TimeoutError, Exception):
        pass

    return {
        "status": "completed",
        "candidates": len(candidates),
        "themes": len(themes),
        "rankings": rankings,
        "theme_groups": {k: len(v) for k, v in themes.items()},
    }


def format_for_instruction(result: dict) -> str:
    if result["status"] == "no_candidates":
        return "Consolidation: no candidates with sufficient recall activity."

    rankings = result.get("rankings", {})
    parts = [f"Consolidation pipeline: {result['candidates']} candidates, {result['themes']} themes."]

    if rankings.get("promote"):
        parts.append("\nHigh-signal files (promote key entries to workspace docs):")
        for item in rankings["promote"][:5]:
            r = item["raw"]
            parts.append(
                f"  * {item['path']} (score={item['score']}, "
                f"freq={r['frequency']}, div={r['diversity']}, rich={r['richness']})"
            )

    if rankings.get("prune_candidates"):
        parts.append("\nLow-signal files (candidates for pruning):")
        for item in rankings["prune_candidates"][:5]:
            parts.append(f"  - {item['path']} (score={item['score']})")

    return "\n".join(parts)


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Staged consolidation pipeline")
    ap.add_argument("--json", action="store_true", help="Output raw JSON")
    args = ap.parse_args()

    result = run_pipeline()
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(format_for_instruction(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
