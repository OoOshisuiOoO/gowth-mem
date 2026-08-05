# Zero-Mem Retrieval Repair (v4.3) — Design Spec

Date: 2026-08-05 · Status: approved by user · Source: arXiv 2607.29377v1
("Zero-Mem: Zero-Token Memory Operations for LLM Agents", Xiao et al., 31 Jul 2026)

## Problem

The paper's thesis — every memory operation outside final question answering should cost
zero LLM tokens — is gowth-mem's own priority list (data safety > recall quality > token
efficiency > simplicity). Auditing the repo against it found that the deterministic
machinery gowth-mem already has is **not working**. Measured on the live vault
(15,145 chunks, 5 workspaces, 2026-05-06 → 2026-08-05):

| ID | Defect | Evidence |
|---|---|---|
| D1 | `/mem-recall` returns **0 hits for any natural-language query** — FTS5 treats bare terms as implicit AND | `why did we drop vector recall` → 0 hits; same terms OR-joined → 991 hits |
| D2 | Hyphen/colon queries raise `OperationalError`, swallowed by `except: return []` so the user sees "(no results)" | `vector-recall` → `no such column: recall`; `forget: ttl` → `no such column: forget` |
| D3 | `_extract_tag` reads only `content`, but `split_chunks` lifts `## [decision] …` into `heading` | **4,714** chunks carry a `[tag]` heading; only **54** of 15,145 have `tag` set |
| D4 | Workspace filter runs in Python **after** the SQL `LIMIT` | `--ws personal --limit 20 "python"` returns nothing though 3 personal chunks match |
| D5 | `append_entry` never touches the index — a just-written entry is unrecallable until manual `/mem-reindex` | live index.db 5 days stale |
| D6 | **27% of the index is dead**: 4,031 chunk rows across 237 paths point at files `_forget.py` deleted | `_index.py` GCs vanished paths only under `--full` |
| D7 | 272 archived `.gz` never indexed → no archive recall; index.db is currently their *only* searchable copy | `indexed paths under .archive/: 0` |
| D8 | Bootstrap loads **2 of 5 files and drops `docs/handoff.md`**: `MAX_TOTAL = 15_000` while `shared/secrets.md` (13,520 B, "pointers only") eats 90% of the cap | `[bootstrap: loaded 2/5 files, 15000 chars / 15000 cap]`; ~124,500 tokens/day |
| D9 | Six commands target the pre-v2.7 `$PWD/docs/` layout. `/mem-journal` **writes memory outside the vault** (unsynced, unindexed); `/mem-cost` measures 0 of 9 files — which is *why* D8 went unnoticed | `commands/mem-journal.md:10`; `/mem-cost` run verbatim |

D3 is the root cause of the inert `bm25(chunks_fts, 5.0, 3.0, 1.0)` weighting: the 5.0
tag boost applies to 0.4% of the corpus.

## What we adopt from the paper, and what we reject

Adopted — the deterministic **fuse → close → calibrate tail** (eq 12–16) and the query
profile (eq 6), because they are pure arithmetic over an index:

- `φ(q) = {subject, keywords, answer-type, temporal-cues, boundary}` (eq 6) — the
  principled fix for D1/D2: build the FTS5 expression from a parsed profile, never from
  raw user text.
- Evidence closure `C(q) = Dedup(M(q) ∪ N_g(M(q)) ∪ N_h(M(q)))` (eq 14) — `N_h` = sibling
  chunks in the same file; `N_g` = 1-hop relational substitute (topic-folder
  co-membership + `00-README` MOC + `slugs.parents/aliases`). Worth +4.17 F1 in the
  paper's ablation.
- Deterministic calibration (eq 15) — answer-type × tag match, temporal recency,
  layer weighting. Worth +1.94 F1. Adopted as **soft down-weights**, never the paper's
  hard `Filter` that deletes candidates.
- Provenance: "all units inherit provenance from their underlying raw traces" (eq 5).

**Rejected: the entity-context graph + Personalized PageRank (eq 3–4, 8–10), the paper's
largest ablation delta (−17.19 F1 when removed).** Three grounds:

1. **Measured, on this corpus.** A working stdlib prototype (regex identifier harvest as
   the spaCy substitute; 0.70 s build over 15,145 chunks; frontier-limited PPR at
   16–39 ms/query, γ = 0.6) was ablated on 5 real queries:

   | Variant | MRR | ranks |
   |---|---|---|
   | BM25 + per-path collapse | **1.000** | 1,1,1,1,1 |
   | + calibration | **1.000** | 1,1,1,1,1 |
   | + closure (no graph) | **1.000** | 1,1,1,1,1 |
   | + graph secondary ρ=0.4 | 0.900 | 2,1,1,1,1 |
   | + graph primary ρ=0.6 | 0.767 | 2,1,3,1,1 |

   The graph **hurts at every ρ**. Everything initially credited to it came from
   **per-path collapse** (deduplicating one file appearing 3× in results). Caveat: the
   metric saturates, so it cannot separate the top three variants — it only establishes
   that the graph earns nothing here.
