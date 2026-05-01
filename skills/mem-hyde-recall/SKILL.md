---
name: mem-hyde-recall
description: Use deliberately when a conceptual / underspecified question's automatic recall returned nothing useful. Generates a hypothetical answer, uses that to find chunks via vector or grep. Costs 1 extra LLM cycle.
---

# mem-hyde-recall

HyDE-pattern recall for queries the standard hook misses.

## Inputs

- The user's conceptual question.
- Workspace root.

## Algorithm (HyDE — Hypothetical Document Embeddings)

1. From the question, **draft a 1-2 paragraph hypothetical answer** as if you already knew it. Don't worry about being correct — the goal is to produce text whose embedding sits near the right chunks in vector space.
2. **Search**:
   - If `.gowth-mem/index.db` exists AND `sqlite-vec` is importable AND an embedding key is set:
     - Embed the hypothetical answer.
     - Vector top-K against `chunks_vec`.
     - Combine with FTS5 BM25 over original question via RRF fusion (k=60 standard).
   - Else (graceful fallback):
     - Extract ≥5-char keywords from the hypothetical answer.
     - Grep `docs/**/*.md` and `wiki/**/*.md`.
3. Filter out temporal-invalid lines (`(superseded)` or expired `valid_until:`).
4. Return up to 5 chunks with file paths and headings as evidence.
5. **Synthesize** against the original question. Cite each chunk like `docs/exp.md § Lessons` or `wiki/topics/EMA Cross.md § Setup`.

## Output format

```
## HyDE recall for: "<question>"

### Hypothetical answer (used for retrieval, not authoritative)

<your draft, 1-2 paragraphs>

### Retrieved evidence

- docs/exp.md § Lessons — <line>
- wiki/topics/Foo.md § Bar — <line>
- ...

### Synthesis

<answer the original question, citing only the retrieved evidence>
```

## Hard rules

- The hypothetical answer is for retrieval only — don't present it as fact.
- Synthesis must cite specific chunks (file + heading); no hallucinated sources.
- If no useful chunks retrieved, say so plainly and suggest `/mem-reindex`, `/wiki-query`, or external research.
- Never write to docs/* during this skill — it's read-only.

## Cost

- 1 LLM call to draft the hypothetical answer.
- 1 embedding call (~$0.000003) if vector path active.
- 0 extra calls in fallback grep path.
