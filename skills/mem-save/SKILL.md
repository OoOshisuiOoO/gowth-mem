---
name: mem-save
description: Use when the user says "save this", "remember this", "note this", "ghi lại", "lưu", or after a debug session ends. Routes the entry to the correct docs/* file (handoff/exp/ref/tools/secrets/files) per the gowth-mem taxonomy. For long-term knowledge use claude-obsidian's /save instead.
---

# mem-save

Save a memory entry into the right file with the right structure.

## File routing (working memory — gowth-mem)

| Type of info | File |
|---|---|
| Episodic experience (debug, fix, lesson, surprise, anti-pattern) | `docs/exp.md` |
| Verified semantic fact (API, spec, limit) — must include Source link | `docs/ref.md` |
| Tool note (syntax that worked, gotcha, version) | `docs/tools.md` |
| Resource pointer (env-var name, file path; **never the value**) | `docs/secrets.md` |
| Session state (current task / next / blocker / open threads) | `docs/handoff.md` |
| Project structure (path / convention) | `docs/files.md` |

For **long-term knowledge** (concepts, methods that span sessions / projects), invoke claude-obsidian's `/save` instead — it files into `wiki/` with wikilinks, not `docs/`.

## Entry format

Each entry is 1–2 lines. No noise. Include source / link when relevant.

```markdown
- <fact / decision / lesson> — <why or context, ≤1 line>
  Source: <URL | file:line | session date>
```

## Steps

1. Determine the type of info using the routing table.
2. Read the target `docs/<name>.md`. If missing, suggest running `/mem-init` first.
3. Append under the correct section header (each template has section headers like `## Lessons`, `## API / SDK`, `## Cú pháp đã work`).
4. If a previous entry contradicts the new one, **delete the old entry** — never keep both.
5. Report the file path written.

## Hard rules

- Never write a real secret value into `docs/secrets.md`. Only env-var name + how to obtain.
- Never `mem-save` to `docs/handoff.md` mid-task — wait until end of session or before `/compact`.
- For verified external facts, refuse to save without a Source link.
