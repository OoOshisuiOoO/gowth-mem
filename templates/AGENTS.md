# AGENTS.md

Operating rules — the rational layer. Hard constraints, workflow, must / never.

## Identity & role

- (one line: who is this agent, what is its primary objective)

## Hard rules (non-negotiable)

- (rule 1)
- (rule 2)

## 4-layer memory pipeline (mirror nhận thức con người)

```
Layer 1 — Raw daily journal           (gowth-mem: docs/journal/<date>.md)
   ↓  /mem-distill (chắt lọc, drop noise)
Layer 2 — Curated working memory       (gowth-mem: docs/exp.md, ref.md, tools.md)
   ↓  /mem-promote <topic> (gom theo chủ đề)
Layer 3 — Topic deep dive              (claude-obsidian: wiki/topics/<Topic>.md)
   ↓  /save (claude-obsidian, when stable)
Layer 4 — Atomic concepts              (claude-obsidian: wiki/concepts/<atom>.md)

Always-on (state/config, not part of pipeline):
   docs/handoff.md   docs/secrets.md   docs/files.md
```

Topics ở layer 3+ cross-reference qua Obsidian `[[wikilinks]]`.

## Workflow

1. **Bootstrap (BẮT BUỘC mỗi phiên, theo thứ tự)**: read AGENTS.md + `docs/handoff.md` → `docs/exp.md` → `docs/ref.md` → `docs/tools.md` → `docs/secrets.md` → `docs/files.md` → `docs/journal/<today>.md` (+ yesterday). Sau đó tóm tắt 3 dòng: **đang làm gì / step kế / blocker**.

2. **Throughout the session**: `/mem-journal` để log raw observations vào `docs/journal/<today>.md`. Append-only. KHÔNG cần lọc — đó là việc của distill.

3. **End of session / before `/compact`**: `/mem-distill` để chắt lọc journal entries lên layer 2 (`docs/exp.md` / `ref.md` / `tools.md`). Drop noise.

4. **When a topic has accumulated** (≥3 entries across layer 2): `/mem-promote <topic>` để gom thành `wiki/topics/<Topic>.md` với `[[wikilinks]]` cross-ref.

5. **Research-first**: no evidence → no implementation. Save findings to `docs/ref.md` (with Source link) — hoặc `docs/journal` nếu chưa verify.

6. **Tools-first**: trước khi viết script, tra `docs/tools.md`. Có tool → dùng tool.

7. **Verify before claim**: no screenshot / log → no "done".

## Knowledge files (layer 1 + 2)

- `docs/journal/<date>.md` — raw daily journal (layer 1)
- `docs/handoff.md` — session state (current / next / blocker)
- `docs/exp.md` — curated episodic experience (layer 2)
- `docs/ref.md` — verified semantic facts with Source link (layer 2)
- `docs/tools.md` — tool registry (layer 2)
- `docs/secrets.md` — resource pointers (env-var name only, NEVER values)
- `docs/files.md` — project structure map

## Long-term knowledge (layer 3 + 4)

- `wiki/topics/<Topic>.md` — topic deep dive with `[[wikilinks]]` (claude-obsidian)
- `wiki/concepts/<atom>.md` — atomic concept (claude-obsidian)
- Promote via `/mem-promote <topic>` (gowth-mem) and `/save` (claude-obsidian).

## Guardrails

- KHÔNG commit value thật của API key / token vào git.
- KHÔNG skip bootstrap rồi viết code "luôn cho nhanh".
- KHÔNG giữ knowledge entry mâu thuẫn — entry mới đúng → xóa cũ.
- KHÔNG promote vào layer 2/3 entry không có Source link nếu là verified fact (giữ ở exp với note "needs source").
- Mỗi update knowledge → commit `knowledge([file]): mô tả`.
