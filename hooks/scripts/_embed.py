#!/usr/bin/env python3
"""Tiny embedding HTTP client (stdlib only — urllib).

Auto-detects which embedding API to use based on env vars:
  OPENAI_API_KEY    → OpenAI text-embedding-3-small (1536d, supports Matryoshka cut to 512)
  VOYAGE_API_KEY    → Voyage voyage-multilingual-2 (1024d, best for Vietnamese)
  GEMINI_API_KEY    → Gemini gemini-embedding-001 (768d default, supports cut)
                      (also accepts GOOGLE_API_KEY)

If no key is set, returns None (graceful fallback).

Usage as module:
    from _embed import embed_one, detect_provider
    vec = embed_one("query text")  # list[float] or None

Usage as CLI:
    python3 _embed.py "query text"  # prints JSON {provider, dim, vector}
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Optional


DEFAULT_DIM = 512  # Matryoshka cut for OpenAI / Gemini; for Voyage use native 1024.


def detect_provider() -> Optional[tuple[str, str]]:
    if os.environ.get("OPENAI_API_KEY"):
        return ("openai", os.environ["OPENAI_API_KEY"])
    if os.environ.get("VOYAGE_API_KEY"):
        return ("voyage", os.environ["VOYAGE_API_KEY"])
    g = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if g:
        return ("gemini", g)
    return None


def embed_one(text: str, timeout: float = 8.0) -> Optional[list[float]]:
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
