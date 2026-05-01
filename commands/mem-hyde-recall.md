---
description: HyDE-style recall for conceptual / underspecified queries that grep+vector miss. Generates a hypothetical answer paragraph, embeds it, then recalls. Costs 1 LLM call; use deliberately, not on every prompt.
argument-hint: "<conceptual question>"
---

Run a HyDE (Hypothetical Document Embedding) recall for a conceptual question that the on-prompt recall hook missed.

How it works:

1. **Generate** a brief hypothetical answer to the question (1-2 paragraphs) using your in-session knowledge — this is the "hypothetical document".
2. **Embed** that hypothetical document and search the SQLite index built by `/mem-reindex`.
3. **Synthesize** retrieved chunks against the original question.

When to use:
- Question uses vague / abstract language ("how do we handle X?"; "what's our strategy for Y?").
- The auto-recall hook returned nothing or irrelevant matches.
- Worth 1 extra LLM call to find the right context.

When NOT to use:
- Question has specific identifiers (file names, function names, error messages) — grep / vector finds these directly.
- Index doesn't exist (`/mem-reindex` hasn't been run) — falls back to grep over docs/** and wiki/**.

## Steps

1. Read the question.
2. Draft a 1-2 paragraph "ideal answer" as if you already knew the answer (your hypothetical document).
3. If `.gowth-mem/index.db` and `sqlite-vec` are available + embedding key:
   - Use the index to find chunks similar to the hypothetical document.
4. Else:
   - Fall back to grep over `docs/**/*.md` and `wiki/**/*.md` using keywords from your hypothetical document.
5. Synthesize what you find against the original question. Cite sources `<file>:<heading>`.
6. If still no useful match, suggest the user run `/mem-reindex` (if no index) or `/wiki-query` (claude-obsidian deep search).

## Reference

HyDE paper: Gao et al. 2022 — "Precise Zero-Shot Dense Retrieval without Relevance Labels" (`arXiv:2212.10496`).
