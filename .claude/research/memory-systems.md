# Memory Systems — Distilled Findings

Condensed from 12 systems studied in `RESEARCH.md`. Only actionable insights retained.

## What we adopted

| Source | What we took | Where it lives |
|---|---|---|
| **mem0** | ADD/UPDATE/DELETE/NOOP rewrite logic | `mem-distill` skill, `_prune.py` |
| **Letta/MemGPT** | 3-tier hierarchy (core/recall/archival) | shared/ (core) → workspaces/ docs (recall) → topics (archival) |
| **Generative Agents** | Importance × recency × relevance scoring | `recall-active.py` tier-score + SRS |
| **Voyager** | Skill library as procedural memory | `skills/<slug>.md` convention |
| **Reflexion** | Self-reflection → lessons buffer | `/mem-reflect` skill |
| **MemPalace** | Auto-trigger hooks (no manual skill invocation) | Stop/PreCompact/UserPromptSubmit hooks |
| **Anthropic memory tool** | File-based JIT load with pagination | `bootstrap-load.py` cap 12k/file, 60k total |
| **LangMem** | Explicit semantic/episodic/procedural taxonomy | 7-type schema prefixes |

## What we skipped and why

| Source | Pattern | Why skipped |
|---|---|---|
| **Zep/Graphiti** | Temporal knowledge graph with valid_at/invalid_at | Adopted `valid_until:` + `(superseded)` markers instead — simpler, no graph DB |
| **MemoRAG** | Long-context model as global memory | Requires custom model; hosted APIs don't expose KV cache |
| **HippoRAG** | OpenIE → knowledge graph → PageRank | Overkill for markdown vault; deferred to claude-obsidian wiki |
| **Cognee** | Triple store (graph + vector + relational) | Three-store complexity; SQLite FTS5+vec covers our needs |
| **GPTCache** | Semantic response cache | Stale-answer risk for evolving code work; limited ROI |
| **AutoCompressor/Gist tokens** | Learned prompt compression | Requires custom-trained model, not available on hosted APIs |
| **ColBERT/ColPali** | Late interaction / visual doc retrieval | Overkill for markdown files |
| **GraphRAG** | LLM-heavy community summaries | High offline cost; deferred to future plugin |

## 5 convergent patterns (across all 12)

1. **2-3 tier hierarchy** — every system separates hot (always-loaded) from cold (retrieved on demand)
2. **Async extraction** — heavyweight processing offline, lightweight retrieval online
3. **Semantic + episodic + procedural** — LangMem made this explicit; maps to our [ref]/[exp]/[skill-ref]
4. **ADD/UPDATE/DELETE** — mem0 and Generative Agents both converged on rewrite-over-append
5. **Multi-signal scoring** — no system uses single-signal retrieval; all combine 2+ signals
