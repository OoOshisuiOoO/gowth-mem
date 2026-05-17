---
slug: <slug>                   # global ID per workspace, kebab-case ≤60 chars, regex ^[a-z0-9][a-z0-9-]{0,59}$
title: <Title Case>
type: topic                    # always "topic" for 00-README.md (MOC)
status: draft                  # draft → active → distilled → archived
maturity: experimental         # experimental | stable | deprecated
created: 2026-05-04
last_touched: 2026-05-04
parents: []                    # parent slugs same workspace (≤3 levels)
links: []                      # related slugs same workspace; cross-ws via [[ws:slug]]
aliases: []                    # alternate names for fuzzy/exact alias resolution
tags: []                       # free-form labels (e.g. [k8s, observability])
---

# <Title Case>

## TL;DR

> 1-2 lines core idea — after reading, you know what this topic is and when to use it.

## Aspects (auto)

(populated by `_moc.py rebuild_topic_readme` — newest-first list of dated aspect files
`YYYY-MM-DD-<aspect>.md` plus `lessons.md` if present. NEVER edit manually.)

## Cross-links (manual)

- (curate related topics here — this section is preserved across rebuilds)
