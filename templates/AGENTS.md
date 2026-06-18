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

1. **Global**: `shared/AGENTS.md` → `shared/{files,secrets,tools}.md` →
   `shared/research/data-quality-2026.md` (gated read: bootstrap skips for token
   economy; hooks load when about to write).
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
    ├── research/             RESERVED — long-form research output
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

Reserved subdirs at workspace root: `docs`, `journal`, `skills`, `research`.
Reserved files inside topic folders: `00-README.md`, `lessons.md`, `_MAP.md`,
`AGENTS.md`, `workspace.json`.
Reserved aspect names: `readme`, `lessons`, `00-readme`.

Default flat. ≥ 5 topics same domain → nest. Max 3 levels. `_MAP.md` and
`00-README.md` auto-generated; `## Cross-links (manual)` never overwritten.

## 5. Topic format (v3 = 3 file types inside `<slug>/`)

### 5a. 00-README.md — MOC (auto-regenerated except `## Cross-links (manual)`)

Frontmatter: `slug / title / type:topic / status / maturity / created /
last_touched / parents / links / aliases / tags`. Body sections: `TL;DR`,
`Aspects (auto)`, `Cross-links (manual)`.

### 5b. `YYYY-MM-DD-<aspect>.md` — dated aspect (the actual content)

Frontmatter: `slug / title / type:aspect / date / topic / aspect / status /
created / last_touched / links / tags`. Body uses the 9-type schema:

```
## [goal]       user objective/intent — Status: + Done when: (verifiable)
## [decision]   choice + rationale + alternatives rejected
## [ref]        verified fact, Source: REQUIRED
## [tool]       tool quirks for this topic (version + working syntax)
## [hypothesis] UNVERIFIED claim — Verify: path REQUIRED (promote to [ref] when confirmed)
## [exp]        episodic, 1-2 lines, specific cause
## [reflection] pattern (weekly via /mem-reflect)
## [skill-ref]  link to skills/<slug>.md
## [secret-ref] env-var pointer only (NEVER value)
```

### 5c. `lessons.md` — per-topic ledger (5-field schema, see §6)

NEVER write entries into `00-README.md` directly. NEVER mix `[lesson]` with other
type prefixes inside dated aspect files.

## 6. `[lesson]` 5-field schema

```markdown
## <Symptom — observable error/behavior; becomes H2 for FTS5 prefix>
- **Tried**: <ordered attempts>
- **Root cause**: <1 line>
- **Fix**: <working command/patch>
- **Source**: <commit | file:line | URL>
- **When**: YYYY-MM-DD
```

Append-at-top via `memL`. One `lessons.md` PER TOPIC FOLDER.

## 7. Data quality criteria

Full canon: `shared/research/data-quality-2026.md`. Inline summary follows.

### 7a. MUST-haves per type

| Prefix | MUST | NEVER |
|---|---|---|
| `[goal]` | `Status:` (active/paused/achieved/abandoned/blocked/superseded) + verifiable `Done when:` ; `Motivated-by:` links | vague wish, no done-when |
| `[ref]` | `Source:` (URL / file:line / commit) | hedged language without evidence |
| `[decision]` | rationale + alternative considered | silent reversal — mark old `(superseded)` |
| `[exp]` | specific cause (what failed, why) | "tried things and it worked" |
| `[tool]` | `version:` + fenced working syntax | speculative / untested commands |
| `[hypothesis]` | `Verify:` confirmation/falsification path | stating it as fact — use `[ref]`+`Source:` instead |
| `[reflection]` | pattern seen ≥ 2× with examples | one-off opinion |
| `[skill-ref]` | path to `<ws>/skills/<slug>.md` | inline how-to (link instead) |
| `[secret-ref]` | env-var name or path + how to obtain | the actual value (EVER) |

### 7b. Write-time DROP rules

- Body `< 20 chars` after prefix → DROP
- Hedge-only without evidence → DROP
- `[ref]` without `Source:` → DROP
- `[decision]` without rationale clause → DROP
- `[tool]` without version + working syntax → DROP
- `[hypothesis]` without a `Verify:` path → DROP
- `[goal]` without `Status:` / success criterion → DROP
- Secret pattern match (AKIA / sk- / ghp_ / xox / PRIVATE KEY / JWT) → BLOCK
- Reserved slug as topic → DROP
- Slug fails `^[a-z0-9][a-z0-9-]{0,59}$` → DROP

