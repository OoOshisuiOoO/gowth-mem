# AGENTS.md (shared — cross-workspace)

Hard rules. Stable across workspaces. Per-workspace overrides: `workspaces/<ws>/AGENTS.md`.

## 1. Identity

Persistent memory layer for Claude Code. Single vault `~/.gowth-mem/`, synced via git remote.
1 session = 1 active workspace. Knowledge stays in active ws unless explicit cross-ws.
Tự đọc state, tra `shared/secrets.md`, ghi kinh nghiệm. Không hỏi lại thứ đã có trong docs.

## 2. Workspace resolve

First match wins: env `GOWTH_WORKSPACE` → `config.json.workspace_map` glob → `config.json.active_workspace` → `"default"`.
Switch: `/mem-workspace <name>`.

## 3. Bootstrap (SessionStart)

1. **Global**: `shared/AGENTS.md` → `shared/{files,secrets,tools}.md`.
2. **Workspace**: `workspaces/<ws>/AGENTS.md` (override) → `<ws>/_MAP.md` → `<ws>/docs/handoff.md` → `<ws>/docs/{exp,ref,tools,files}.md` → top-3 topic `00-README.md` (by `frontmatter.last_touched`) → `<ws>/journal/{today,yesterday}.md` → `<ws>/skills/_index`.

v3 nudge: if `settings.layout_version < 3`, SessionStart prepends an upgrade hint pointing at `/mem-migrate-v3`.

Output: **workspace=<ws> / đang làm gì / step kế / blocker**.

## 4. Layout (v3.0 — topic folder + dated aspect)

```
~/.gowth-mem/
├── shared/                   cross-workspace
│   ├── AGENTS.md  _MAP.md  files.md  secrets.md  tools.md
│   └── skills/<slug>.md
└── workspaces/<ws>/
    ├── AGENTS.md             workspace-specific rules (slim)
    ├── workspace.json  _MAP.md
    ├── docs/                 RESERVED — handoff/exp/ref/tools/files
    ├── journal/<date>.md     RESERVED
    ├── skills/<slug>.md      RESERVED
    ├── research/             RESERVED — long-form research output (new in v3)
    ├── <slug>/               ★ TOPIC FOLDER (v3)
    │   ├── 00-README.md      ★ MOC — TL;DR + Aspects (auto) + Cross-links (manual)
    │   ├── YYYY-MM-DD-<aspect>.md   ★ dated aspect — multiple per topic per day
    │   └── lessons.md        ★ folder ledger (5-field schema)
    └── <domain>/             DOMAIN folder (no <domain>/<domain>.md)
        ├── _MAP.md
        └── <sub>/00-README.md   nested topic (≤3 levels)
```

`[[<slug>]]` resolves to `<somewhere>/<slug>/00-README.md` (v3), falling back to
v2.4 `<slug>/<slug>.md` and v2.3 flat `<slug>.md` for read-path compatibility.

Reserved subdirs (cannot be a topic slug at workspace root):
`docs`, `journal`, `skills`, `research`.
Reserved files inside topic folders: `00-README.md`, `lessons.md`, `_MAP.md`, `AGENTS.md`, `workspace.json`.
Reserved aspect names (cannot be `<aspect>` portion): `readme`, `lessons`, `00-readme`.
Default flat. ≥5 topics same domain → nest. Max 3 levels. `_MAP.md` auto-generated; `## Cross-links (manual)` never overwritten.

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
> 1-2 lines core.

## Aspects (auto)
- 2026-05-04: [[ema-cross/2026-05-04-backtest|backtest]] — first preview line
- 2026-05-03: [[ema-cross/2026-05-03-rules|rules]] — entry/exit logic
- [[ema-cross/lessons|lessons]] — 5-field ledger

## Cross-links (manual)
- (preserved across rebuilds)
```

### 5b. `YYYY-MM-DD-<aspect>.md` — dated aspect file (the actual content)

```markdown
---
slug: ema-cross-backtest      # topic-slug + aspect-slug
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

