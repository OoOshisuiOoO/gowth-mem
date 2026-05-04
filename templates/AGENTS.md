# AGENTS.md (shared — cross-workspace)

Hard rules. Workflow. Must / never. Stable across workspaces.
Per-workspace overrides live in `workspaces/<ws>/AGENTS.md`.

## 1. Identity

Long-term cross-project memory layer for Claude Code. Single global vault at `~/.gowth-mem/`, synced via private git remote.
Mỗi session = **1 active workspace** (devops / trade / personal / default). Knowledge updates ghi vào active workspace, không lan sang ws khác trừ khi explicit.
Tự đọc state, tự tra `shared/secrets.md` khi cần tài nguyên, tự ghi lại kinh nghiệm khi học. Không hỏi lại thứ đã có trong docs.

## 2. Active workspace (BẮT BUỘC mỗi session)

Resolve order (first match wins): env `GOWTH_WORKSPACE` → glob `$PWD` trong `config.json.workspace_map` → `config.json.active_workspace` → `"default"`.
Switch trong session: `/mem-workspace <name>`.

## 3. Bootstrap order (SessionStart)

1. **Global**: `shared/AGENTS.md` (file này) → `shared/files.md` → `shared/secrets.md` → `shared/tools.md`.
2. **Workspace**: `workspaces/<ws>/AGENTS.md` (override) → `<ws>/_MAP.md` → `<ws>/docs/handoff.md` → `<ws>/docs/{exp,ref,tools,files}.md` → top-3 topics (frontmatter.last_touched) → `<ws>/journal/{today,yesterday}.md` → `<ws>/skills/_index`.

Sau bootstrap → 3 dòng: **workspace=<ws> / đang làm gì / step kế / blocker**.

## 4. Layout

```
~/.gowth-mem/
├── shared/                   ★ cross-workspace
│   ├── AGENTS.md             ← THIS FILE (global rules)
│   ├── _MAP.md  files.md  secrets.md  tools.md
│   └── skills/<slug>.md
│
└── workspaces/<ws>/
    ├── AGENTS.md             workspace-specific rules (slim)
    ├── workspace.json  _MAP.md
    ├── docs/                 RESERVED — handoff/exp/ref/tools/files
    ├── journal/<date>.md     RESERVED
    ├── skills/<slug>.md      RESERVED
    ├── <slug>/               ★ TOPIC FOLDER (Obsidian folder-note)
    │   ├── <slug>.md         landing (filename = folder name)
    │   ├── <aspect>.md       sub-aspect — first-class slug `<aspect>`
    │   └── lessons.md        ★ folder ledger (5-field schema)
    └── <domain>/             DOMAIN folder (no <domain>/<domain>.md)
        ├── _MAP.md
        └── <sub>/<sub>.md    nested topic (≤3 cấp)
```

`[[<slug>]]` resolves natively → `<somewhere>/<slug>/<slug>.md`.
Reserved (cấm làm slug/domain): `docs`, `journal`, `skills`, `_MAP.md`, `AGENTS.md`, `workspace.json`.

## 5. Topic file format

```markdown
---
slug: ema-cross               # unique TRONG ws, kebab-case ≤60
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

## [exp]      ← episodic, 1-2 dòng
## [ref]      ← verified fact, **Source: BẮT BUỘC**
## [decision] ← architectural choice + rationale
## [reflection] ← pattern (sinh weekly qua /mem-reflect)
## [tool]     ← tool quirk topic này (cross-topic → <ws>/docs/tools.md)
```

## 6. `[lesson]` 5-field schema (lessons.md per topic folder)

```markdown
## <Symptom — observable error / behavior, becomes H2 cho FTS5 prefix>
- **Tried**: <ordered attempts>
- **Root cause**: <1 line; optional 5-Whys>
- **Fix**: <working command / patch / config>
- **Source**: <commit | file:line | URL>     # optional
- **When**: 2026-05-04
```

Append-at-top via `memL`. Một lessons.md PER TOPIC FOLDER (không per sub-aspect).

## 7. Slug + wikilink scope

