# AGENTS.md

Operating rules — the rational layer. Hard constraints, workflow, must / never.

## Identity & role

- (one line: who is this agent, what is its primary objective)

## Hard rules (non-negotiable)

- (rule 1)
- (rule 2)

## 5-tier memory pipeline (mirror nhận thức con người)

```
Tier A — Procedural skill library     (gowth-mem: docs/skills/<name>.md)
Tier 1 — Raw daily journal             (gowth-mem: docs/journal/<date>.md)
   ↓  /mem-distill (mem0 ADD/UPDATE/DELETE/NOOP)
Tier 2 — Curated working memory        (gowth-mem: docs/exp.md, ref.md, tools.md)
   ↓  /mem-reflect (Generative-Agents reflection)
docs/exp.md § Reflections              (high-level patterns)
   ↓  /mem-promote <topic>
Tier 3 — Topic deep dive               (claude-obsidian: wiki/topics/<Topic>.md, [[wikilinks]])
   ↓  /save (claude-obsidian)
Tier 4 — Atomic concepts               (claude-obsidian: wiki/concepts/<atom>.md)

Always-on (state/config, not pipeline):
   docs/handoff.md   docs/secrets.md   docs/files.md
```

## Workflow

1. **Bootstrap (BẮT BUỘC mỗi phiên)**: read AGENTS.md + `docs/handoff.md` → `docs/exp.md` → `docs/ref.md` → `docs/tools.md` → `docs/secrets.md` → `docs/files.md` → `docs/journal/<today>.md` (+ yesterday) + skills index. Tóm tắt 3 dòng: **đang làm gì / step kế / blocker**.

2. **Throughout the session**: `/mem-journal` để log raw observations vào `docs/journal/<today>.md`. Append-only.

3. **After repeating a workflow ≥2×**: `/mem-skillify <name>` để extract reusable skill (Voyager pattern). Future sessions invoke it as short reference instead of replaying instructions.

4. **End of session / before `/compact`**: `/mem-distill` (mem0 ADD/UPDATE/DELETE/NOOP semantics). Run `/mem-cost` to verify shrinkage.

5. **Weekly / end-of-sprint**: `/mem-reflect` để generate 1-3 high-level reflections.

6. **When a topic accumulates** (≥3 entries): `/mem-promote <topic>` để gom thành `wiki/topics/<Topic>.md` với `[[wikilinks]]`.

7. **Research-first**: no evidence → no implementation. Save findings to `docs/ref.md` với Source link. Conflict cũ → xóa.

8. **Tools-first**: trước khi viết script, tra `docs/tools.md`. Có tool → dùng tool.

9. **Verify before claim**: no screenshot / log → no "done".

## Token efficiency (provider prompt caching)

To maximize provider prompt-cache hit rate (Anthropic 75-90% discount on cached prefix):

- **Stable prefix** (KHÔNG thay đổi qua sessions): AGENTS.md, docs/secrets.md, docs/files.md, docs/tools.md. Đặt sớm trong context — provider sẽ cache.
- **Volatile suffix** (thay đổi mỗi session): docs/handoff.md, docs/journal/<today>.md, retrieved snippets.
- **Recall additionalContext** đã được hook đặt SAU bootstrap → naturally volatile, không phá cache.

Practical rule: nếu sửa AGENTS.md / TOOLS.md / SECRETS.md thường xuyên → cache miss thường xuyên → tốn token. Giữ những file này stable; thay đổi → batch vào 1 lần.

## Knowledge files (tier 1 + 2)

- `docs/journal/<date>.md` — raw daily journal (tier 1)
- `docs/handoff.md` — session state (always-on)
- `docs/exp.md` — curated episodic + § Reflections (tier 2)
- `docs/ref.md` — verified semantic facts với Source (tier 2)
- `docs/tools.md` — tool registry (tier 2)
- `docs/secrets.md` — resource pointers (env-var name only, NEVER values)
- `docs/files.md` — project structure map
- `docs/skills/<name>.md` — Voyager-style reusable workflows (tier A)

## Long-term knowledge (tier 3 + 4)

- `wiki/topics/<Topic>.md` — topic deep dive với `[[wikilinks]]` (claude-obsidian)
- `wiki/concepts/<atom>.md` — atomic concept (claude-obsidian)

## Temporal facts convention

For entries that may become stale (API behavior changes, version-specific syntax):

```markdown
- ANTHROPIC_API_KEY format: starts with `sk-ant-` — Source: docs.anthropic.com — valid_until: 2026-12-31
- (old) Use `claude-3-opus` model — Source: ... — (superseded by claude-opus-4)
```

The `recall-active.py` hook **automatically skips** lines containing:
- `(superseded)` (case-insensitive)
- `valid_until: YYYY-MM-DD` where date is in the past

Conflict resolution: when adding a new entry that supersedes an old one → mark old as `(superseded)` instead of deleting (keeps audit trail), or delete entirely if irrelevant.

## Spaced resurfacing (forgetting curve)

Hook `recall-active.py` tracks `last_seen` per file in `.gowth-mem/state.json`. With ~25% probability per prompt, surfaces 1 file that hasn't been seen in ≥7 days. This counters the forgetting curve — old knowledge stays warm without explicit review.

`.gowth-mem/` should be added to your workspace `.gitignore`.

## Guardrails

- KHÔNG commit value thật của API key / token vào git.
- KHÔNG skip bootstrap rồi viết code "luôn cho nhanh".
- KHÔNG giữ knowledge entry mâu thuẫn — entry mới đúng → xóa cũ hoặc mark `(superseded)`.
- KHÔNG promote vào tier 2/3 entry không có Source nếu là verified fact.
- Mỗi update knowledge → commit `knowledge([file]): mô tả`.
