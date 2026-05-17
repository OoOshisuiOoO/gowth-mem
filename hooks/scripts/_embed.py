#!/usr/bin/env python3
"""Embedding client — DISABLED by default in v3.3.

v3.3 ships with **deterministic-only retrieval**: FTS5 BM25 (in ``_index.py``)
plus char-ngram Jaccard fuzzy match (``_lexical.py``). No external LLM
embedding API is called unless the user opts in explicitly by setting
``GOWTH_MEM_USE_LLM_EMBED=1``.

When opt-in is set, the same providers as before are supported (legacy path):
  OPENAI_API_KEY  → OpenAI text-embedding-3-small (1536d, Matryoshka cut to 512)
  VOYAGE_API_KEY  → Voyage voyage-multilingual-2 (1024d, best for Vietnamese)
  GEMINI_API_KEY  → Gemini gemini-embedding-001 (768d default)
                    (also accepts GOOGLE_API_KEY)

Without the opt-in, ``embed_one()`` and ``detect_provider()`` return ``None``,
and ``_index.py`` builds an FTS5-only index. This honours the v3.3 design
goal: no LLM API in the runtime path.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Optional


DEFAULT_DIM = 512  # Matryoshka cut for OpenAI / Gemini; for Voyage use native 1024.

OPT_IN_ENV = "GOWTH_MEM_USE_LLM_EMBED"


def _llm_embed_opted_in() -> bool:
    val = os.environ.get(OPT_IN_ENV, "").strip().lower()
    return val in {"1", "true", "yes", "on"}


def detect_provider() -> Optional[tuple[str, str]]:
    if not _llm_embed_opted_in():
        return None
    if os.environ.get("OPENAI_API_KEY"):
        return ("openai", os.environ["OPENAI_API_KEY"])
    if os.environ.get("VOYAGE_API_KEY"):
        return ("voyage", os.environ["VOYAGE_API_KEY"])
    g = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if g:
        return ("gemini", g)
    return None


def embed_one(text: str, timeout: float = 8.0) -> Optional[list[float]]:
    if not _llm_embed_opted_in():
        return None
    p = detect_provider()
    if not p:
        return None
    provider, key = p
    text = text[:8000]  # cap input
    try:
        if provider == "openai":
            req = urllib.request.Request(
                "https://api.openai.com/v1/embeddings",
                data=json.dumps({
                    "input": text,
                    "model": "text-embedding-3-small",
                    "dimensions": DEFAULT_DIM,
                }).encode(),
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read())
            return d["data"][0]["embedding"]
        if provider == "voyage":
            req = urllib.request.Request(
                "https://api.voyageai.com/v1/embeddings",
                data=json.dumps({
                    "input": [text],
                    "model": "voyage-multilingual-2",
                }).encode(),
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read())
            return d["data"][0]["embedding"]
        if provider == "gemini":
            url = (
                "https://generativelanguage.googleapis.com/v1beta/"
                f"models/gemini-embedding-001:embedContent?key={key}"
            )
            req = urllib.request.Request(
                url,
                data=json.dumps({
                    "content": {"parts": [{"text": text}]},
                    "outputDimensionality": DEFAULT_DIM,
                }).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read())
            return d["embedding"]["values"]
    except (urllib.error.URLError, KeyError, ValueError, TimeoutError):
        return None
    return None


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: _embed.py <text>", file=sys.stderr)
        return 2
    vec = embed_one(sys.argv[1])
    if vec is None:
        print(json.dumps({"error": "no provider key, or API call failed"}))
        return 1
    p = detect_provider()
    print(json.dumps({"provider": p[0] if p else None, "dim": len(vec), "vector": vec[:5] + ["..."]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