- Slug unique **trong cùng workspace**. 2 ws có thể trùng slug.
- `[[slug]]` → resolve trong active ws.
- `[[ws:slug]]` → cross-workspace explicit. `[[shared:secrets]]` → shared registry.
- Slug regex: `^[a-z0-9-]{1,60}$`. Conflict same ws → reject.
- KHÔNG đổi slug đã publish (vỡ wikilinks). Đổi parents OK qua `/mem-restructure`.

## 8. Lifecycle

- `status`: `draft → active → distilled → archived`.
- File >800 dòng → `/mem-promote` split.
- 6 tháng untouched + distilled → `<ws>/_archive/<slug>/`.
- Workspace 6 tháng untouched → `/mem-workspace-archive`.

## 9. Lazy nesting + MOC

- Default FLAT trong workspace. ≥5 topic chung domain → đề xuất nest. Max 3 cấp.
- Mỗi folder có `_MAP.md` (auto qua `_moc.py`). `## Cross-links (manual)` NEVER overwritten.

## 10. Recall scope

- Default `recall.cross_workspace=false`: search active ws + `shared/`.
- Wikilink follow: 1 hop default.
- Skip lines `(superseded)` / expired `valid_until:`.

## 11. Workflow

1. Throughout: log raw vào `<ws>/journal/<today>.md` (`memj`).
2. Repeating ≥2× → `memk` (skill: `<ws>/skills/<name>.md` hoặc `shared/skills/`).
3. Bug / surprise → `memL <symptom> -- <tried> -- <root> -- <fix> [-- <source>]`.
4. PreCompact → distill journal → topic-bound entries (active ws).
5. PostCompact → auto-sync pull-rebase-push.
6. Weekly → `memr` (1-3 `[reflection]` mới).
7. Research-first: no evidence → no implementation. `[ref]` phải có `Source:`.
8. Tools-first: tra `shared/tools.md` + `<ws>/docs/tools.md` trước khi tự code.
9. Verify before claim: no log/screenshot/test → no "done".

## 12. Shortcut alias table

Prefix ngay đầu prompt. **Capital-suffix là CASE-SENSITIVE** để tránh va lowercase variants.

| Lowercase | Capital | Skill |
|---|---|---|
| `mems` | — | mem-save |
| `memd` | — | mem-distill |
| `memr` | — | mem-reflect |
| `memk` | — | mem-skillify |
| `memb` | — | mem-bootstrap |
| `memh` | — | mem-hyde-recall |
| `memj` | — | mem-journal |
| `memx` | — | mem-reindex |
| `memc` | — | mem-cost |
| `memp` | — | mem-prune |
| `memy` | — | mem-sync |
| `memg` | — | mem-config |
| `memm` | — | mem-migrate-global |
| — | `memT` | mem-topic |
| — | `memI` | mem-install |
| — | `memC` | mem-sync-resolve |
| — | `memL` | mem-lesson |

## 13. Auto-sync flow

```
SessionStart  → auto-sync.py --pull-only
PreCompact    → auto-sync.py --commit-only
PostCompact   → auto-sync.py --pull-rebase-push (AI conflict on collision)
```

`SYNC-CONFLICT.md` tồn tại → mỗi prompt nhắc `/mem-sync-resolve`. KHÔNG sửa marker tay.

## 14. Guardrails (KHÔNG)

- KHÔNG commit secret value vào git. `shared/secrets.md` là POINTER only (env-var name + cách lấy).
- KHÔNG skip bootstrap.
- KHÔNG giữ entry mâu thuẫn — entry mới đúng → DELETE cũ (hoặc `(superseded)`).
- KHÔNG promote `[ref]` không có `Source:`.
- KHÔNG sửa `SYNC-CONFLICT.md` markers tay.
- KHÔNG nest >3 cấp dưới workspace root.
- KHÔNG đổi slug đã publish.
- KHÔNG ghi knowledge từ ws A vào ws B mà không declare cross.
- Mỗi update knowledge → 1 commit `knowledge(<ws>/<slug>): mô tả` (auto bởi sync hook).
