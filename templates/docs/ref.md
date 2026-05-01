# docs/ref.md

Semantic — fact đã verify. Ghi sau mỗi lần research source bên ngoài.

Format: cốt lõi 1-2 dòng, **bắt buộc** có Source link. Conflict → xóa cũ hoặc mark `(superseded)`.

## API / SDK

- (fact) — (1-line takeaway)
  Source: <URL>

## Specs / Standards

- (rule / behavior) — (vì sao quan trọng)
  Source: <URL or file:line>

## Numbers / Limits

- (giới hạn / threshold) — (ngữ cảnh)
  Source: <URL>

## Temporal-aware examples

For facts that may go stale, use one of these inline markers — `recall-active.py` will auto-skip stale entries:

```markdown
- Anthropic prompt cache TTL = 5 min default — Source: docs.anthropic.com — valid_until: 2026-12-31
- (superseded) Old API base URL was https://api.openai.com/v1/engines — Source: archived
```

The hook skips:
- Any line containing `(superseded)` (case-insensitive)
- Any line with `valid_until: YYYY-MM-DD` where date is in the past

Add `valid_until:` only when you're confident the fact will become invalid by that date.
