# AGENTS.md

Operating rules — the rational layer. Hard constraints, workflow, must / never.

## Identity & role

- (one line: who is this agent, what is its primary objective)

## Hard rules (non-negotiable)

- (rule 1)
- (rule 2)

## Workflow

1. **Bootstrap (BẮT BUỘC mỗi phiên, theo thứ tự)**: read AGENTS.md + `docs/handoff.md` → `docs/exp.md` → `docs/ref.md` → `docs/tools.md` → `docs/secrets.md` → `docs/files.md`. Sau đó tóm tắt 3 dòng: **đang làm gì / step kế / blocker**.
2. **Research-first**: no evidence → no implementation. Save findings to `docs/ref.md` (with Source link).
3. **Tools-first**: trước khi viết script, tra `docs/tools.md`. Có tool → dùng tool.
4. **Verify before claim**: no screenshot / log → no "done".

## Knowledge files (working memory)

- `docs/handoff.md` — session state (current / next / blocker)
- `docs/exp.md` — episodic experience (debug, fix, lesson)
- `docs/ref.md` — verified semantic facts (with Source link)
- `docs/tools.md` — tool registry
- `docs/secrets.md` — resource pointers (env-var name only, NEVER values)
- `docs/files.md` — project structure map

## Long-term knowledge (optional)

- `wiki/` — claude-obsidian vault (concepts, entities, domains). Promote from `docs/exp.md` / `docs/ref.md` when knowledge stabilizes via `/save`.

## Guardrails

- KHÔNG commit value thật của API key / token vào git.
- KHÔNG skip bootstrap rồi viết code "luôn cho nhanh".
- KHÔNG giữ knowledge entry mâu thuẫn — entry mới đúng → xóa cũ.
- Mỗi update knowledge → commit `knowledge([file]): mô tả`.
