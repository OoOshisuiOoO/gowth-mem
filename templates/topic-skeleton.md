---
slug: <slug>                   # global ID per workspace, kebab-case ≤60 chars, regex ^[a-z0-9][a-z0-9-]{0,59}$
title: <Title Case>
status: draft                  # draft → active → distilled → archived
created: 2026-05-04
last_touched: 2026-05-04
parents: []                    # path = workspaces/<ws>/<parents...>/<slug>.md (≤3 cấp)
links: []                      # related slugs same workspace; cross-ws via [[ws:slug]]
aliases: []                    # alternate names for fuzzy/exact alias resolution
tags: []                       # free-form labels (e.g. [k8s, observability])
maturity: experimental         # experimental | stable | deprecated
---

# <Title Case>

## TL;DR

> 1-2 dòng cốt lõi — đọc xong phải biết topic này nói về cái gì và khi nào dùng.

## Context

Khi nào topic này matter, ai cần, vấn đề nó giải quyết. 2-4 câu, không lan man.
Nếu topic là 1 incident/bug fix, nêu trigger + impact + scope.

## Definitions (optional)

Term-specific glossary chỉ trong topic này (1-2 dòng/term). Xoá nếu không cần.

## [exp]

Episodic — debug, fix, lesson, surprise, anti-pattern. Format `- YYYY-MM-DD: <1-2 dòng cốt lõi> (Source: <reproducible>)`.

- (empty)

## [ref]

Verified facts. **Source: BẮT BUỘC** (URL / file:line / doc / paper).

- (empty)

## [decision]

Architectural / design choice + rationale. Nêu rõ "chọn X over Y vì Z".

- (empty)

## [reflection]

Pattern / takeaway sinh qua `/mem-reflect` weekly. Cluster các `[exp]` thành insight.

- (empty)

## How to / Runbook (optional)

Step-by-step nếu topic có flow operational (e.g. "khi alert X fire → check Y → run Z").
Xoá section nếu topic chỉ là knowledge thuần.

## Open questions

Các câu hỏi chưa trả lời được — TODO cho research lần sau. Mỗi câu 1 dòng.

- (empty)

## See also

Cross-link tới topic khác (cùng workspace hoặc cross-ws). Convention `- [[slug]] — vì sao related (1 dòng)`.

- (empty)

## Sources

URL / paper / commit ngoài workspace. Phân biệt với `[ref]` Source — đây là background reading.

- (empty)

## Changelog

Append-only history of major edits (không thay thế git log; chỉ ghi mốc người-đọc).

- 2026-05-04: Initial draft.