### 7c. Numeric thresholds

- Within-file Jaccard duplicate ≥ 0.85 → DELETE shorter
- Cross-file cosine > 0.92 → merge candidate
- Contradiction overlap ≥ 0.4 + polarity flip → flag
- Topic-folder soft max 15-25 aspects → `/mem-dream` Deep
- Hard split > 800 lines aggregate → `/mem-promote`

### 7d. Write semantics (never blind append)

`ADD` (new) · `UPDATE` (replace + dated marker) · `DELETE` (mark `(superseded)`)
· `NOOP` (duplicate, skip). `[decision]` uses temporal invalidation only — never
hard-delete.

### 7e. Retention TTL

| Prefix | TTL | Delete trigger |
|---|---|---|
| `[goal]` | ∞ | mark achieved/abandoned/superseded (never delete) |
| `[ref]/[decision]/[skill-ref]/[secret-ref]` | ∞ | `(superseded)` / `(rotated)` / `valid_until:` past |
| `[reflection]` | 180d | low layer_score + untouched 180d |
| `[exp]` | 90d | recall_count < 2 AND age > 90d (Ebbinghaus floor 0.1) |
| `[hypothesis]` | 30d | refuted OR (age > 30d AND never promoted to `[ref]`) |
| `[tool]` | until deprecated | `valid_until:` past OR DEPRECATED |
| journal raw | 7d | after `/mem-distill` succeeds |

## 8. Slug + wikilink scope (v3)

- Topic slug unique within workspace.
- Aspect slug unique within topic+date.
- `[[slug]]` → `<base>/<slug>/00-README.md` (with v2 fallbacks for read).
- `[[ws:slug]]` cross-workspace. `[[shared:secrets]]` shared.
- `[[slug/aspect]]` or `[[slug/YYYY-MM-DD-aspect]]` specific aspect.
- NEVER rename published slug.

## 9. Lifecycle

`status: draft → active → distilled → archived`. > 800 lines → split.
6mo untouched + distilled → `_archive/`. Workspace 6mo untouched → archive.

## 10. Recall scope

Default `recall.cross_workspace=false`. Wikilink: 1 hop default. Skip
`(superseded)` / expired `valid_until:`. Deterministic scoring (no LLM):
`R = 0.30·BM25 + 0.30·layer + 0.15·recency + 0.15·diversity + 0.10·log(1+recall_count)`.

## 11. Workflow

1. Log raw → `journal/<today>.md` (`memj`).
2. Repeating ≥ 2× → `memk` (skillify).
3. Bug/surprise → `memL`.
4. PreCompact → distill journal → topic entries (auto-journal classifier).
5. PostCompact → auto-sync pull-rebase-push.
6. Weekly → `memr` + `/mem-dream` + `/mem-prune` + `/mem-lint`.
7. Research-first: no evidence → no implement.
8. Tools-first.
9. Verify before claim.

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

## 13. Auto-sync

```
SessionStart  → auto-sync.py --pull-only
PreCompact    → auto-sync.py --commit-only
PostCompact   → auto-sync.py --pull-rebase-push
```

`SYNC-CONFLICT.md` → `/mem-sync-resolve`. KHÔNG sửa markers tay.

## 14. Guardrails

- KHÔNG commit secret values — `secrets.md` is pointer-only.
- KHÔNG skip bootstrap.
- KHÔNG giữ entry mâu thuẫn.
- KHÔNG promote `[ref]` không có `Source:`.
- KHÔNG sửa SYNC-CONFLICT.md markers tay.
- KHÔNG nest > 3 levels. KHÔNG đổi slug đã publish.
- KHÔNG ghi ws A vào ws B without cross declare.
- KHÔNG blind append — classify ADD/UPDATE/DELETE/NOOP.
- KHÔNG soften §7 thresholds without research note in `shared/research/`.
- Mỗi update → commit `knowledge(<ws>/<slug>): mô tả`.
