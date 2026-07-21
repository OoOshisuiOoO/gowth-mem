# AGENTS.md (shared — cross-workspace)

Hard rules. Stable across workspaces. Per-workspace overrides:
`workspaces/<ws>/AGENTS.md`. Data-quality canon: `shared/research/data-quality-2026.md`.

## 1. Identity

Persistent memory layer for Claude Code. Single vault `~/.gowth-mem/`, synced via
git remote. 1 session = 1 active workspace. Knowledge stays in active ws unless
explicit cross-ws. Tự đọc state, tra `shared/secrets.md`, ghi kinh nghiệm.
Không hỏi lại thứ đã có trong docs.

## 2. Workspace resolve

First match wins: env `GOWTH_WORKSPACE` → `config.json.workspace_map` glob →
`config.json.active_workspace` → `"default"`. Switch: `/mem-workspace <name>`.

## 3. Bootstrap (SessionStart)

1. **Global**: `shared/AGENTS.md` → `shared/{files,secrets,tools}.md`.
   `shared/research/data-quality-2026.md` is gated (loaded on first write, not
   on bootstrap — token economy).
2. **Workspace**: `workspaces/<ws>/AGENTS.md` (override) → `<ws>/_MAP.md` →
   `<ws>/docs/handoff.md` → `<ws>/docs/{exp,ref,tools,files}.md` → top-3 topic
   `00-README.md` (by `frontmatter.last_touched`) → `<ws>/journal/{today,yesterday}.md`
   → `<ws>/skills/_index`.

v3 nudge: if `settings.layout_version < 3`, SessionStart prepends an upgrade
hint pointing at `/mem-migrate-v3`.

Output: **workspace=<ws> / đang làm gì / step kế / blocker**.

## 4. Layout (v3.0 — topic folder + dated aspect)

```
~/.gowth-mem/
├── shared/                   cross-workspace
│   ├── AGENTS.md  _MAP.md  files.md  secrets.md  tools.md
│   ├── research/             ★ canonical research notes (data-quality-2026.md, …)
│   └── skills/<slug>.md
└── workspaces/<ws>/
    ├── AGENTS.md             workspace-specific rules (slim — overrides only)
    ├── workspace.json  _MAP.md
    ├── docs/                 RESERVED — handoff/exp/ref/tools/files
    ├── journal/<date>.md     RESERVED
    ├── skills/<slug>.md      RESERVED
    ├── research/             RESERVED — long-form research output (v3+)
    ├── <slug>/               ★ TOPIC FOLDER (v3)
    │   ├── 00-README.md      ★ MOC — TL;DR + Aspects (auto) + Cross-links (manual)
    │   ├── YYYY-MM-DD-<aspect>.md   ★ dated aspect — multiple per topic per day
    │   └── lessons.md        ★ folder ledger (5-field schema)
    └── <domain>/             DOMAIN folder (no <domain>/<domain>.md)
        ├── _MAP.md
        └── <sub>/00-README.md   nested topic (≤3 levels)
```

`[[<slug>]]` resolves to `<somewhere>/<slug>/00-README.md` (v3), falling back to
v2.4 `<slug>/<slug>.md` and v2.3 flat `<slug>.md` (read-path only).

Reserved subdirs at workspace root (cannot be a topic slug):
`docs`, `journal`, `skills`, `research`.
Reserved files inside topic folders: `00-README.md`, `lessons.md`, `_MAP.md`,
`AGENTS.md`, `workspace.json`.
Reserved aspect names: `readme`, `lessons`, `00-readme`.

Default flat. ≥ 5 topics same domain → nest. Max 3 levels. `_MAP.md` and
`00-README.md` auto-generated; `## Cross-links (manual)` never overwritten.

## 5. Topic format (v3 = 3 file types inside `<slug>/`)

### 5a. 00-README.md — MOC (auto-regenerated except `## Cross-links (manual)`)

