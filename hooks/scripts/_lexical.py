#!/usr/bin/env python3
"""Deterministic lexical similarity — char n-gram Jaccard, pure stdlib.

Used as a fallback when FTS5 BM25 returns too few results (e.g. fuzzy/typo
queries, Vietnamese morphology where unicode61 tokeniser splits poorly).

No LLM, no embeddings, no pip deps. Cheap enough to run inline.

Public API:
    char_ngrams(text, n=3)              -> set[str]
    jaccard(a, b)                       -> float in [0, 1]
    fuzzy_search(query, candidates, top_k=10, min_score=0.15)
        candidates: Iterable[(key, text)]
        returns: list[(key, score)] sorted desc, len <= top_k

Tuning notes:
  - n=3 (trigrams) is the sweet spot for typo tolerance + small set size.
  - min_score=0.15 cuts most noise on short strings; lower it for short queries.
  - Both query and candidate text are lowercased & whitespace-normalised before
    n-gram extraction so case/spacing differences don't tank the score.
"""
from __future__ import annotations

import re
from typing import Iterable


_WS_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _WS_RE.sub(" ", text.lower()).strip()


def char_ngrams(text: str, n: int = 3) -> set[str]:
    """Return the set of character n-grams of length *n* in *text*.

    Text is normalised (lowercased, whitespace collapsed) before extraction.
    Returns ``set()`` for inputs shorter than *n*.
    """
    if n < 1:
        raise ValueError("n must be >= 1")
    t = _normalize(text)
    if len(t) < n:
        return set()
    return {t[i:i + n] for i in range(len(t) - n + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity |a ∩ b| / |a ∪ b|. Returns 0 when both empty."""
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def fuzzy_search(
    query: str,
    candidates: Iterable[tuple[str, str]],
    top_k: int = 10,
    min_score: float = 0.15,
    n: int = 3,
) -> list[tuple[str, float]]:
    """Rank *candidates* against *query* by char-n-gram Jaccard.

    candidates: iterable of (key, text) — key is whatever caller wants returned
                (path, slug, id...); text is the searchable content.

    Returns [(key, score)] sorted by score desc, limited to *top_k* and filtered
    by *min_score*. Stable: ties preserve input order.
    """
    q = char_ngrams(query, n=n)
    if not q:
        return []
    scored: list[tuple[str, float]] = []
    for key, text in candidates:
        c = char_ngrams(text, n=n)
        s = jaccard(q, c)
        if s >= min_score:
            scored.append((key, s))
    scored.sort(key=lambda kv: -kv[1])
    return scored[:top_k]


def main() -> int:
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="char-ngram fuzzy search demo")
    ap.add_argument("query")
    ap.add_argument("texts", nargs="+", help="candidate texts (one per arg)")
    ap.add_argument("-n", type=int, default=3)
    ap.add_argument("--min", type=float, default=0.15)
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()

    pairs = [(f"idx-{i}", t) for i, t in enumerate(args.texts)]
    for key, score in fuzzy_search(args.query, pairs, top_k=args.top,
                                   min_score=args.min, n=args.n):
        print(f"{score:.3f}\t{key}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
