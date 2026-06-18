---
type: research
slug: data-quality-2026
title: Data Quality Canon for gowth-mem (2025-2026 convergent practice)
created: 2026-05-18
last_touched: 2026-05-18
sources:
  - perplexity_deep_research 2026-05-18 (mem0, Zep, Letta, A-MEM, HippoRAG, Cognee, LangMem)
  - gemini_deep_research 2026-05-18 ("LLM Memory Hygiene Systems Survey", report id r_5c70ec0f741ac48d)
  - .claude/research/v3.4-{brain-memory,llm-memory-systems,hook-patterns}.md
status: active
---

# Data Quality Canon

Single source of truth for "what's a keepable memory entry vs trash" across all
gowth-mem workspaces. AGENTS.md cites this file; hooks (`_prune.py`, `_lint.py`,
`_dedup.py`, `_consolidate.py`) implement these thresholds. **Do not soften
without a research note showing the new threshold is at least as safe.**

> **v3.9 provenance layer**: for the verified/unverified distinction (`[ref]` vs `[hypothesis]`)
> and the goal-tracking layer (`[goal]`), see `shared/research/provenance-2026.md`.

## 0. Why this exists

> Memory is not a retrieval problem — it is a lifecycle problem.
> Unrestricted accumulation → context rot, stacked contradictions, retrieval drift.
> (Gemini DR, 2026-05-18)

Five convergent rules across mem0 / Zep / Letta-MemGPT / A-MEM / HippoRAG /
Cognee / LangMem / MemoryOS:

1. Extract only **specific, durable, user-relevant** facts. Drop hedged or transient.
2. **Canonicalize per entity/topic**. Merge paraphrases; never store the same fact twice.
3. **ADD / UPDATE / DELETE / NOOP** write semantics — never blind append.
4. **Tiered retention**: working (context budget) · episodic (recency decay) ·
   semantic (until contradicted) · procedural (longest).
5. **Multi-signal scoring at retrieval**: BM25 + vector + recency + frequency + reinforcement.

## 1. Hard write-time gates (DROP rules)

Any entry that fails ANY of these is REJECTED at write time (`_topic.append_entry`,
`_lesson.append_lesson`, auto-journal protocol step 4).

| Rule | Threshold | Source |
|---|---|---|
| Min body length | `< 20 chars` after stripping prefix → DROP | gowth-mem 3.4 baseline |
| Hedge-only language | regex `\b(maybe|I think|probably|might be|seems like|kinda)\b` AND no `Source:` / no `Tried:`/`Fix:` → DROP | LangMem importance gate |
| `[ref]` without `Source:` | DROP — every verified fact must cite | shared/AGENTS.md §14, Zep fact rating |
| `[decision]` without rationale | DROP if no `because/since/rationale/why:` clause | Voyager critic gate |
| `[exp]` without specific cause | DROP if matches `/tried (things|stuff|some)/i` and no concrete attempt | mem0 specificity rule |
| `[tool]` without version + working syntax | DROP if no `version:` AND no fenced command | shared/tools.md schema |
| `[secret-ref]` with raw value | DROP and alert — pointer-only contract (env-var name or path) | shared/AGENTS.md §14 |
| `[hypothesis]` without `Verify:` path | DROP — unverified claim must name its confirmation/falsification path | v3.9 provenance-2026 |
| `[goal]` without `Status:` / success criterion | DROP — vague wish without `Done when:` is not a goal | v3.9 provenance-2026 |
| Hedge ratio | hedge words / total words > 0.25 → DROP | derived from LangMem importance |
| Reserved slug as topic | `docs / journal / skills / research / readme / lessons / 00-readme` → DROP | v3 layout invariant |
| Slug regex | `^[a-z0-9][a-z0-9-]{0,59}$` → DROP otherwise | v3 layout invariant |

### 1a. Secret-pattern pre-sweep (BLOCK write)

Before any write to `~/.gowth-mem/`, content matching any of these regexes is
either redacted via `_privacy.sanitize` OR the write is rejected with
`secret_leak_block`:

```
AKIA[A-Z0-9]{16}                       # AWS access key
sk-[A-Za-z0-9]{32,}                    # OpenAI / generic SK
ghp_[A-Za-z0-9]{30,}                   # GitHub personal token
gho_[A-Za-z0-9]{30,}                   # GitHub OAuth token
ghu_[A-Za-z0-9]{30,}                   # GitHub user token
xox[bpsa]-[A-Za-z0-9-]{10,}            # Slack
-----BEGIN [A-Z ]*PRIVATE KEY-----     # PEM/PGP key
[A-Za-z0-9_-]{40,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}   # JWT
```

