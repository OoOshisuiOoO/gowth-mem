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

1. **Global**: `shared/AGENTS.md` → `shared/{files,secrets,tools}.md`
2. **Workspace**: `<ws>/AGENTS.md` → `<ws>/_MAP.md` → `<ws>/docs/handoff.md` → `<ws>/docs/{exp,ref,tools,files}.md` → top-3 topics → `<ws>/journal/{today,yesterday}.md` → `<ws>/skills/_index`

Output: **workspace=<ws> / đang làm gì / step kế / blocker**.

## 4. Layout

```
~/.gowth-mem/
├── shared/                   cross-workspace
│   ├── AGENTS.md  _MAP.md  files.md  secrets.md  tools.md
│   └── skills/<slug>.md
└── workspaces/<ws>/
    ├── AGENTS.md  workspace.json  _MAP.md
    ├── docs/                 handoff/exp/ref/tools/files
    ├── journal/<date>.md
    ├── skills/<slug>.md
    ├── <slug>/               topic folder (Obsidian folder-note)
    │   ├── <slug>.md         landing page
    │   ├── <aspect>.md       sub-aspect
    │   └── lessons.md        folder ledger (§6)
    └── <domain>/_MAP.md      domain grouping (no <domain>.md)
```

Reserved names: `docs`, `journal`, `skills`, `_MAP.md`, `AGENTS.md`, `workspace.json`.
Default flat. ≥5 topics same domain → nest. Max 3 levels. `_MAP.md` auto-generated; `## Cross-links (manual)` never overwritten.

## 5. Topic file format

```markdown
---
slug: ema-cross               # unique in ws, kebab-case ≤60
title: EMA Cross Strategy
status: draft|active|distilled|archived
created: 2026-05-02
last_touched: 2026-05-02
parents: [strategies, trend]
links: [rsi, breakout]
aliases: [ema-9-21]
---
# EMA Cross Strategy
> Cốt lõi 1 dòng.

## [exp]        episodic, 1-2 dòng
## [ref]        verified fact, Source: BẮT BUỘC
## [decision]   architectural choice + rationale
## [reflection] pattern (weekly qua /mem-reflect)
## [tool]       tool quirk (cross-topic → docs/tools.md)
```

## 6. Lesson schema (lessons.md per topic folder)

```markdown
## <Symptom — observable error, H2 for FTS5>
- **Tried**: <ordered attempts>
- **Root cause**: <1 line>
- **Fix**: <working command/patch>
- **Source**: <commit | file:line | URL>
- **When**: 2026-05-04
```

Append-at-top via `memL`. One lessons.md per topic folder.

## 7. Slugs & wikilinks

- Slug unique within workspace. Regex: `^[a-z0-9-]{1,60}$`.
- `[[slug]]` → active ws. `[[ws:slug]]` → cross-ws. `[[shared:secrets]]` → shared.
- KHÔNG đổi slug đã publish (vỡ wikilinks). Đổi parents OK qua `/mem-restructure`.

## 8. Lifecycle

`draft → active → distilled → archived`. File >800 dòng → `/mem-promote` split. 6 tháng untouched + distilled → archive.

## 9. Workflow

1. Log raw → `journal/<today>.md` (`memj`).
2. Repeating ≥2× → `memk` (skillify).
3. Bug/surprise → `memL <symptom> -- <tried> -- <root> -- <fix>`.
4. PreCompact → distill journal → topic entries.
5. PostCompact → auto-sync pull-rebase-push.
6. Weekly → `memr` (1-3 reflections).
7. Research-first: no evidence → no implement. `[ref]` must have `Source:`.
8. Tools-first: tra `shared/tools.md` + `<ws>/docs/tools.md` trước khi tự code.
9. Verify before claim: no log/screenshot/test → no "done".

## 10. Shortcuts

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

## 11. Auto-sync

```
SessionStart  → auto-sync.py --pull-only
PreCompact    → auto-sync.py --commit-only
PostCompact   → auto-sync.py --pull-rebase-push
```

`SYNC-CONFLICT.md` exists → mỗi prompt nhắc `/mem-sync-resolve`. KHÔNG sửa markers tay.

## 12. Recall

Default `cross_workspace=false`: search active ws + `shared/`. Wikilink follow: 1 hop. Skip `(superseded)` / expired `valid_until:`.

## 13. Guardrails

- KHÔNG commit secret values — `secrets.md` is pointer-only (env-var names).
- KHÔNG skip bootstrap.
- KHÔNG giữ entry mâu thuẫn — mới đúng → DELETE cũ (hoặc `(superseded)`).
- KHÔNG promote `[ref]` không có `Source:`.
- KHÔNG sửa `SYNC-CONFLICT.md` markers tay.
- KHÔNG nest >3 levels. KHÔNG đổi slug đã publish.
- KHÔNG ghi ws A knowledge vào ws B without cross declare.
- Mỗi knowledge update → commit `knowledge(<ws>/<slug>): mô tả`.
