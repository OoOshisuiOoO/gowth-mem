# docs/files.md

Project structure. Map các thư mục / file quan trọng.

## Tree

```
.
├── docs/              # Working memory (gowth-mem layer)
│   ├── handoff.md
│   ├── exp.md
│   ├── ref.md
│   ├── tools.md
│   ├── secrets.md
│   └── files.md
├── wiki/              # Knowledge base (claude-obsidian, optional)
├── evidence/          # Screenshots, logs, traces (gitignored)
└── (project source)
```

## Conventions

- Working memory → `docs/`
- Long-term knowledge → `wiki/` (qua claude-obsidian's `/save`)
- Run evidence → `evidence/run_<timestamp>/`
- Secrets → never in repo (xem `docs/secrets.md`)

## Important paths

- (e.g. `src/strategy/` — chiến thuật trading)
- (e.g. `tests/fixtures/` — sample data)