Migration verify-by-hash must run AFTER sanitize (post-sanitize-stable hash) —
otherwise verify_fail false-positives on AWS docs example keys etc. See
`workspaces/personal/akiaiosfodnn7example-placeholder/`.

## 2. Numeric thresholds (consolidation, dedup, contradiction)

| Threshold | Value | Used by | Source |
|---|---|---|---|
| Within-file Jaccard duplicate | `≥ 0.85` → keep first/longer, DELETE rest | `_prune.py` | gowth-mem v2.6 (matches Mem0 0.85-0.90) |
| Cross-file semantic near-dup (sqlite-vec) | cosine `> 0.92` → merge candidate | `_consolidate.py` REM | Mem0 / Letta sleep-time defrag |
| Contradiction keyword overlap | `≥ 0.4` AND polarity mismatch → FLAG | `_lint.py` / `_contradict.py` | Zep temporal-edge invalidation |
| Char-trigram Jaccard fuzzy fallback | `≥ 0.6` → wikilink resolve | `_wikilink.py` | v3.2 deterministic-only path |
| Topic-folder soft max | `15-25` aspect files per topic | `_dream.py` Deep | Letta defragmentation target |
| Topic-folder hard split | aggregate `> 800 lines` → `/mem-promote` | `_promote.py` | shared/AGENTS.md §8 |
| Aspect-file hard size | `> 400 lines` → `/mem-promote` warning | shared/AGENTS.md §8 |
| Fact poignancy (manual, optional) | scale `1-5`; only `≥ 3` survives to `[decision]/[ref]` | docs-side | Zep `fact_rating` 1-5 |

## 3. Retention TTL by 9-type schema

Maps gowth-mem's 9 prefixes onto the working / episodic / semantic / procedural taxonomy:

| Prefix | Memory class | Default TTL | Decay rule | Delete trigger |
|---|---|---|---|---|
| `[goal]` | working/reflective | ∞ | none | mark achieved/abandoned/superseded — never delete |
| `[secret-ref]` | resource pointer | ∞ until rotated | none | `(rotated)` marker OR `valid_until:` past |
| `[ref]` | semantic | ∞ until contradicted | none | superseded marker OR `_lint.py` confirms contradiction |
| `[decision]` | semantic + procedural | ∞ | none | immutable — mark `(superseded)` instead of delete (audit trail) |
| `[skill-ref]` | procedural | ∞ | none | source skill removed/deprecated |
| `[reflection]` | semantic (meta) | 180 days unless cross-linked ≥ 2× | linear after 90d | low layer_score + untouched 180d |
| `[exp]` | episodic | 90 days unless reinforced | Ebbinghaus | recall_count < 2 AND age > 90d |
| `[hypothesis]` | episodic-staging | 30 days | linear | refuted OR (age > 30d AND never promoted to `[ref]`) |
| `[tool]` | semantic (versioned) | until version deprecated | none | `valid_until:` past OR version in DEPRECATED list |
| journal raw | working | 7 days raw, then distill | hard cutoff | after `/mem-distill` succeeds |

### 3a. Decay formula (Ebbinghaus, LangMem-style)

```
strength = importance × exp(-Δdays / retention_factor)
retention_factor = 30 (days)
floor            = 0.1   (never fully forgotten if importance ≥ 0.5)
on recall        → reset Δdays = 0  (spaced-repetition reinforcement)
```

`importance` source: `[goal]=1.0`, `[decision]=1.0`, `[ref]=0.9`, `[skill-ref]=0.9`,
`[reflection]=0.7`, `[tool]=0.7`, `[exp]=0.5`, `[hypothesis]=0.4`, `[secret-ref]=1.0`.

## 4. Multi-signal recall score (deterministic-only — no LLM in path)

```
R = w_bm25·BM25_norm + w_layer·layer_score + w_recency·recency + w_div·diversity + w_reinf·log(1+recall_count)
  w_bm25=0.30  w_layer=0.30  w_recency=0.15  w_div=0.15  w_reinf=0.10
```

Where `layer_score` buckets (from v3.0 F16):

