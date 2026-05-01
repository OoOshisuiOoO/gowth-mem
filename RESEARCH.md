# RESEARCH — memory & retrieval state of the art (2025-2026)

Catalog of LLM agent memory systems, retrieval algorithms, token-efficiency techniques, and PKM patterns relevant to gowth-mem. Sources cited from perplexity deep-research lanes; exact URLs in citations at end of each section.

## A. Memory systems for LLM agents

| System | Repo | Architecture | Memory types | Token tactic |
|---|---|---|---|---|
| **mem0** | [mem0ai/mem0](https://github.com/mem0ai/mem0) | LLM extracts memories async; ADD/UPDATE/DELETE/NOOP rewrite logic; vector + entity + SQL stores | semantic facts, light episodic | ~90% token reduction vs raw history; only retrieves relevant snippets |
| **Letta / MemGPT** | [letta-ai/letta](https://github.com/letta-ai/letta) | OS-style hierarchy: core (always in prompt) / recall (event log) / archival (RAG) | core=semantic, recall=episodic, archival=semantic+docs | strict size limit on core; explicit promote/demote between tiers |
| **Zep** | [getzep/zep](https://github.com/getzep/zep) | Temporal knowledge graph (Graphiti); BM25 + semantic + graph traversal | semantic (with `valid_at`/`invalid_at`) | facts replace each other temporally; small slice per query |
| **Cognee** | [topoteretes/cognee](https://github.com/topoteretes/cognee) | Triple store: graph (Kuzu) + vector (LanceDB) + relational (SQLite) | semantic + episodic | 6-line setup; multi-hop traversal cuts long chunks |
| **Anthropic memory tool** | (built into Claude SDK) | Client-side `/memories` file tree; view/create/str_replace/insert/delete | episodic + semantic + procedural (file-based) | JIT load via `view`; pagination; agent prunes outdated |
| **LangChain / LangMem** | [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph) | Short-term thread state + long-term JSON store; explicit semantic/episodic/procedural taxonomy | all 3 | `Episode` schema (observation/thoughts/action/result) for compact episodic |
| **LlamaIndex memory** | [run-llama/llama_index](https://github.com/run-llama/llama_index) | FIFO chat + Memory Blocks (Static/FactExtraction/Vector); priorities | episodic + semantic | block priorities act as pruning order under tight budget |
| **MemoRAG** | [VectorSpaceLab/MemoRAG](https://github.com/VectorSpaceLab/MemoRAG) | Long-context model (≤1M tokens) acts as global memory; KV cache reuse | episodic+semantic fused into model state | encode once, reuse 20-30× faster than re-encoding |
| **HippoRAG** | [OSU-NLP-Group/HippoRAG](https://github.com/OSU-NLP-Group/HippoRAG) | OpenIE → knowledge graph → Personalized PageRank for multi-hop | semantic + associative | single-step multi-hop avoids long chains in prompt |
| **Generative Agents** | [joonspk-research/generative_agents](https://github.com/joonspk-research/generative_agents) | Memory stream + reflection module; importance × recency × relevance | episodic + reflected semantic | replays only top-scored memories per "thought" |
| **Voyager** | [MineDojo/Voyager](https://github.com/MineDojo/Voyager) | Skill library: code + NL description, semantically retrieved | procedural | calling skill `use_skill('build_bridge')` ≪ replaying long instructions; 3.3× more items, 15.3× faster tech progress |
| **Reflexion** | [noahshinn024/reflexion](https://github.com/noahshinn024/reflexion) | Self-reflection module produces NL lessons stored in episodic buffer | episodic + procedural via reflection | short reflection texts ≪ full dialogues; ~50% token cut on retried tasks |

**Convergent patterns** across all 12:
1. **2-3 tier hierarchy** (working / mid / archival) — exactly our 4-layer.
2. **Async extraction vs online retrieval** — mem0 / Cognee / Zep / HippoRAG / Reflexion all do heavyweight extract offline, lightweight retrieve online.
3. **Semantic + episodic + procedural** taxonomy explicit in LangMem.
4. **ADD/UPDATE/DELETE rewrite** beats blind append (mem0, Generative Agents).
5. **Multi-signal scoring** (semantic + BM25 + entity + recency) at retrieval.

## B. Retrieval algorithms

| Algorithm | When to use | Trade-offs | Implementation hint |
|---|---|---|---|
| **BM25 + vector hybrid (RRF)** | technical docs / code where exact identifiers matter but users paraphrase | maintain 2 indexes, choose fusion (RRF / weighted) | Redis Stack, OpenSearch, Vespa expose hybrid native |
| **MMR (Maximal Marginal Relevance)** | dedup near-duplicate chunks from same section | O(k²) cosine, λ hyperparameter, ~30 lines code | post-process top-K vector hits |
| **HyDE (Hypothetical Document Embeddings)** | underspecified / conceptual queries | extra LLM call/query | `imanoop7/RAG-with-Hyde`, Haystack `HypotheticalDocumentEmbedder` |
| **RAPTOR** | very long docs, multi-step thematic questions | high offline cost, LLM summarization passes | LlamaIndex `RaptorRetriever` pack; `1rsh/raptor-rag` |
| **GraphRAG (Microsoft)** | corpora where relationships ≥ text content | LLM-heavy graph build + community summaries | [microsoft/graphrag](https://github.com/microsoft/graphrag); 3.4× better on complex QA |
| **LightRAG** | small/medium corpora, graph-lite | simpler than GraphRAG, less powerful | [HKUDS/LightRAG](https://github.com/HKUDS/LightRAG) |
| **bge-reranker / Cohere Rerank** | last-hop precision boost | linear in top-K × seq-len | [BAAI/bge-reranker-base](https://huggingface.co/BAAI/bge-reranker-base) |
| **ColBERT (late interaction)** | near cross-encoder quality at large scale | per-token vectors, multi-vector index | [stanford-futuredata/ColBERT](https://github.com/stanford-futuredata/ColBERT) |
| **ColPali** | visual docs (PDFs/scans), bypasses OCR | per-page patch embeddings | [illuin-tech/colpali](https://github.com/illuin-tech/colpali) |
| **Contextual retrieval (Anthropic)** | retrieval-failure off-by-section bugs | larger indexed text | embed chunk **with parent heading + breadcrumb**; -49% to -67% retrieval failures |
| **SM-2 / Anki SRS** | resurfacing old knowledge over time | cards / interval scheduling | [open-spaced-repetition/fsrs4anki](https://github.com/open-spaced-repetition/fsrs4anki) |

## C. Token efficiency techniques

| Technique | Savings | Trade-off | Repo / tool |
|---|---|---|---|
| **Provider prompt caching** | 75-90% discount on cached prefix; 40-85% TTFT cut | exact-prefix match, TTL ~5 min - 1 hr | Anthropic / OpenAI / Gemini native |
| **Semantic cache (GPTCache)** | skip entire LLM call on hit (0 tokens) | embedding latency on miss; staleness risk | [zilliztech/GPTCache](https://github.com/zilliztech/GPTCache) |
| **LongLLMLingua** | 2-10× compression, sometimes IMPROVES accuracy; up to 94% cost cut | lossy on edge cases; need compressor | [microsoft/LLMLingua](https://github.com/microsoft/LLMLingua) |
| **AutoCompressor** | 660 tokens → 50 soft prompts | needs finetuned model (LLaMA/OPT) | [princeton-nlp/AutoCompressors](https://github.com/princeton-nlp/AutoCompressors) |
| **Gist tokens** | up to 26× compression, ~40% FLOPs cut | needs custom-trained model | OpenReview "Learning to Compress Prompts with Gist Tokens" |
| **Recurrent Memory Transformer** | linear scaling to millions of tokens | custom architecture, not on hosted APIs | RMT NeurIPS paper |
| **JIT hierarchical retrieval (H-MEM, HiGMem)** | ~10× fewer turns retrieved vs flat | routing failure → missed context | H-MEM, HiGMem repos |
| **Voyager skill reuse** | invoke skill ≪ replay full instructions | engineering for discovery + sandboxing | [MineDojo/Voyager](https://github.com/MineDojo/Voyager) |
| **Embedding model choice** | 384-dim (bge-small) vs 1536 (text-embed-3-small): 4× index size | quality varies | [BAAI/bge-small-en](https://huggingface.co/BAAI/bge-small-en), `text-embedding-3-small`, `voyage-multilingual-2` (best for Vietnamese, +5.6% over OpenAI v3) |
| **Vector index quantization** | int8: 4× memory cut; bfloat16: 2× cut, near-zero quality drop | tuning per model | [asg017/sqlite-vec](https://github.com/asg017/sqlite-vec) |

## D. PKM patterns adaptable to AI memory

| Pattern | Core principle | Adaptation hint |
|---|---|---|
| **Zettelkasten** | atomic notes + unique IDs + bidirectional links + emergent structure | `wiki/concepts/<atom>.md` per fact; `[[wikilinks]]` everywhere |
| **Smart Connections** | Obsidian semantic search via embedding | match our v0.4 hybrid recall idea |
| **BASB / PARA** | Project / Area / Resource / Archive folders | mirror in docs/ taxonomy by lifecycle |
| **Logseq blocks** | every block addressable, outline-first | journal entries as bullets are already block-addressable |
| **Roam bidirectional links** | link any concept anywhere | Obsidian `[[]]` ≡ |
| **Tana supertags** | typed tags with attributes | YAML frontmatter `type: topic\|concept\|fact\|skill` |
| **Andy Matuschak evergreen notes** | notes never finished, evolve over time | mem-distill UPDATE > ADD when entry exists |
| **Generative Agents reflection** | periodic high-level summary from memory stream | NEW: `/mem-reflect` skill (importance-weighted) |
| **Karpathy LLM Wiki** | persistent markdown vault LLM reads | already → claude-obsidian's `wiki/` |
| **Voyager skill library** | reusable code + NL description | NEW: `docs/skills/<name>.md` convention |

## E. Mapping to gowth-mem layers

```
Layer 1 (raw journal)          ←  Anthropic /memories pattern, Logseq blocks
Layer 2 (curated docs/)        ←  mem0 ADD/UPDATE/DELETE, LangMem Episode schema, Reflexion lessons
Layer 3 (wiki/topics)          ←  Cognee graph view, HippoRAG, Roam/Obsidian wikilinks
Layer 4 (wiki/concepts)        ←  Zettelkasten atomic notes, Andy Matuschak evergreen
Always-on (handoff/secrets)    ←  Letta core memory pattern
NEW: docs/skills/              ←  Voyager skill library (procedural memory)
NEW: /mem-reflect              ←  Generative Agents reflection
NEW: contextual recall         ←  Anthropic contextual retrieval
```

## F. v0.4 roadmap (priority by ROI)

### Tier 1 — ship now (no new deps)

1. **mem0 ADD/UPDATE/DELETE/NOOP** in mem-distill skill — prevents dedup bloat, file-size wins.
2. **Contextual retrieval** in recall-active.py — prepend heading/breadcrumb to each match line. Reported 35-67% reduction in retrieval failures.
3. **MMR diversity** in recall-active.py — when 3 hits cluster in same file, pick across files.
4. **Voyager skill library** convention — `docs/skills/<name>.md` with description + steps. Auto-loaded by recall when intent matches.
5. **Generative Agents reflection** — `/mem-reflect` reads journal, produces 1-3 high-level summaries to docs/exp.md or wiki/concepts.

### Tier 2 — needs light infra (sqlite-vec, embedding API)

6. ✅ **SHIPPED v0.6**: Hybrid BM25 + vector recall via SQLite FTS5 + sqlite-vec + RRF fusion. Auto-detects `OPENAI_API_KEY` / `VOYAGE_API_KEY` / `GEMINI_API_KEY`. Graceful 3-tier fallback: vector hybrid → FTS5-only → grep. Build/refresh via `/mem-reindex`.
7. **SKIPPED**: Semantic response cache (GPTCache pattern). Stale-answer risk for evolving code work; limited ROI for our retrieval-only path.
8. ✅ **SHIPPED v0.5**: Spaced resurfacing — `.gowth-mem/state.json` SM-2-lite tracker; ~25% prob per prompt resurfaces files unseen ≥7 days.

### Tier 3 — architectural

9. ✅ **SHIPPED v0.5**: Temporal facts — `valid_until: YYYY-MM-DD` and `(superseded)` markers; recall auto-skips invalid lines.
10. ✅ **SHIPPED v0.6**: HyDE-lite — exposed as opt-in `/mem-hyde-recall <question>` skill rather than auto-on-prompt. Drafts hypothetical answer, embeds via index (or falls back to keyword grep), synthesizes against retrieved chunks.
11. ✅ **SHIPPED v0.5**: Provider prompt caching guidance in `templates/AGENTS.md` § Token efficiency. Stable prefix (AGENTS / SECRETS / TOOLS / FILES) → cache hit; volatile suffix (handoff / journal / recall) → cache miss expected.

### Bonus shipped v0.5

12. ✅ **Token cost estimator** `/mem-cost` — char + token breakdown of bootstrap; warns if approaching 60k cap.

### Shipped v0.7 — auto-trigger hooks (mempalace-inspired)

After studying [MemPalace](https://github.com/MemPalace/mempalace) (their `mempal_save_hook.sh` fires every 15 messages and BLOCKS the AI; `mempal_precompact_hook.sh` forces emergency save before compact), shipped 3 auto-trigger upgrades to remove manual skill invocation:

13. ✅ **Stop hook `auto-journal.py`** — counts user turns in `.gowth-mem/state.json` per session; every 10 turns, emits `decision: "block"` with full mem-distill instructions inline. Claude saves before yielding. Replaces manual `/mem-distill`.

14. ✅ **PreCompact upgraded to BLOCK** — was advisory `additionalContext`; now `decision: "block"` with full save instructions. Compact can't proceed until docs/* are flushed.

15. ✅ **UserPromptSubmit intent → inline skill body** — `user-augment.py` detects intents (save / skillify / reflect / bootstrap; English + Vietnamese) and injects FULL skill instructions inline. Claude executes the skill behavior without `/mem-*` slash command.

Note on MemPalace storage: their plugin DOES use ChromaDB embeddings under the hood (verified from `mempalace/searcher.py` + plugin manifest keywords `chromadb`). What's distinctive is they store **verbatim text** with embeddings as the index — no summarization/paraphrasing. The "no manual skill" pattern was the actual lesson worth porting.

### Tier 4 — out of scope

12. RAPTOR / GraphRAG / HippoRAG — handled by claude-obsidian's wiki-fold + lint, or future plugin.
13. AutoCompressor / gist tokens — needs custom model, defer.
14. ColBERT / ColPali — overkill for markdown vault.

## G. References (verified URLs)

Memory systems: mem0ai/mem0, letta-ai/letta, getzep/zep, topoteretes/cognee, VectorSpaceLab/MemoRAG, OSU-NLP-Group/HippoRAG, joonspk-research/generative_agents, MineDojo/Voyager, noahshinn024/reflexion, langchain-ai/langgraph, run-llama/llama_index.

Retrieval: stanford-futuredata/ColBERT, illuin-tech/colpali, microsoft/graphrag, HKUDS/LightRAG, BAAI/bge-reranker-base, anthropic-cookbook (contextual retrieval).

Token efficiency: zilliztech/GPTCache, microsoft/LLMLingua, princeton-nlp/AutoCompressors, asg017/sqlite-vec.

PKM: zettelkasten.de, smartconnections.app, andymatuschak.org/evergreen, antigravity.codes/blog/karpathy-llm-wiki-idea-file.
