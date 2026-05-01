# docs/exp.md

Episodic — kinh nghiệm. Ghi sau mỗi lần debug / giải quyết / quyết định.

## Schema (strict — adapted from mempalace's 5-type extractor)

Each entry MUST start with a `[type]` prefix. The type drives behavior in recall and prune.

| Type | Use for | Example |
|---|---|---|
| `[decision]` | Choice + rationale | `- [decision] use EMA cross over RSI because DD 30% lower` |
| `[preference]` | Recurring rule ("always X" / "never Y") | `- [preference] always set stoploss via ATR, never via fixed %` |
| `[milestone]` | Working solution / breakthrough | `- [milestone] EMA strategy live 30 days, DD = 25%` |
| `[problem]` | Bug / failure / fix | `- [problem] naive EMA cross DD 60% → fix: filter by ATR slope` |

Each entry is 1-2 lines. Optional continuation lines indented.

## Quality gates (entries failing these get DROPPED by mem-distill)

- ≥ 20 chars (no fragments)
- Not pure code (no shell pipes, imports, only operators)
- Has Source link OR happened in this session (verifiable)
- Confidence > 0.3 (not vague speculation)

## Decisions

- [decision] (choice) chosen because (rationale)
  Source: <commit | file:line | session date>

## Preferences

- [preference] always/never (pattern) — (rationale 1 line)
  Source: <ref>

## Milestones

- [milestone] (what works now, with measurable result)
  Source: <run / log>

## Problems

- [problem] (bug or failure mode) → fix: (solution) — (insight)
  Source: <commit / postmortem>

## Reflections

(Filled by `mem-reflect` skill / `memr` shortcut. Cross-entry patterns. Don't add manually.)

## Outdated / superseded markers

To deprecate without immediate delete (for audit before next prune):

```markdown
- [decision] (old choice) — Source: ... — (superseded by [[New Decision]] on 2026-05-02)
- [fact] (old API behavior) — valid_until: 2025-12-31
```

`mem-prune` will DELETE these on next run. Don't keep parallel contradicting entries — pick one truth.
