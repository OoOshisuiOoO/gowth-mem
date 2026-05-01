# AGENTS.md

Operating rules — the rational layer. Hard constraints, workflow, must / never.

## 1. Identity & role

- Long-term cross-project memory layer for Claude Code. Single global vault at `~/.gowth-mem/`, synced via private git remote.
- Mỗi session làm việc trong **1 workspace** (devops / trade / personal / default). Knowledge updates ghi vào active workspace, không lan sang workspace khác.
- Mode: tự đọc state, tự tra `shared/secrets.md` khi cần tài nguyên, tự ghi lại kinh nghiệm khi học được điều mới. Không hỏi lại thứ đã có trong docs.

## 2. Active-workspace resolution (BẮT BUỘC mỗi session)

Bootstrap hook resolve theo thứ tự (first match wins):

1. Env `GOWTH_WORKSPACE=<name>` (highest priority).
2. Session-scoped `~/.gowth-mem/.session-workspace` (set by `/mem-workspace <name>`).
3. Glob match `$PWD` trong `config.json.workspace_map`.
4. `config.json.active_workspace` (default `"default"`).

Switch trong session: `/mem-workspace <name>`.

## 3. Bootstrap order

1. **Global** (cross-workspace):
   - `AGENTS.md` — rules (file này)
   - `shared/files.md` — top-level tree
   - `shared/secrets.md` — env-var POINTERS
   - `shared/tools.md` — system-wide tool registry
2. **Workspace** (active):
   - `workspaces/<ws>/AGENTS.md` — optional override
   - `workspaces/<ws>/_MAP.md` — root topic MOC
   - `workspaces/<ws>/docs/handoff.md` — đang ở đâu
   - `workspaces/<ws>/docs/{exp,ref,tools,files}.md` — workspace-scoped knowledge
   - Top-3 topics theo `frontmatter.last_touched`
   - `workspaces/<ws>/journal/{today,yesterday}.md`
   - `workspaces/<ws>/skills/_index` (nếu có)

Sau bootstrap → tóm tắt 3 dòng: **workspace=<ws> / đang làm gì / step kế / blocker**.

## 4. v2.2 layout

```
~/.gowth-mem/
├── AGENTS.md  settings.json  config.json  state.json  index.db  .locks/
├── shared/                     ★ truly cross-workspace
│   ├── _MAP.md  secrets.md  tools.md  files.md
│   └── skills/<slug>.md        shared Voyager workflows
└── workspaces/
    ├── _MAP.md                 workspace registry
    └── <ws>/                   default | devops | trade | …
        ├── workspace.json      metadata
        ├── AGENTS.md           (optional override)
        ├── _MAP.md             root topic MOC
        ├── docs/{handoff,exp,ref,tools,files}.md
        ├── topics/             v2.1 hierarchical, ≤3 cấp
        ├── journal/<date>.md
        └── skills/<slug>.md    (optional override)
```

## 5. Topic file format (file-per-topic, default trong workspace)

```markdown
---
slug: ema-cross               # unique TRONG workspace, kebab-case ≤60 chars
title: EMA Cross Strategy
status: draft|active|distilled|archived
created: 2026-05-02
last_touched: 2026-05-02
parents: [strategies, trend]   # path = workspaces/<ws>/topics/strategies/trend/<slug>.md
links: [rsi, breakout]
aliases: [ema-9-21]
---

# EMA Cross Strategy

> Cốt lõi 1 dòng.

## [exp]
- 2026-04-15: <1-2 dòng> (Source: …)

## [ref]
- <fact> (Source: ta-lib 0.4.0 docs)

## [decision]
- Chọn X over Y vì Z (Source: …)

## [reflection]
- Pattern observation. Cross-link [[other-slug]] hoặc [[other-ws:other-slug]].
```

## 6. Section schema

| Section | Yêu cầu |
|---|---|
| `## [exp]` | Episodic. 1-2 dòng. `Source:` nếu reproducible |
| `## [ref]` | Verified fact. **`Source:` BẮT BUỘC** |
| `## [decision]` | Architectural choice + rationale |
| `## [reflection]` | Pattern / takeaway, sinh qua `/mem-reflect` |
| `## [tool]` | Tool quirk specific topic. Cross-topic → `<ws>/docs/tools.md` |

`[skill-ref]` → `frontmatter.links:`. `[secret-ref]` → `shared/secrets.md` POINTER only.

## 7. Slug + wikilink scope

- Slug unique **trong cùng workspace**. 2 workspace có thể trùng slug.
- `[[slug]]` → resolve trong active workspace.
- `[[ws:slug]]` → cross-workspace explicit.
- `[[shared:secrets]]` → reference shared registry.
- Slug regex: `^[a-z0-9-]{1,60}$`. Conflict in same ws → reject save.

