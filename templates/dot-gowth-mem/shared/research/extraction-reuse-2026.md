---
type: research
slug: extraction-reuse-2026
title: Extraction & Reuse Canon — how to store memory so it's reusable, and hard rules that block junk
created: 2026-06-18
last_touched: 2026-06-18
sources:
  - gemini_deep_research 2026-06-18 (conv c_742be0a1ce2e1d61 + c_31a0d05a7bdf8421) — B-TAZ architecture, extraction-for-reuse, poignancy/cosine matrix
  - perplexity_deep_research 2026-06-18 (backend f4b8784b) — ingestion gates: mem0 confidence, Zep fact-rating, LangMem, Letta
  - perplexity_ask 2026-06-18 (955b213d) — "Git Hippocampus": 12 deterministic hard write-rules
status: active
---

# Extraction & Reuse Canon

Companion to `data-quality-2026.md`. That file = **what to keep**. This file =
**how to write it so a future session can actually reuse it**, and the **hard
rules that reject junk at write time** (`_gate.py` enforces the deterministic subset).

> A memory is only worth storing if a *future, different* session can recall it and
> apply it. The split that matters: an **episodic record** answers "what happened";
> a **semantic rule** answers "what rule applies here". Never promote the raw
> fail-chain into semantic memory — distill the invariant. (Gemini DR 2026-06-18)

## 1. The lifecycle (capture → extract → consolidate → forget)

| Stage | When | What | gowth-mem |
|---|---|---|---|
| Capture | per event | append raw to journal (ephemeral buffer) | `precompact-flush` / `memj` |
| Extract | session end / debounce ~30s idle | raw → atomic, self-contained, reusable entry | `/mem-distill` |
| Consolidate | weekly | merge near-dups, promote repeated episodics, link | `/mem-dream` |
| Forget | daily/weekly | archive raw past 7d TTL, decay stale | `_forget.py` / `/mem-forget` |

Extraction runs **offline** (not mid-task) so it sees a *complete* cognitive loop
(error → tried → fix), not half-formed utterances. (LangMem debounce; Gemini DR.)

## 2. What makes an entry REUSABLE (write these, or it's dead weight)

1. **Self-contained** — resolve every pronoun and relative time. ❌ "it broke when the
   server restarted" → ✅ "the auth-service readiness probe fails on cold start because
   the DB pool warms lazily". No "this/that/yesterday" without a referent.
2. **Canonical phrasing** — one normalized claim, present tense, explicit subject.
3. **Trigger / when-to-apply** — the lexical cues that should surface it later
   (error class, filename, command, dependency, symptom). This is the deterministic
   substitute for embeddings. Put them in the text and in `tags:`.
4. **Provenance** — `Source:` (file:line / commit / URL / command). No provenance →
   not reusable (can't be re-verified) → rejected for `[ref]`.
5. **Validity** — `valid_until:` for anything time-bound; mark superseded, don't silently edit.
6. **Episodic vs semantic** — a one-off fix stays `[exp]` in `lessons.md`; only a rule
   seen ≥2× or an explicit decision is promoted to a `[ref]`/`[decision]` semantic entry.

## 3. HARD write-time rules (deny-by-default) — `_gate.py` enforces the ★ deterministic ones

| # | Rule | Reject when | ★ code |
|---|---|---|---|
| 1 | Secret leak | matches AKIA / `sk-` / `ghp_`/`gho_`/`ghu_` / `xox[bpsa]-` / JWT / PEM | ★ |
| 2 | Empty / placeholder | blank, or body is `todo/tbd/fixme/misc/random/stuff/wip/...` | ★ |
| 3 | Too short | body < 20 chars (< ~5 tokens) after stripping `[tag]` | ★ |
| 4 | Hedged, no evidence | hedge words (`maybe/i think/probably/seems/might`) AND no Source/`code`/path/URL | ★ |
| 5 | `[ref]` without Source | no `Source:` and no URL | ★ |
| 6 | `[decision]` without rationale | no `because/since/so that/rationale/why/vì/để` | ★ |
| 7 | `[tool]` without version+syntax | no `version:`/`vN.N` AND no `` `command` `` | ★ |
| 8 | `[secret-ref]` with raw value | pointer-only contract — env-var name or path, never the value | ★ |
| 9 | Multi-claim blob | >1 independent fact / >~40 tokens with multiple predicates → **split first** | agent |
| 10 | Low durability | transient ("timeout is now 3") vs durable ("service needs backoff because rate-limited") | agent |
| 11 | Low poignancy | importance < 0.40 (chit-chat, failed-attempt-only, one-off) → not semantic | agent |
| 12 | Reserved/format | bad slug, reserved name, malformed frontmatter | ★ (slug) |

★ = enforced deterministically in `_gate.py` at `_topic.append_entry` / `_lesson.append_lesson`.
The rest are agent discipline (need an LLM judgment: poignancy, durability, splitting).
Run `python3 _gate.py --scan --all` to find existing entries that violate the ★ rules.

## 4. Mutation decision matrix (ADD / MERGE / DEDUP / SUPERSEDE / NOOP)

After gates pass, compare the candidate to existing entries:

| Similarity | Same claim? | Action |
|---|---|---|
| Jaccard < 0.85 AND cosine < 0.75 | — | **ADD** new entry |
| 0.75 ≤ cosine ≤ 0.91 | adds nuance | **MERGE** — rewrite existing to absorb the new detail |
| cosine ≥ 0.92 | identical | **NOOP / DEDUP** — drop; (optionally append provenance) |
| cosine ≥ 0.92 | contradicts | **SUPERSEDE** — mark old `(superseded YYYY-MM-DD)`, write new (`[decision]` never hard-deleted) |

Thresholds: Jaccard 0.85, cosine 0.92 (= `data-quality-2026.md` §2). Deterministic
path uses Jaccard + tag-aware SHA-1 dedup (`_dedup.py`); cosine only when sqlite-vec present.

## 5. Reusable metadata (frontmatter fields that earn their keep)

`slug, type, status, last_touched` (always) · `tags:` (entity/trigger cues) ·
`Source:` inline (provenance) · `valid_until:` (temporal) · `(superseded by: …)` markers.
Heavyweight fields from research (uuid, poignancy_score, trigger_conditions, provenance_edges,
valid_at/invalid_at) are the **B-TAZ** ideal; gowth-mem approximates them deterministically
with tags + Source + valid_until + git history (the provenance edge).

## 6. The target architecture (named)

Deep research (Gemini, 2026-06-18) converges on **Bi-Temporal Agentic Zettelkasten (B-TAZ)**:
A-MEM atomic-note graph + Zep bi-temporal supersession + LangMem debounced async extraction
+ Letta agentic self-edit. gowth-mem is a **deterministic, markdown-in-git** realization:
Zettelkasten = topic-folder + dated aspects; bi-temporal = `valid_until`/`(superseded)` +
git history; debounce = Stop-hook every-N-turns + `/mem-distill`; agentic = the agent writes
via `memL`/`memj` under these gates. The gap vs full B-TAZ (no embeddings, no LLM in write
path) is the deliberate price of determinism + zero runtime deps.
