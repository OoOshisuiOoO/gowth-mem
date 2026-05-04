"""Topic body templates per `type:` (v2.8).

Each template prescribes what to record so users don't stare at empty
[exp]/[ref]/[decision]/[reflection] sections wondering what goes where.
Sections include 1-line guides (`> hint`) the user replaces with content.

Vocabulary (10 types):
  runbook   — pager playbook (Limoncelli 7-section)
  incident  — postmortem (timeline / RCA / fix / actions)
  reference — verified facts about a tool/concept (TL;DR + sources)
  research  — open question under investigation
  strategy  — trading playbook (thesis / setup / entry / exit / risk / backtest)
  how-to    — task procedure (goal / prereqs / steps + verify)
  concept   — explainer for a domain term
  decision  — ADR (context / options / decision / consequences)
  tool      — tool registry entry (install / usage / gotchas)
  misc      — fallback skeleton (current default)

`render(topic_type, slug, title, today, parents, summary="")` returns the
full markdown body (including frontmatter). Unknown type → misc fallback.
"""
from __future__ import annotations

TYPES = ("runbook", "incident", "reference", "research", "strategy",
         "how-to", "concept", "decision", "tool", "misc")


def _frontmatter(slug: str, title: str, today: str, parents: list[str], topic_type: str) -> str:
    return (
        f"---\n"
        f"slug: {slug}\n"
        f"title: {title}\n"
        f"type: {topic_type}\n"
        f"status: draft\n"
        f"created: {today}\n"
        f"last_touched: {today}\n"
        f"parents: [{', '.join(parents)}]\n"
        f"links: []\n"
        f"aliases: []\n"
        f"---\n\n"
    )


