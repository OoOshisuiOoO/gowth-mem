---
slug: topic-a-lessons
title: Topic A — Lessons
---

# Topic A — Lessons

## OOM on metadata write

- **Tried**: bumped jvm.heap=4g, restart FE
- **Root cause**: catalog scan loop holds metadata lock 8m
- **Fix**: enable `metadata.write.async=true`
- **Source**: starrocks/fe/META.md:120
- **When**: 2026-04-11
