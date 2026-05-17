#!/usr/bin/env python3
"""Token-budget context planner — 4-tier scoring, deterministic.

Combines three signals into a single retrieval score per candidate file:
  1. Lexical match — char-ngram Jaccard between query and file text head.
  2. Tier weight — agentmemory taxonomy (working / episodic / semantic / procedural).
  3. Recency decay — exponential, Ebbinghaus-style 14-day half-life by default.

``plan_context(ws, query, budget_chars, settings=None)`` returns an ordered list
``[(path, snippet, score)]`` greedy-filled until *budget_chars* is exhausted.

Always-include slots (the "stable prefix" for prompt caching):
  - ``shared/AGENTS.md``, ``shared/secrets.md``, ``shared/tools.md``
  - ``workspaces/<ws>/AGENTS.md``, ``workspaces/<ws>/docs/handoff.md``
  - today's journal if it exists

After the stable prefix, remaining budget is filled by tier-weighted score.

No LLM. Pure stdlib. Designed to be opt-in via ``settings.retrieval.use_budget_planner=true``.
"""
from __future__ import annotations

import math
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
from _home import (  # type: ignore
    active_workspace,
    agents_md,
    docs_dir,
    gowth_home,
    journal_dir,
    read_settings,
    secrets_md,
    shared_dir,
    shared_tools_md,
    skills_dir,
    workspace_agents_md,
    workspace_dir,
)
from _lexical import char_ngrams, jaccard  # type: ignore


DEFAULT_TIER_WEIGHTS = {
    "working": 1.0,
    "episodic": 0.7,
    "semantic": 0.8,
    "procedural": 0.6,
}
DEFAULT_HALF_LIFE_DAYS = 14
DEFAULT_HEAD_CHARS = 4000


def _recency_decay(mtime: float, now: float, half_life_days: float) -> float:
    if mtime <= 0 or half_life_days <= 0:
        return 0.0
    age_days = max(0.0, (now - mtime) / 86400.0)
    return math.exp(-math.log(2) * age_days / half_life_days)


def _classify_tier(path: Path, ws_root: Path) -> str:
    """Map a path to a 4-tier label (agentmemory taxonomy)."""
    try:
        rel = path.relative_to(ws_root)
    except ValueError:
        return "semantic"
    parts = rel.parts
    if not parts:
        return "semantic"
    head = parts[0]
    if head == "journal":
        return "working" if path.name == f"{date.today().isoformat()}.md" else "episodic"
    if head == "skills":
        return "procedural"
    if head == "docs":
        if path.name == "handoff.md":
            return "working"
        return "semantic"
    if head == "research":
        return "semantic"
    return "semantic"  # topic folders


def _read_head(path: Path, max_chars: int) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            return f.read(max_chars)
    except OSError:
        return ""


def _iter_candidates(ws: str) -> list[Path]:
    """Walk shared/ + workspaces/<ws>/* for indexable markdown."""
    out: list[Path] = []
    sd = shared_dir()
    if sd.is_dir():
        out.extend(p for p in sd.glob("*.md") if p.is_file())
        sk = sd / "skills"
        if sk.is_dir():
            out.extend(p for p in sk.glob("*.md") if p.is_file())
    wd = workspace_dir(ws)
    if wd.is_dir():
        for p in wd.rglob("*.md"):
            if p.is_file():
                out.append(p)
    return out


def _tier_weights(settings: dict) -> dict[str, float]:
    cb = settings.get("context_budget", {}) if isinstance(settings, dict) else {}
    tw = cb.get("tier_weights") if isinstance(cb, dict) else None
    if isinstance(tw, dict):
        merged = dict(DEFAULT_TIER_WEIGHTS)
        for k, v in tw.items():
            try:
                merged[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
        return merged
    return dict(DEFAULT_TIER_WEIGHTS)


def _half_life(settings: dict) -> float:
    cb = settings.get("context_budget", {}) if isinstance(settings, dict) else {}
    try:
        return float(cb.get("recency_half_life_days", DEFAULT_HALF_LIFE_DAYS))
    except (TypeError, ValueError):
        return DEFAULT_HALF_LIFE_DAYS


def _stable_prefix_paths(ws: str) -> list[Path]:
    paths = [
        agents_md(),
        secrets_md(),
        shared_tools_md(),
        workspace_agents_md(ws),
        docs_dir(ws) / "handoff.md",
    ]
    today_journal = journal_dir(ws) / f"{date.today().isoformat()}.md"
    if today_journal.is_file():
        paths.append(today_journal)
    return paths


def plan_context(
    ws: Optional[str] = None,
    query: str = "",
    budget_chars: int = 15_000,
    settings: Optional[dict] = None,
    head_chars: int = DEFAULT_HEAD_CHARS,
) -> list[tuple[Path, str, float]]:
    """Plan a context bundle within *budget_chars* total.

    Returns ``[(path, snippet, score)]`` in load order: stable prefix first,
    then tier-weighted scored candidates. Snippet is truncated to fit the
    remaining budget per file (no per-file hard cap beyond budget).
    """
    settings = settings or read_settings()
    ws = ws or active_workspace()
    weights = _tier_weights(settings)
    half_life = _half_life(settings)
    now = time.time()
    ws_root = workspace_dir(ws).resolve()

    plan: list[tuple[Path, str, float]] = []
    used = 0
    seen: set[Path] = set()

    for p in _stable_prefix_paths(ws):
        if not p.is_file() or p in seen:
            continue
        room = budget_chars - used
        if room <= 200:
            break
        text = _read_head(p, room)
        if not text.strip():
            continue
        plan.append((p, text, 1.0))
        used += len(text)
        seen.add(p)

    if used >= budget_chars - 200:
        return plan

    q_ngrams = char_ngrams(query) if query.strip() else set()
    scored: list[tuple[float, Path, str]] = []
    for p in _iter_candidates(ws):
        if p in seen:
            continue
        head = _read_head(p, head_chars)
        if not head.strip():
            continue
        tier = _classify_tier(p, ws_root)
        weight = weights.get(tier, 0.5)
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = 0.0
        recency = _recency_decay(mtime, now, half_life)
        if q_ngrams:
            lex = jaccard(q_ngrams, char_ngrams(head))
        else:
            lex = 0.5  # neutral when no query
        score = lex * weight * (0.5 + 0.5 * recency)
        if score <= 0:
            continue
        scored.append((score, p, head))

    scored.sort(key=lambda t: -t[0])
    for score, p, head in scored:
        room = budget_chars - used
        if room <= 200:
            break
        snippet = head if len(head) <= room else head[:room]
        plan.append((p, snippet, round(score, 4)))
        used += len(snippet)
        seen.add(p)

    return plan


def main() -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Token-budget context planner")
    ap.add_argument("--ws", default=None)
    ap.add_argument("--query", default="")
    ap.add_argument("--budget", type=int, default=15_000)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    plan = plan_context(ws=args.ws, query=args.query, budget_chars=args.budget)
    if args.json:
        gh = gowth_home()
        out = []
        for p, snippet, score in plan:
            try:
                rel = str(p.relative_to(gh))
            except ValueError:
                rel = str(p)
            out.append({"path": rel, "chars": len(snippet), "score": score})
        print(json.dumps({"plan": out, "total_chars": sum(len(s) for _, s, _ in plan)}, indent=2))
    else:
        total = sum(len(s) for _, s, _ in plan)
        for p, snippet, score in plan:
            print(f"{score:.3f}\t{len(snippet):>6}c\t{p}")
        print(f"\n[total: {total} chars across {len(plan)} files]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
