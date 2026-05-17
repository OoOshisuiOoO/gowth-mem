---
slug: <slug>-<aspect>          # topic slug + aspect slug (kebab-case)
title: <Title Case> — <Aspect Title>
type: aspect                   # always "aspect" for dated aspect files
date: 2026-05-04               # YYYY-MM-DD (matches filename prefix)
topic: <slug>                  # parent topic folder slug
aspect: <aspect>               # aspect name (no .md, kebab-case)
status: draft                  # draft → active → distilled → archived
created: 2026-05-04
last_touched: 2026-05-04
links: []                      # related slugs same workspace; cross-ws via [[ws:slug]]
tags: []                       # free-form labels
---

# <Aspect Title>

> Filename pattern: `<YYYY-MM-DD>-<aspect>.md` inside `workspaces/<ws>/<slug>/`.
> Reserved aspect names (blocked): `readme`, `lessons`, `00-readme`.

## [exp]

Episodic — debug, fix, lesson, surprise, anti-pattern.
Format `- YYYY-MM-DD: <1-2 lines core> (Source: <reproducible>)`.

- (empty)

## [ref]

Verified facts. **Source: REQUIRED** (URL / file:line / doc / paper).

- (empty)

## [decision]

Architectural / design choice + rationale. State "chose X over Y because Z".

- (empty)

## [reflection]

Pattern / takeaway from `/mem-reflect` weekly. Cluster `[exp]` entries into insight.

- (empty)

## [tool]

Tool quirks specific to this topic (cross-topic quirks → `<ws>/docs/tools.md`).

- (empty)

## Notes

Free-form scratch space for this aspect / date. Delete sections that stay empty.
