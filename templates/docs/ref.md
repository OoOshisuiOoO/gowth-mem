# docs/ref.md

Semantic — verified external facts. Ghi sau mỗi lần research source ngoài.

## Schema (strict)

Each entry MUST:
- Start with `[fact]` prefix
- Include a `Source:` link (URL preferred)
- Be 1-2 lines

Optional metadata for stale-detection (mem-prune drops expired):
- `valid_until: YYYY-MM-DD`
- `version: <name>` (e.g. `version: claude-opus-4-7`)
- `applies_to: <constraint>` (e.g. `applies_to: anthropic SDK >= 0.42`)

## Quality gates (mem-distill rejects entries failing these)

- Has Source URL or `file:line`
- ≥ 20 chars
- Not vague speculation
- Distinguishes from existing entries (Jaccard < 0.85)

## API / SDK

- [fact] (claim about API behavior, with version)
  Source: <URL> — version: <X>

## Specs / Standards

- [fact] (rule from spec) — (why it matters)
  Source: <URL or file:line>

## Numbers / Limits

- [fact] (limit / threshold) — (context)
  Source: <URL>

## Examples

```markdown
- [fact] Anthropic prompt cache TTL = 5 min default
  Source: https://docs.anthropic.com/cache — valid_until: 2026-12-31

- [fact] OpenAI text-embedding-3-small = 1536 dims, supports Matryoshka cut to 256
  Source: https://platform.openai.com/docs/guides/embeddings — version: 2024-12

- [fact] (old) Anthropic legacy URL https://api.anthropic.com/v1/engines (superseded)
  Source: archived
```

`mem-prune` deletes the `(superseded)` and any with past `valid_until:`.

## Conflict rule

If a new fact contradicts an existing one — DELETE the old (don't keep both). Audit lives in git log.
