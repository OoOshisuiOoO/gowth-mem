# docs/tools.md

Tool registry. Ghi sau mỗi lần dùng / cài tool.

## Schema

Each entry MUST start with `[tool]` prefix.

| Section | Format |
|---|---|
| Installed | `\| <tool> \| <version> \| <install cmd> \| <use case> \|` (table row) |
| Cú pháp đã work | `- [tool] <command syntax> — gotcha: <X> — version: <Y>` |
| Đã thử nhưng bỏ | `- [tool] (rejected) <name> — reason: <why>` |

Optional metadata:
- `version: <number>` for version-pinned syntax (mem-prune deletes when version is in DEPRECATED list or `valid_until` past)

## Quality gates

- Tool name + version explicit
- Working syntax (not theoretical)
- Reproducible (`brew install x` not "install somehow")

## Installed

| Tool | Version | Cài bằng | Use case |
|---|---|---|---|
| ripgrep | 14.0 | `brew install rg` | grep recursive default, ignores .gitignore |

## Cú pháp đã work

- [tool] `rg "pattern" -t py` — only Python files
  Gotcha: ignores .gitignore by default; add `-uu` for full search.

- [tool] `gh repo create OWNER/NAME --public --source . --push` — version: gh 2.89

## Đã thử nhưng bỏ

- [tool] (rejected) <name> — reason: (slow / lỗi / overlap)

## Conflict rule

Multiple ways to do the same thing → keep the simplest. DELETE longer/clunkier alternatives.
