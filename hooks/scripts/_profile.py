#!/usr/bin/env python3
"""Deterministic query profile phi(q) + safe FTS5 expression builder (v4.3).

Adopts Zero-Mem eq (6) — arXiv 2607.29377v1 §3.2:

    "For each query, Zero-Mem constructs a lightweight profile
     phi(q) = {subject, keywords, answer-type, temporal-cues, boundary}."

Why this exists (the bug it fixes), measured on a 15,145-chunk live vault:

  * FTS5 ANDs bare terms, so `_query.py` passing raw user text meant ANY
    natural-language query returned nothing:
        "why did we drop vector recall"          -> 0 hits
        '"why" OR "did" OR ... OR "recall"'      -> 991 hits
  * Unquoted punctuation is FTS5 syntax, so hyphens/colons raised
    OperationalError, which `except Exception: return []` swallowed into a
    silent "(no results)":
        vector-recall  -> no such column: recall
        forget: ttl    -> no such column: forget

`fts_match()` therefore emits ONLY quoted phrases joined by OR. Everything a
user can type becomes a literal; no user input is ever interpreted as FTS5
syntax. Quoting also turns `vector-recall` into the phrase "vector recall",
which is the intended match rather than an error.

Pure stdlib. No LLM, no embeddings, no pip deps. Reuses _tags.py's tested
identifier harvester so `_forget.py`, `raw_ttl_days` and `prop-firm-funding`
survive tokenisation intact.

Public API:
    profile(query, days=0) -> dict
        {subject, keywords, answer_type, temporal, boundary}
    fts_match(profile) -> str
        Safe FTS5 MATCH expression, or "" when nothing is searchable.

CLI:
    python3 _profile.py "why did we drop vector recall"
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _tags import (  # type: ignore
    _PROSE_WORD_RE,
    STOPWORDS,
    _harvest_priority,
)

# Minimum length for a prose keyword. Identifiers harvested by _tags are exempt —
# `db`, `ci`, `s3` are meaningful when they appear as identifiers, not as prose.
MIN_PROSE_LEN = 3

# ── answer-type cues (eq 6 "answer-type") ────────────────────────────────
# Maps a query's grammatical shape onto gowth-mem's 9-type schema so calibration
# can prefer entries of the requested type. Order matters: first match wins, so
# the more specific cue must come first.
_TYPE_CUES: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\b(why did we|why do we|decid\w*|chose|choose|chosen|rationale|"
                r"tradeoff|trade-off)\b", re.I), "decision"),
    (re.compile(r"\b(broke|broken|bug|failed|failure|mistake|lesson|postmortem|"
                r"regress\w*|went wrong|why didn't)\b", re.I), "exp"),
    (re.compile(r"\b(how do i|how to|how does|steps|workflow|procedure|runbook|"
                r"recipe)\b", re.I), "skill-ref"),
    (re.compile(r"\b(version|command|flag|cli|install\w*|upgrade)\b", re.I), "tool"),
    (re.compile(r"\b(goal|objective|target|aiming|plan to)\b", re.I), "goal"),
    (re.compile(r"\b(unverified|assumption|hypothesis|suspect|might be)\b", re.I),
     "hypothesis"),
    (re.compile(r"\b(what is|what are|define|definition|fact|source|reference)\b",
                re.I), "ref"),
)

# ── temporal cues (eq 6 "temporal-cues") ─────────────────────────────────
_TEMPORAL_RE = re.compile(
    r"\b(today|yesterday|tomorrow|tonight|last\s+(?:week|month|year|time|session)|"
    r"this\s+(?:week|month|year)|recent\w*|latest|newest|current\w*|lately|"
    r"when|before|after|since|until|ago|earlier|previous\w*|"
    r"\d{4}-\d{2}-\d{2}|v\d+(?:\.\d+)+)\b",
    re.I,
)

# Characters that carry meaning to FTS5 and must never reach it unquoted.
_UNSAFE_IN_TOKEN = re.compile(r'["*^:()\[\]{}]')


def _dedupe(seq: list[str]) -> list[str]:
    """Order-preserving dedupe, case-insensitive on the comparison key."""
    out: list[str] = []
    seen: set[str] = set()
    for tok in seq:
        key = tok.lower()
        if key and key not in seen:
            seen.add(key)
            out.append(tok)
    return out


def keywords_of(query: str) -> list[str]:
    """Return content anchors: harvested identifiers first, then prose words.

    Identifiers keep their original form (`_forget.py`, `raw_ttl_days`); prose
    words are lowercased. Stopwords (EN + VI + ascii-VI, from _tags) are dropped.
    """
    idents, prose_text = _harvest_priority(query)
    prose: list[str] = []
    for m in _PROSE_WORD_RE.finditer(prose_text):
        word = m.group(0)
        low = word.lower()
        if low in STOPWORDS or len(low) < MIN_PROSE_LEN or low.isdigit():
            continue
        prose.append(low)
    return _dedupe(idents + prose)


def _answer_type(query: str) -> str:
    for rx, tag in _TYPE_CUES:
        if rx.search(query):
            return tag
    return ""


def profile(query: str, days: int = 0) -> dict:
    """Return phi(q) = {subject, keywords, answer_type, temporal, boundary}.

    subject   most specific anchor (first harvested identifier, else the longest
              prose keyword) — "" when the query has no content words.
    keywords  ordered content anchors (see keywords_of).
    answer_type  one of the 9 schema tags, or "" when the query gives no cue.
    temporal  True when the query asks about time ordering or recency.
    boundary  admissible interaction scope in days, or None when unbounded.
    """
    query = query or ""
    kws = keywords_of(query)
    idents, _ = _harvest_priority(query)
    if idents:
        subject = idents[0]
    elif kws:
        subject = max(kws, key=len)
    else:
        subject = ""
    return {
        "subject": subject,
        "keywords": kws,
        "answer_type": _answer_type(query),
        "temporal": bool(_TEMPORAL_RE.search(query)),
        "boundary": days if days and days > 0 else None,
    }


def _safe_token(tok: str) -> str:
    """Strip every character FTS5 could read as syntax; return '' if nothing left."""
    cleaned = _UNSAFE_IN_TOKEN.sub(" ", tok).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def fts_match(prof: dict, max_terms: int = 12) -> str:
    """Build a safe FTS5 MATCH expression from a profile.

    Every term is emitted as a quoted phrase joined by OR, so:
      * no bare term can be read as a column filter, operator, or prefix query;
      * multi-word/hyphenated anchors match as phrases;
      * a query of only stopwords/punctuation yields "" (caller must treat an
        empty expression as "nothing searchable", NOT as "no results").

    OR (not AND) is deliberate: FTS5's implicit AND is what made every
    natural-language query return zero rows. BM25 ranking handles precision.
    """
    terms: list[str] = []
    for tok in prof.get("keywords") or []:
        safe = _safe_token(tok)
        if len(safe) < 2:
            continue
        terms.append(f'"{safe}"')
        if len(terms) >= max_terms:
            break
    return " OR ".join(terms)


def _cli() -> int:
    if len(sys.argv) < 2:
        print(__doc__.strip().splitlines()[0])
        print("usage: _profile.py <query>")
        return 0
    q = " ".join(sys.argv[1:])
    p = profile(q)
    print(f"query      : {q}")
    print(f"subject    : {p['subject'] or '-'}")
    print(f"keywords   : {', '.join(p['keywords']) or '-'}")
    print(f"answer_type: {p['answer_type'] or '-'}")
    print(f"temporal   : {p['temporal']}")
    print(f"boundary   : {p['boundary']}")
    print(f"fts_match  : {fts_match(p) or '(nothing searchable)'}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