```markdown
---
slug: ema-cross               # unique within ws, kebab-case ≤60
title: EMA Cross Strategy
type: topic
status: draft|active|distilled|archived
maturity: experimental|stable|deprecated
created: 2026-05-02
last_touched: 2026-05-02
parents: [strategies, trend]
links: [rsi, breakout]
aliases: [ema-9-21]
tags: []
---
# EMA Cross Strategy

## TL;DR
> 1-2 lines core (this is what bootstrap reads — must compress the entire topic).

## Aspects (auto)
- 2026-05-04: [[ema-cross/2026-05-04-backtest|backtest]] — first preview line
- [[ema-cross/lessons|lessons]] — 5-field ledger

## Cross-links (manual)
- (preserved across rebuilds)
```

### 5b. `YYYY-MM-DD-<aspect>.md` — dated aspect (the actual content)

```markdown
---
slug: ema-cross-backtest
title: EMA Cross Strategy — Backtest
type: aspect
date: 2026-05-04
topic: ema-cross
aspect: backtest
status: draft
created: 2026-05-04
last_touched: 2026-05-04
links: []
tags: []
---

# Backtest
```

Body uses the 9-type schema:

```
## [goal]       ← user objective/intent — Status: + Done when: (verifiable)
## [decision]   ← architectural choice + rationale (why, alternatives rejected)
## [ref]        ← verified fact, Source: REQUIRED
## [tool]       ← tool quirks specific to this topic (version + working syntax)
## [hypothesis] ← UNVERIFIED claim — Verify: path REQUIRED (promote to [ref] when confirmed)
## [exp]        ← episodic, 1-2 lines, specific cause
## [reflection] ← pattern (weekly via /mem-reflect)
## [skill-ref]  ← link to skills/<slug>.md
## [secret-ref] ← env-var pointer only (NEVER value)
```

### 5c. `lessons.md` — per-topic ledger (5-field schema, see §6)

NEVER write entries into `00-README.md` directly. NEVER mix `[lesson]` with other
type prefixes inside dated aspect files (lessons have their own file).

## 6. `[lesson]` 5-field schema (`lessons.md` per topic folder)

```markdown
## <Symptom — observable error/behavior; becomes H2 for FTS5 prefix>
- **Tried**: <ordered attempts>
- **Root cause**: <1 line>
- **Fix**: <working command/patch>
- **Source**: <commit | file:line | URL>
- **When**: 2026-05-04
```

Append-at-top via `memL`. One `lessons.md` PER TOPIC FOLDER (not per dated aspect).

## 7. Data quality criteria (canonical — read this before every write)

Full canon: `shared/research/data-quality-2026.md`. Inline summary follows.

### 7-LANG. Language policy (enforced by `_gate.py` when `settings.gate.english_only=true`)

- **Curated data is stored in ENGLISH** — every `[type]` entry, lessons.md,
  docs/{exp,ref,tools}.md, MOC TL;DRs, skills. The gate REJECTS curated
  writes with >2 Vietnamese diacritic chars (`not_english`).
- Raw journal / precompact dumps may stay bilingual (ephemeral buffer, TTL 7d).
- Legacy Vietnamese files: translate-on-touch — whenever you edit/consolidate
  a file, rewrite the touched entries in English. Never mass-translate blindly;
  dense domain notes must keep exact meaning (numbers, tickers, params verbatim).
- Conversation language with the user stays Vietnamese — this policy is about
  STORAGE only (English compresses better, embeds better, and greps uniformly).

### 7a. MUST-haves per type

| Prefix | MUST | NEVER |
|---|---|---|
| `[goal]` | `Status:` (active/paused/achieved/abandoned/blocked/superseded) + verifiable `Done when:` ; `Motivated-by:` links | vague wish, no done-when |
| `[ref]` | `Source:` (URL / file:line / commit) | hedged language ("maybe", "I think") |
| `[decision]` | rationale + alternative considered | silent reversal — mark old `(superseded)` |
| `[exp]` | specific cause (what failed, why) | "tried things and it worked" |
| `[tool]` | `version:` + fenced working syntax | speculative / untested commands |
| `[hypothesis]` | `Verify:` confirmation/falsification path | stating it as fact — use `[ref]`+`Source:` instead |
| `[reflection]` | pattern seen ≥ 2× with examples | one-off opinion |
| `[skill-ref]` | path to `<ws>/skills/<slug>.md` or `shared/skills/` | inline how-to (link instead) |
| `[secret-ref]` | env-var name OR file path + how to obtain | the actual value (EVER) |

