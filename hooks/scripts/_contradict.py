#!/usr/bin/env python3
"""Heuristic contradiction detection — pure stdlib, no LLM.

Scans ``[ref]``, ``[decision]``, ``[tool]`` lines across a workspace looking for
candidate pairs that:
  * share >= ``min_entity_overlap`` content keywords (default 3), AND
  * differ on a negation/polarity marker (``not`` vs presence,
    ``disabled`` vs ``enabled``, ``false`` vs ``true``, ``removed`` vs ``added``,
    ``deprecated`` vs ``recommended``, ``forbidden`` vs ``allowed``).

This is intentionally noisy-but-cheap: emits candidate pairs as warnings;
never auto-mutates files. A human (or AI agent) decides whether to:
  * delete the older entry,
  * add a ``[contradicts: <other>]`` link, or
  * mark one entry with ``valid_until: <date>``.

Used by ``/mem-lint --contradictions``.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).parent))
from _home import (  # type: ignore
    active_workspace,
    gowth_home,
    iter_topic_files,
    journal_dir,
    workspace_dir,
)


# v3.9: include [hypothesis] — an unverified claim contradicting a verified [ref]
# is exactly the signal worth flagging. [goal] is excluded (intent, not a fact claim).
TYPE_RE = re.compile(r"\[(ref|decision|tool|hypothesis)\]")

POLARITY_PAIRS: list[tuple[str, str]] = [
    ("enabled", "disabled"),
    ("enable", "disable"),
    ("true", "false"),
    ("added", "removed"),
    ("add", "remove"),
    ("allowed", "forbidden"),
    ("allow", "forbid"),
    ("recommended", "deprecated"),
    ("supported", "unsupported"),
    ("required", "optional"),
    ("on", "off"),
    ("works", "broken"),
    ("fixed", "broken"),
    ("pass", "fail"),
    ("passing", "failing"),
]

NEGATION_TOKENS = frozenset({"not", "no", "never", "without", "can't", "cannot",
                              "won't", "wont", "isn't", "isnt", "doesn't", "doesnt"})

STOPWORDS = frozenset({
    "this", "that", "with", "from", "have", "been", "were", "they", "them",
    "their", "there", "would", "could", "should", "about", "which", "what",
    "when", "where", "while", "into", "than", "then", "some", "such", "very",
    "just", "only", "your", "you", "for", "the", "and", "but", "not", "all",
    "any", "are", "was", "has", "had", "its", "out", "via", "use", "uses",
    "used", "using", "will", "must", "may", "can", "ref", "tool", "decision",
})

WORD_RE = re.compile(r"\b\w{4,}\b")


def _keywords(text: str) -> set[str]:
    return {w for w in WORD_RE.findall(text.lower()) if w not in STOPWORDS}


def _polarity_signature(text: str) -> set[str]:
    """Return polarity markers present in *text* as a normalised set.

    e.g. ``"foo is enabled"`` → ``{"enabled"}``;
         ``"foo is not enabled"`` → ``{"enabled:neg"}``.
    """
    t = text.lower()
    tokens = re.findall(r"\b[\w']+\b", t)
    neg = any(tok in NEGATION_TOKENS for tok in tokens)
    sig: set[str] = set()
    for a, b in POLARITY_PAIRS:
        if re.search(rf"\b{re.escape(a)}\b", t):
            sig.add(f"{a}:neg" if neg else a)
        if re.search(rf"\b{re.escape(b)}\b", t):
            sig.add(f"{b}:neg" if neg else b)
    return sig


def _is_opposite(sig_a: set[str], sig_b: set[str]) -> bool:
    """True iff *sig_a* contains one half of a polarity pair and *sig_b* contains
    the other half (negation-aware). Also handles ``x`` vs ``x:neg``."""
    if not sig_a or not sig_b:
        return False
    norm_a = {s.split(":")[0] for s in sig_a}
    norm_b = {s.split(":")[0] for s in sig_b}
    neg_a = {s.split(":")[0] for s in sig_a if s.endswith(":neg")}
    neg_b = {s.split(":")[0] for s in sig_b if s.endswith(":neg")}
    pos_a = norm_a - neg_a
    pos_b = norm_b - neg_b

    for a, b in POLARITY_PAIRS:
        if (a in pos_a and b in pos_b) or (b in pos_a and a in pos_b):
            return True
    for term in pos_a & neg_b:
        return True
    for term in pos_b & neg_a:
        return True
    return False


def _candidate_lines(path: Path) -> Iterable[tuple[int, str]]:
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return []
    out = []
    for i, line in enumerate(text.splitlines(), start=1):
        if TYPE_RE.search(line):
            out.append((i, line.strip()))
    return out


def _all_workspace_files(ws: str) -> list[Path]:
    paths: list[Path] = []
    paths.extend(iter_topic_files(ws))
    jd = journal_dir(ws)
    if jd.is_dir():
        paths.extend(p for p in jd.glob("*.md") if p.is_file())
    wd = workspace_dir(ws)
    docs = wd / "docs"
    if docs.is_dir():
        paths.extend(p for p in docs.glob("*.md") if p.is_file())
    return paths


def find_contradictions(ws: str | None = None,
                        min_entity_overlap: int = 3) -> list[dict]:
    """Return a list of candidate contradiction pairs across workspace *ws*.

    Each pair has shape::

        {
          "a": {"path": str, "line": int, "text": str},
          "b": {"path": str, "line": int, "text": str},
          "shared_entities": [str, ...],
          "polarity_a": [str, ...],
          "polarity_b": [str, ...],
        }
    """
    ws = ws or active_workspace()
    gh = gowth_home()
    entries: list[tuple[str, int, str, set[str], set[str]]] = []
    for f in _all_workspace_files(ws):
        try:
            rel = str(f.relative_to(gh))
        except ValueError:
            rel = str(f)
        for lineno, line in _candidate_lines(f):
            kws = _keywords(line)
            sig = _polarity_signature(line)
            if not sig:
                continue
            entries.append((rel, lineno, line, kws, sig))

    pairs: list[dict] = []
    for i in range(len(entries)):
        pa, la, ta, ka, sa = entries[i]
        for j in range(i + 1, len(entries)):
            pb, lb, tb, kb, sb = entries[j]
            shared = ka & kb
            if len(shared) < min_entity_overlap:
                continue
            if not _is_opposite(sa, sb):
                continue
            pairs.append({
                "a": {"path": pa, "line": la, "text": ta},
                "b": {"path": pb, "line": lb, "text": tb},
                "shared_entities": sorted(shared)[:8],
                "polarity_a": sorted(sa),
                "polarity_b": sorted(sb),
            })
    return pairs


def format_report(pairs: list[dict]) -> str:
    if not pairs:
        return "[contradict] no candidate contradictions found."
    lines = [f"[contradict] {len(pairs)} candidate pair(s):"]
    for i, p in enumerate(pairs, start=1):
        lines.append(f"  #{i}  shared={','.join(p['shared_entities'][:4])}")
        lines.append(f"    A  {p['a']['path']}:{p['a']['line']}  {p['a']['text'][:120]}")
        lines.append(f"    B  {p['b']['path']}:{p['b']['line']}  {p['b']['text'][:120]}")
        lines.append(f"       polarity_a={p['polarity_a']}  polarity_b={p['polarity_b']}")
    return "\n".join(lines)


def main() -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Heuristic contradiction scanner")
    ap.add_argument("--ws", default=None)
    ap.add_argument("--min-overlap", type=int, default=3)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    pairs = find_contradictions(ws=args.ws, min_entity_overlap=args.min_overlap)
    if args.json:
        print(json.dumps({"count": len(pairs), "pairs": pairs}, indent=2))
    else:
        print(format_report(pairs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