## [exp]      ← episodic, 1-2 lines
## [ref]      ← verified fact, **Source: REQUIRED**
## [decision] ← architectural choice + rationale
## [reflection] ← pattern (weekly via /mem-reflect)
## [tool]     ← tool quirks specific to this topic
```

### 5c. `lessons.md` — per-topic ledger (5-field schema, see §6)

NEVER write entries into `00-README.md` directly. NEVER mix `[lesson]` with other
type-prefixes inside dated aspect files (lessons have their own file).

## 6. `[lesson]` 5-field schema (`lessons.md` per topic folder)

```markdown
## <Symptom — observable error / behavior, becomes H2 for FTS5 prefix>
- **Tried**: <ordered attempts>
- **Root cause**: <1 line>
- **Fix**: <working command/patch>
- **Source**: <commit | file:line | URL>
- **When**: 2026-05-04
```

Append-at-top via `memL`. One `lessons.md` PER TOPIC FOLDER (not per dated aspect).

## 7. Slug + wikilink scope (v3)

- Topic slug unique **within workspace**. 2 ws can reuse the same slug.
- Aspect slug unique **within topic+date** (you can have 2 aspects same day, must differ).
- `[[slug]]` → resolves in active ws via v3 layout: `<base>/<slug>/00-README.md`.
  Falls back to v2.4 `<slug>/<slug>.md` then v2.3 flat `<slug>.md` (read-path only).
- `[[ws:slug]]` → cross-workspace explicit. `[[shared:secrets]]` → shared registry.
- `[[slug/aspect]]` or `[[slug/YYYY-MM-DD-aspect]]` → specific aspect file.
- Slug regex (both topic and aspect): `^[a-z0-9][a-z0-9-]{0,59}$`. Conflict in same scope → reject.
- Reserved aspect names blocked: `readme`, `lessons`, `00-readme`.
- NEVER rename published topic slug (breaks wikilinks). Change `parents:` via `/mem-restructure` only.

## 8. Lifecycle

- `status`: `draft → active → distilled → archived`.
- Topic folder >800 lines aggregate → `/mem-promote` split into sub-topics.
- 6 months untouched + distilled → `<ws>/_archive/<slug>/`.
- Workspace 6 months untouched → `/mem-workspace-archive`.

## 9. Lazy nesting + MOC

- Default FLAT in workspace. ≥5 topics with shared parent → suggest nest. Max 3 levels.
- Each topic folder has `00-README.md` (auto via `_moc.py rebuild_topic_readme`).
- Each domain folder (no `00-README.md`) has `_MAP.md` (auto via `_moc.py`).
- `## Cross-links (manual)` block NEVER overwritten.

## 10. Recall scope

- Default `recall.cross_workspace=false`: search active ws + `shared/`.
- Wikilink follow: 1 hop default.
- Skip lines `(superseded)` / expired `valid_until:`.

## 11. Workflow

1. Log raw → `journal/<today>.md` (`memj`).
2. Repeating ≥2× → `memk` (skillify).
3. Bug/surprise → `memL <symptom> -- <tried> -- <root> -- <fix>`.
4. PreCompact → distill journal → topic entries.
5. PostCompact → auto-sync pull-rebase-push.
6. Weekly → `memr` (1-3 reflections).
7. Research-first: no evidence → no implement. `[ref]` must have `Source:`.
8. Tools-first: tra `shared/tools.md` + `<ws>/docs/tools.md` trước khi tự code.
9. Verify before claim: no log/screenshot/test → no "done".

## 12. Shortcuts

| Alias | Skill | | Alias | Skill |
|---|---|---|---|---|
| `mems` | mem-save | | `memx` | mem-reindex |
| `memd` | mem-distill | | `memc` | mem-cost |
| `memr` | mem-reflect | | `memp` | mem-prune |
| `memk` | mem-skillify | | `memy` | mem-sync |
| `memb` | mem-bootstrap | | `memg` | mem-config |
| `memh` | mem-hyde-recall | | `memm` | mem-migrate-global |
| `memj` | mem-journal | | `memT` | mem-topic |
| `memI` | mem-install | | `memL` | mem-lesson |
| `memC` | mem-sync-resolve | | | |

Capital-suffix is case-sensitive.

## 13. Auto-sync

```
SessionStart  → auto-sync.py --pull-only
PreCompact    → auto-sync.py --commit-only
PostCompact   → auto-sync.py --pull-rebase-push
```

`SYNC-CONFLICT.md` exists → mỗi prompt nhắc `/mem-sync-resolve`. KHÔNG sửa markers tay.

## 14. Guardrails

- KHÔNG commit secret values — `secrets.md` is pointer-only (env-var names).
- KHÔNG skip bootstrap.
- KHÔNG giữ entry mâu thuẫn — mới đúng → DELETE cũ (hoặc `(superseded)`).
- KHÔNG promote `[ref]` không có `Source:`.
- KHÔNG sửa `SYNC-CONFLICT.md` markers tay.
- KHÔNG nest >3 levels. KHÔNG đổi slug đã publish.
- KHÔNG ghi ws A knowledge vào ws B without cross declare.
- Mỗi knowledge update → commit `knowledge(<ws>/<slug>): mô tả`.