### 7b. Write-time DROP rules (enforced by `_topic.append_entry` + auto-journal step 4)

- Body `< 20 chars` after prefix → DROP
- Hedge-only (`maybe / I think / probably / might be / seems like`) without
  `Source:` / `Tried:` / `Fix:` → DROP
- `[ref]` without `Source:` → DROP
- `[decision]` without rationale clause → DROP
- `[tool]` without version and working syntax → DROP
- `[hypothesis]` without a `Verify:` path → DROP
- `[goal]` without `Status:` / success criterion → DROP
- Secret pattern match (AKIA / sk- / ghp_ / xox / PRIVATE KEY / JWT) → BLOCK
  (handled by `_privacy.sanitize`; migration verify must run post-sanitize)
- Reserved slug as topic (`docs/journal/skills/research/readme/lessons/00-readme`) → DROP
- Slug fails `^[a-z0-9][a-z0-9-]{0,59}$` → DROP

### 7c. Numeric thresholds

- Within-file Jaccard duplicate ≥ 0.85 → `_prune.py` deletes shorter
- Cross-file cosine similarity > 0.92 → `_consolidate.py` REM merge candidate
- Contradiction keyword overlap ≥ 0.4 + polarity flip → `_lint.py` flags
- Topic-folder soft max: 15-25 aspect files → `/mem-dream` Deep
- Topic-folder hard split: aggregate > 800 lines → `/mem-promote`
- Aspect file: > 400 lines → `/mem-promote` warning

### 7d. Write semantics (mem0-style, never blind append)

```
ADD    no similar existing entry (Jaccard < 0.85 AND cosine < 0.92)
UPDATE similar entry + newer date / better source → replace in place
DELETE new entry contradicts old + polarity flip → mark old (superseded), write new
NOOP   duplicate, no new info → skip
```

`[decision]` uses temporal invalidation: never delete, mark `(superseded by:
<new-slug>, YYYY-MM-DD)` — preserves audit trail.

### 7e. Retention TTL (auto-applied by `_prune.py` + `/mem-dream`)

| Prefix | TTL | Delete trigger |
|---|---|---|
| `[goal]` | ∞ | mark achieved/abandoned/superseded (never delete) |
| `[ref]` / `[decision]` / `[skill-ref]` / `[secret-ref]` | ∞ | `(superseded)` / `(rotated)` / `valid_until:` past |
| `[reflection]` | 180d | low layer_score + untouched 180d |
| `[exp]` | 90d | recall_count < 2 AND age > 90d (Ebbinghaus floor 0.1) |
| `[hypothesis]` | 30d | refuted OR (age > 30d AND never promoted to `[ref]`) |
| `[tool]` | until version deprecated | `valid_until:` past OR DEPRECATED |
| journal raw | 7d | after `/mem-distill` succeeds |

## 8. Slug + wikilink scope (v3)

- Topic slug unique within workspace. 2 ws can reuse the same slug.
- Aspect slug unique within topic+date.
- `[[slug]]` → resolves in active ws via v3: `<base>/<slug>/00-README.md`.
  Falls back to v2.4 `<slug>/<slug>.md` then v2.3 flat (read-path only).
- `[[ws:slug]]` → cross-workspace explicit. `[[shared:secrets]]` → shared registry.
- `[[slug/aspect]]` or `[[slug/YYYY-MM-DD-aspect]]` → specific aspect.
- NEVER rename published topic slug (breaks wikilinks). Change `parents:` via
  `/mem-restructure` only.

## 9. Lifecycle & retention (>3 months → archive)