## 8. Lifecycle

- `frontmatter.status`: `draft → active → distilled → archived`.
- File >800 dòng → `/mem-promote` split sang folder brain-style.
- 6 tháng untouched + distilled → `<ws>/topics/_archive/<slug>/`.
- Workspace 6 tháng untouched → `/mem-workspace-archive` → `workspaces/_archive/<name>/`.

## 9. Lazy nesting + restructure

- Default FLAT trong workspace. ≥5 topic chung domain → đề xuất nest. Max 3 cấp.
- `/mem-restructure`: user mapping (slug→new parents) → atomic move + rebuild MOC + regen index.

## 10. MOC per folder

- Mỗi folder trong `workspaces/<ws>/topics/` có `_MAP.md` (auto-rebuild bởi `_moc.py`).
- Workspace root: `workspaces/<ws>/_MAP.md`. Cross-workspace registry: `workspaces/_MAP.md`. Shared: `shared/_MAP.md`.
- Auto-sections (Children/Subfolders/Parent) regen từ frontmatter scan. `## Cross-links (manual)` NEVER overwritten.

## 11. Recall scope

- Default `recall.cross_workspace=false`: search active workspace + `shared/`.
- `cross_workspace=true` → search hết.
- Wikilink follow: 1 hop default.

## 12. Workflow

1. Throughout session: log raw vào `workspaces/<active>/journal/<today>.md` (`memj`).
2. Repeating ≥2×: `memk` → `<ws>/skills/<name>.md` (hoặc `shared/skills/`).
3. Before /compact (PreCompact hook): distill journal → topic-bound entries.
4. After /compact (PostCompact hook): auto-sync pull-rebase-push.
5. Weekly: `memr` → 1-3 `[reflection]` mới.
6. Research-first: no evidence → no implementation. `[ref]` phải có `Source:`.
7. Tools-first: `shared/tools.md` + `<ws>/docs/tools.md` + topic match.
8. Verify before claim: no screenshot/log/test → no "done".

## 13. Multi-session safety

- `state.json` writes: `file_lock("state")`.
- Sync ops: `file_lock("sync")`.
- MOC rebuild: `file_lock("moc")`.
- Markdown writes atomic.
- `index.db` WAL + busy_timeout. Schema: `slugs(workspace, slug, …)` PK=`(workspace,slug)`.

## 14. Auto-sync flow

```
SessionStart  → auto-sync.py --pull-only
PreCompact    → auto-sync.py --commit-only
PostCompact   → auto-sync.py --pull-rebase-push (AI conflict on collision)
```

`SYNC-CONFLICT.md` tồn tại → mỗi prompt nhắc `/mem-sync-resolve`.

## 15. Token efficiency (provider prompt caching)

- **Stable prefix**: `AGENTS.md`, `shared/files.md`, `shared/secrets.md`, `shared/tools.md`, `workspaces/<ws>/_MAP.md` — cached 75-90%.
- **Volatile suffix**: `<ws>/docs/handoff.md`, `<ws>/journal/<today>.md` — không cache.
- Caps: 12k chars/file, 60k total.

## 16. Temporal facts

```markdown
- `[ref]` ANTHROPIC_API_KEY format `sk-ant-` (Source: docs.anthropic.com) — valid_until: 2026-12-31
- `[ref]` (old) Use claude-3-opus — (superseded by claude-opus-4)
```

`recall-active.py` tự skip `(superseded)` và `valid_until` quá hạn.

## 17. Spaced resurfacing

`recall-active.py` tracks `last_seen` per file. ~25% prob/prompt surface 1 file unseen ≥7 ngày. Scope theo workspace.

## 18. Guardrails (KHÔNG)

- KHÔNG commit secret value vào git.
- KHÔNG skip bootstrap.
- KHÔNG giữ entry mâu thuẫn.
- KHÔNG promote `[ref]` không có `Source:`.
- KHÔNG sửa `SYNC-CONFLICT.md` markers tay.
- KHÔNG nest >3 cấp trong `<ws>/topics/`.
- KHÔNG đổi slug đã publish.
- KHÔNG ghi knowledge từ workspace A vào workspace B mà không declare cross.
- Mỗi update knowledge → 1 commit `knowledge(<ws>/<slug>): mô tả`.

## 19. RULES carry-over

1. Parallelism: 2+ task độc lập → song song.
2. Plan mode cho task phức tạp.
3. Evolve AGENTS.md.
4. Reusable skills: làm >1×/ngày → skill.
5. Outcome-based prompting.
6. Verify before claim.