2. **Dependency.** `V_e` needs spaCy NER and `η₀ = cos(e, ê)` needs BGE-M3 — both violate
   the zero-pip runtime rule. The stdlib substitute changes what a node *means*, and the
   paper offers no evidence for quality under that substitution.
3. **Transfer.** The paper attributes the graph's dominance to "HotpotQA's emphasis on
   relational and cross-document reasoning", and Zero-Mem's **only loss anywhere** is
   LoCoMo multi-hop (41.61 vs GAM 42.29) — exactly the conversational regime gowth-mem
   occupies.

Honest loss: no multi-hop bridging to evidence sharing no surface overlap with the query.

Also rejected: min-max dual-view fusion (nothing to fuse once the graph is gone — the
layer/recency signal is simpler as calibration multipliers); the hard `Filter`;
post-reader answer calibration (assumes a short extractable answer); the "0 memory-op
tokens" figure as a target metric (an accounting artifact); syncing `.audit/` prune
previews (violates "never sync real secret values").

## Design

Two tiers. Tier 1 is verified defects with small diffs; Tier 2 is additive and measured
only after Tier 1 lands.

### Tier 1 — repair what exists

| Task | Change | Files |
|---|---|---|
| T1 | `_extract_tag(heading) or _extract_tag(content)`; return `heading` from recall rows | `_index.py`, `_query.py` |
| T2 | Workspace predicate moves into SQL (`path LIKE 'workspaces/'||?||'/%'` / `'shared/%'`) so it filters *before* `LIMIT` | `_query.py` |
| T3 | `_profile.py` (new): `profile(query)` → φ(q); `fts_match(profile)` → safe OR-joined quoted expression. `query_ex()` (new sibling) returns `{"hits", "error"}`; `query_by_type` keeps its `list[dict]` contract | `_profile.py`, `_query.py` |
| T4 | `reindex_paths(paths)` in `_index.py`, wired into `append_entry`/`append_lesson`, try/except-pass | `_index.py`, `_topic.py`, `_lesson.py` |
| T5 | Bootstrap: `MAX_PER_FILE` + an explicit **reserved floor** for `docs/handoff.md` and workspace `AGENTS.md`, loaded before large static files. `MAX_TOTAL` stays 15,000 | `bootstrap-load.py` |
| T6 | Repoint the 6 dead-layout commands at `~/.gowth-mem/workspaces/<ws>/` via `_home.py` | `commands/mem-{journal,cost,distill,reflect,promote,skillify}.md` |

`query_by_type`'s signature and return type are unchanged — 21 tests in
`tests/test_query_by_type.py` assert `list[dict]` and it is documented public API.

### Tier 2 — additive, sequenced

| Task | Change | Gate |
|---|---|---|
| T7 | `.archive/manifest.jsonl` + index archived `.gz` at a low layer weight; never bootstrap-loaded | must land **before** T8 |
| T8 | `sweep_orphans()` — `chunks_fts` rowids deleted **before** `chunks` rows (external-content table); `--dry-run` default; prints counts | after T7 |
| T9 | Closure `N_h` (limit 1) + per-path collapse; rows labelled `support`/`primary` so `--type` never returns untagged prose as a typed entry; `N_g` default **off** | measure after T1 whether returning `heading` already fixes interpretability |
| T10 | Calibration multipliers (type match 1.35, MOC 1.20, `journal/sessions` 0.6, temporal decay) behind `settings.retrieval.*` | after T9 |
| T11 | `source=` on `append_entry`/`append_lesson` → `(from journal/sessions/<date>-<sid>.md#turn-N)`. The trailer **must be stripped before hashing** — it lands in the indexed chunk and would otherwise silently re-break `sha1(strip_tags_text(content))` dedup | independent |
| T12 | `conflict-detect` once-per-session suppression via `state.json` (~16,800 tokens/day while a conflict is open) | independent |

### Data safety

- Every schema touch is idempotent under `file_lock("index-migrate")`, the pattern
  `_migrate_tag_column` already uses. index.db is gitignored and rebuildable, so the
  migration's worst case is a full rebuild — but the migration must **self-refill**, not
  delegate to a command a user must remember.
- No curated content is mutated. No change to `_forget.py` TTLs, `_gate.py`, or the
  synced vault's git behaviour.
- T8 is the only deletion; it is gated, dry-run by default, and sequenced after T7 so it
  cannot destroy the only searchable copy of archived logs.

### Testing

TDD per task. New: `tests/test_profile.py`, `test_query_closure.py`,
`test_index_freshness.py`, `test_bootstrap_budget.py`. Full suite + `py_compile` must
stay green; `bin/test-install.sh` for anything touching hooks. Per CLAUDE.md's pre-tag
rule, every new path is exercised once against the real vault before release.

## Out of scope

Hierarchical trace-unit schema columns (`layer`/`unit`/`ord`/`ts`), min-max fusion,
Stop-hook template slimming (changes what the model *writes* — its own release behind a
settings flag), and the entity graph. The in-flight `feat/self-improver-v4.2` branch is
untouched; no file overlap.
