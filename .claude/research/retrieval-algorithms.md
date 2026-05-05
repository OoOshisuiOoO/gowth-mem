# Retrieval Algorithms — What We Use and Why

Condensed from `RESEARCH.md` § B. Focus on what gowth-mem actually implements.

## Implemented

| Algorithm | Our implementation | File |
|---|---|---|
| **BM25 (FTS5)** | SQLite FTS5 full-text search on topic/doc content | `_index.py`, `recall-active.py` |
| **Vector similarity** | Optional sqlite-vec with OpenAI/Voyage/Gemini embeddings | `_embed.py`, `_index.py` |
| **Hybrid fusion (RRF)** | Reciprocal Rank Fusion at k=60, merging BM25 + vector results | `recall-active.py` |
| **MMR diversity** | Post-process: skip files with Jaccard >0.6 overlap with already-selected | `recall-active.py` |
| **Contextual retrieval** | Prepend `§ <heading> | <line>` breadcrumb to each match | `recall-active.py` |
| **SM-2 SRS** | SM-2-lite in `state.json`; ~25% prob/prompt resurfaces files unseen ≥7 days | `recall-active.py` |
| **HyDE-lite** | Opt-in `/mem-hyde-recall`: draft hypothetical answer → embed → retrieve → synthesize | `/mem-hyde-recall` skill |
| **Grep fallback** | When no index.db exists: grep topics + docs with keyword extraction | `recall-active.py` |

## 3-tier graceful degradation

```
index.db + embeddings → hybrid BM25 + vector (best quality)
index.db only         → FTS5 BM25 (good quality, no API key needed)
no index.db           → grep fallback (works everywhere)
```

## Evaluated but not implemented

| Algorithm | Why not |
|---|---|
| **RAPTOR** | High offline cost (LLM summarization passes); our topics are already curated |
| **GraphRAG** | LLM-heavy graph build; overkill for <1000 topic files |
| **LightRAG** | Simpler than GraphRAG but still graph-based; not worth complexity for our scale |
| **ColBERT** | Per-token vectors need multi-vector index; overkill for markdown |
| **bge-reranker** | Extra model inference at query time; MMR diversity is sufficient for us |

## Embedding model choice

Best for Vietnamese + English mixed content: `voyage-multilingual-2` (+5.6% over OpenAI v3).
Default: `text-embedding-3-small` (1536-dim, widely available).
Budget: `bge-small-en` (384-dim, 4× smaller index, slight quality drop).

Auto-detected from env: `VOYAGE_API_KEY` → Voyage, `OPENAI_API_KEY` → OpenAI, `GEMINI_API_KEY` → Gemini.