- `status`: `draft → active → distilled → archived`.
- Topic folder > 800 lines aggregate → `/mem-promote` split.
- **Aspects older than 90 days are auto-archived** (`_forget.py --aspects`,
  Stop-hook via `topic_layout.auto_archive_enabled`): curated `- [type]`
  blocks are salvaged into the topic's `lessons.md` (in English) FIRST, then
  the raw aspect is gzip-archived to `.archive/topics/` (+ git history —
  recoverable). Every topic always keeps its newest 3 aspects regardless of
  age; `00-README.md` and `lessons.md` are never archived.
- After any archive pass: regen MOCs (`/mem-topic --regen-index`) and clean
  junk — empty husk topics (00-README-only, TL;DR TODO) are DELETED outright.
- Workspace 6 months untouched → `/mem-workspace-archive`.

## 10. Recall scope

- Default `recall.cross_workspace=false`: search active ws + `shared/`.
- Wikilink follow: 1 hop default.
- Skip lines `(superseded)` / expired `valid_until:`.
- Recall score (deterministic, no LLM):
  `R = 0.30·BM25 + 0.30·layer + 0.15·recency + 0.15·diversity + 0.10·log(1+recall_count)`

## 11. Workflow

1. Log raw → `journal/<today>.md` (`memj`).
2. Repeating ≥ 2× → `memk` (skillify).
3. Bug/surprise → `memL <symptom> -- <tried> -- <root> -- <fix>`.
4. PreCompact → distill journal → topic entries (auto-journal classifier).
5. PostCompact → auto-sync pull-rebase-push.
6. Weekly → `memr` (1-3 reflections) + `/mem-dream` + `/mem-prune` + `/mem-lint`.
7. Research-first: no evidence → no implement. `[ref]` must have `Source:`.
8. Tools-first: tra `shared/tools.md` + `<ws>/docs/tools.md` trước khi tự code.
9. Verify before claim: no log/screenshot/test → no "done".
10. **Close the loop**: every work block ENDS with an explicit closure turn —
    done / left / failed+next — and NEVER on an unrecovered failure. Diagnosis
    without shipping = unfinished.
11. **Check cheap preconditions before confident verdicts**: before declaring
    'verified' / 'recovered' / 'no permission', check which host, which remote
    (full list, never `| head`), which identity/secret, and whether every
    flagged trigger is neutralized. A verdict on an unchecked precondition = a guess.

## 12. Shortcuts

| Alias | Skill | | Alias | Skill |
|---|---|---|---|---|
| `mems` | mem-save | | `memx` | mem-reindex |
| `memd` | mem-distill | | `memc` | mem-cost |
| `memr` | mem-reflect | | `memp` | mem-prune |
| `memk` | mem-skillify | | `memy` | mem-sync |
| `memb` | mem-bootstrap | | `memg` | mem-config |
| `memm` | mem-migrate-global | | `memT` | mem-topic |
| `memj` | mem-journal | | `memI` | mem-install |
| `memL` | mem-lesson | | `memC` | mem-sync-resolve |

Capital-suffix is case-sensitive.

## 13. Auto-sync

```
SessionStart  → auto-sync.py --pull-only
PreCompact    → auto-sync.py --commit-only
PostCompact   → auto-sync.py --pull-rebase-push
```

`SYNC-CONFLICT.md` exists → mỗi prompt nhắc `/mem-sync-resolve`. KHÔNG sửa
markers tay (raw `<<<<<<<` markers break FTS5).

## 14. Guardrails

- KHÔNG commit secret values — `secrets.md` is pointer-only (env-var names).
- KHÔNG skip bootstrap.
- KHÔNG giữ entry mâu thuẫn — new wins → DELETE old (or `(superseded)`).
- KHÔNG promote `[ref]` không có `Source:`.
- KHÔNG sửa `SYNC-CONFLICT.md` markers tay.
- KHÔNG nest > 3 levels. KHÔNG đổi slug đã publish.
- KHÔNG ghi ws A knowledge vào ws B without cross declare.
- KHÔNG blind append — always classify ADD/UPDATE/DELETE/NOOP.
- KHÔNG soften §7 thresholds without a research note in `shared/research/`.
- Mỗi knowledge update → commit `knowledge(<ws>/<slug>): mô tả`.