| Layer | Score |
|---|---|
| today's journal/aspect | 90 |
| topic MOC (`00-README.md`) | 80 |
| topic lessons.md | 75 |
| older aspect file | 70 |
| research/ | 65 |
| shared/skills/ | 40 |

`recency = exp(-Δdays / 30)`, `diversity = MMR penalty over already-selected
hits`, `recall_count` from `state.json` (SRS).

## 5. Write semantics (mem0-style)

Every entry passes through `_topic.classify_intent`:

| Intent | When | Action |
|---|---|---|
| `ADD` | no existing similar entry (Jaccard < 0.85 AND cosine < 0.92) | append new |
| `UPDATE` | similar entry exists AND new info supersedes (date newer, source higher) | replace in place, append `(updated YYYY-MM-DD)` |
| `DELETE` | new entry contradicts existing AND polarity flip | mark old `(superseded)`, write new |
| `NOOP` | duplicate of existing AND no new info | skip silently |

Auto-journal protocol step 5 must call this classifier — no blind append.

## 6. Consolidation triggers (`/mem-dream` phases)

| Trigger | Phase | Action |
|---|---|---|
| File recalled `≥ 2×` across sessions | Light | gather as candidate |
| Candidate cluster Jaccard `≥ 0.3` | REM | group by keyword theme |
| Deep score `≥ 0.6` | Deep → promote | surface key entries for manual lift to docs/ref.md |
| Deep score `0.3-0.6` | Deep → maintain | keep, no action |
| Deep score `< 0.3` | Deep → prune candidate | feed to `/mem-prune` |

Run cadence: weekly OR after long session OR when `/mem-recall` returns noisy.

## 7. Contradiction handling (which model)

gowth-mem uses **semantic supersession** (mem0 style) for `[ref]/[tool]/[exp]`:
new entry wins, old marked `(superseded)`, then `_prune.py` deletes after audit
trail in git log.

For `[decision]` we use **temporal invalidation** (Zep style): old decision is
NEVER deleted, only marked `(superseded by: <new-slug>, YYYY-MM-DD)`. This
preserves "why we abandoned X" — critical for not re-litigating.

Detection runs in `_lint.py` (overlap ≥ 0.4 + polarity flip). Resolution is
human-mediated (intentionally not automatic — false-positive cost too high).

## 8. Anti-bloat invariants

- One workspace · one `_MAP.md` · one topic-folder per slug · one `lessons.md` per topic
- One canonical fact per topic; paraphrases merge via UPDATE not ADD
- No raw `<<<<<<<` / `=======` / `>>>>>>>` markers in any `.md` (breaks FTS5)
- `00-README.md` is AUTO-regenerated except `## Cross-links (manual)`
- Reserved subdirs (`docs/`, `journal/`, `skills/`, `research/`) NEVER reshaped
- Per-machine state (`config.json`, `state.json`, `index.db`, `.locks/`) NEVER
  committed (gitignored)

## 9. Quick rubric (≤ 60 sec mental check before writing)

```
1. Is it < 20 chars?                       → DROP
2. Is it hedged with no evidence?          → DROP
3. Does prefix match content?              → fix prefix
4. Is `Source:` present for [ref]?         → DROP if no
5. Is it a paraphrase of existing entry?   → UPDATE not ADD
6. Does it contradict an existing entry?   → mark old (superseded) first
7. Does it leak a secret pattern?          → BLOCK (privacy.sanitize)
8. Will it still matter in 90 days?        → if yes, persist; if no, journal only
```

## 10. References

- Perplexity deep research, 2026-05-18, "Convergent 2025-2026 data-quality criteria for LLM memory" — backend_uuid `5018670e-e50b-4adf-9a7b-610026d264af`
- Gemini deep research, 2026-05-18, "LLM Memory Hygiene Systems Survey" — conv `c_c794a1c50c0137cb`, response `r_5c70ec0f741ac48d`
- Zep docs: graph.add 10K char limit, query 8192 token truncate, fact_rating 1-5
- MemoryOS: heat threshold τ=5, weights α=β=γ=1 (arXiv:2603.07670)
- Voyager / Reflexion: 4-round iterative gate
- Ebbinghaus retention factor 30d, floor 0.1 (LangMem)
- Letta defragmentation: 15-25 focused files per topic
- gowth-mem v3.2 deterministic-only retrieval (no LLM in vector path)