_BODIES = {
    "runbook": (
        "# {title}\n\n"
        "> Cốt lõi: {summary}\n\n"
        "## Triggers\n"
        "> Alert names + thresholds gọi runbook này.\n"
        "- \n\n"
        "## Symptoms\n"
        "> Operator/user thấy gì.\n"
        "- \n\n"
        "## Diagnostics\n"
        "> Commands xác nhận + expected output.\n"
        "- \n\n"
        "## Resolution\n"
        "> Numbered steps, copy-paste safe.\n"
        "1. \n\n"
        "## Rollback\n"
        "> Nếu Resolution làm xấu hơn.\n"
        "- \n\n"
        "## Escalation\n"
        "> Khi nào page ai (oncall, vendor).\n"
        "- \n\n"
        "## See also\n"
        "- \n"
    ),
    "incident": (
        "# {title}\n\n"
        "> Cốt lõi: {summary}\n\n"
        "## Timeline (UTC)\n"
        "> `HH:MM — event` (newest at bottom).\n"
        "- \n\n"
        "## Impact\n"
        "> Services + users affected, duration, severity.\n"
        "- \n\n"
        "## Root cause\n"
        "> 5 Whys → underlying cause (không stop ở proximate).\n"
        "- \n\n"
        "## Resolution\n"
        "> Cái thực sự stop the bleeding.\n"
        "- \n\n"
        "## Action items\n"
        "> Owner + deadline, mỗi item = 1 PR/ticket.\n"
        "- [ ] \n\n"
        "## Source\n"
        "> Ticket / Slack thread / dashboard link.\n"
        "- \n"
    ),
    "reference": (
        "# {title}\n\n"
        "> Cốt lõi: {summary}\n\n"
        "## TL;DR\n"
        "> 1-2 dòng essence — đọc xong là biết dùng để làm gì.\n"
        "- \n\n"
        "## Key concepts\n"
        "> Term → 1-line definition.\n"
        "- \n\n"
        "## Gotchas\n"
        "> Non-obvious behaviour, version quirks, breaking changes.\n"
        "- \n\n"
        "## Sources\n"
        "> URL + version + retrieved date. **BẮT BUỘC.**\n"
        "- \n"
    ),
    "research": (
        "# {title}\n\n"
        "> Cốt lõi: {summary}\n\n"
        "## Question\n"
        "> Câu hỏi cần trả lời, đo lường được.\n"
        "- \n\n"
        "## Hypothesis\n"
        "> Tin ban đầu (có thể sai). Note rõ assumptions.\n"
        "- \n\n"
        "## Findings\n"
        "> Mỗi finding = 1 dòng + (Source: ...).\n"
        "- \n\n"
        "## Decision / Recommendation\n"
        "> Dựa trên findings, làm gì tiếp.\n"
        "- \n"
    ),
    "strategy": (
        "# {title}\n\n"
        "> Cốt lõi: {summary}\n\n"
        "## Thesis\n"
        "> Tại sao strategy work — market mechanic / edge.\n"
        "- \n\n"
        "## Setup\n"
        "> Conditions/regime/instruments. Code-able filter.\n"
        "- \n\n"
        "## Entry rules\n"
        "> Trigger chính xác — không ambiguity.\n"
        "- \n\n"
        "## Exit rules\n"
        "> TP / SL / time-stop / signal reversal.\n"
        "- \n\n"
        "## Risk parameters\n"
        "> Position size, max DD/trade, correlation cap, daily loss cap.\n"
        "- \n\n"
        "## Backtest\n"
        "> Period / win-rate / Sharpe / max DD / sample size.\n"
        "- \n\n"
        "## Forward / Live\n"
        "> Paper trade results, slippage adjustments, live PnL.\n"
        "- \n\n"
        "## Source\n"
        "> Book / paper / trader / repo.\n"
        "- \n"
    ),
    "how-to": (
        "# {title}\n\n"
        "> Cốt lõi: {summary}\n\n"
        "## Goal\n"
        "> Sau khi làm xong, đạt được gì (đo lường được).\n"
        "- \n\n"
        "## Prerequisites\n"
        "> Tools, access, env vars, version pins.\n"
        "- \n\n"
        "## Steps\n"
        "> Numbered, mỗi step kèm `verify:` check.\n"
        "1. — verify: \n\n"
        "## Common errors\n"
        "> Error message → fix.\n"
        "- \n\n"
        "## Source\n"
        "- \n"
    ),
    "concept": (
        "# {title}\n\n"
        "> Cốt lõi: {summary}\n\n"
        "## Definition\n"
        "> 1 câu, không jargon thừa.\n"
        "- \n\n"
        "## Why it exists\n"
        "> Problem nó solve.\n"
        "- \n\n"
        "## Examples\n"
        "> Concrete instance, không trừu tượng.\n"
        "- \n\n"
        "## Related\n"
        "> [[wikilinks]] tới concept liền kề.\n"
        "- \n"
    ),
    "decision": (
        "# {title}\n\n"
        "> Cốt lõi: {summary}\n\n"
        "## Context\n"
        "> Situation forcing the decision (constraints + forces).\n"
        "- \n\n"
        "## Options\n"
        "> Mỗi option: pros / cons / cost.\n"
        "1. \n\n"
        "## Decision\n"
        "> Chọn option nào + 1-line rationale.\n"
        "- \n\n"
        "## Consequences\n"
        "> Trade-offs accept (cả positive + negative).\n"
        "- \n\n"
        "## Status\n"
        "> active / superseded by [[...]] / deprecated.\n"
        "- active\n"
    ),
    "tool": (
        "# {title}\n\n"
        "> Cốt lõi: {summary}\n\n"
        "## Purpose\n"
        "> Tool này solve problem gì.\n"
        "- \n\n"
        "## Install\n"
        "> Command + version pin.\n"
        "```\n"
        "\n"
        "```\n\n"
        "## Usage\n"
        "> Canonical recipe — copy-paste run được.\n"
        "```\n"
        "\n"
        "```\n\n"
        "## Gotchas\n"
        "> Version quirks, breaking changes, platform diffs.\n"
        "- \n\n"
        "## Source\n"
        "> Homepage / repo / docs.\n"
        "- \n"
    ),
    "misc": (
        "# {title}\n\n"
        "> Cốt lõi: {summary}\n\n"
        "## [exp]\n"
        "> Episodic — debug logs, lessons-learned.\n"
        "(empty)\n\n"
        "## [ref]\n"
        "> Verified facts (Source: BẮT BUỘC).\n"
        "(empty)\n\n"
        "## [decision]\n"
        "> Quyết định + rationale.\n"
        "(empty)\n\n"
        "## [reflection]\n"
        "> Pattern observed, hypothesis to test.\n"
        "(empty)\n"
    ),
}


def render(topic_type: str, slug: str, title: str, today: str,
           parents: list[str] | None = None, summary: str = "") -> str:
    """Render full markdown body (frontmatter + body) for a new topic."""
    if topic_type not in _BODIES:
        topic_type = "misc"
    parents = parents or []
    summary = summary or "Cốt lõi 1 dòng (TODO)."
    body = _BODIES[topic_type].format(title=title, summary=summary)
    return _frontmatter(slug, title, today, parents, topic_type) + body
