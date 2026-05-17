"""Topic body templates (v3.0).

v3 layout uses ONE universal skeleton (`TOPIC_README_SKELETON`) for every
topic folder's `00-README.md`. Per-type semantics survive via frontmatter
`type:` field (consumed by `_lint.py` + recall ranking + `/mem-doctor`).

Type vocabulary (carried in frontmatter only):
  runbook   — pager playbook
  incident  — postmortem
  reference — verified facts
  research  — open question
  strategy  — trading playbook
  how-to    — task procedure
  concept   — explainer
  decision  — ADR
  tool      — tool registry entry
  misc      — fallback (default)

`render(topic_type, slug, title, today, parents, summary="")` returns the
full `00-README.md` body (frontmatter + universal MOC skeleton). Unknown
`topic_type` is preserved verbatim in frontmatter (it's metadata only now).

Aspect files (`YYYY-MM-DD-<aspect>.md`) use `TOPIC_ASPECT_SKELETON` from
`templates/topic-aspect-skeleton.md` (rendered by `_topic.ensure_topic_folder`
or callers that create the first aspect file).

F18 lock (2026-05-17): dropped v2.8's 10 per-type README bodies — every topic
folder now uses one shape. Less code, no template-drift, matches reference repo.
"""
from __future__ import annotations

TYPES = ("runbook", "incident", "reference", "research", "strategy",
         "how-to", "concept", "decision", "tool", "misc")


def _frontmatter(slug: str, title: str, today: str, parents: list[str],
                 topic_type: str, aliases: list[str] | None = None,
                 tags: list[str] | None = None, maturity: str = "draft") -> str:
    """v3.0 README frontmatter: slug/title/type/status/created/last_touched/parents/links/aliases/tags/maturity."""
    aliases = aliases or []
    tags = tags or []
    return (
        f"---\n"
        f"slug: {slug}\n"
        f"title: {title}\n"
        f"type: {topic_type}\n"
        f"status: draft\n"
        f"maturity: {maturity}\n"
        f"created: {today}\n"
        f"last_touched: {today}\n"
        f"parents: [{', '.join(parents)}]\n"
        f"links: []\n"
        f"aliases: [{', '.join(aliases)}]\n"
        f"tags: [{', '.join(tags)}]\n"
        f"---\n\n"
    )


TOPIC_README_SKELETON = (
    "# {title}\n\n"
    "## TL;DR\n\n"
    "> {summary}\n\n"
    "## Aspects (auto)\n\n"
    "(empty — dated `YYYY-MM-DD-<aspect>.md` siblings appear here after first write)\n\n"
    "## Cross-links (manual)\n\n"
    "(curate `[[wikilinks]]` to related topics here — preserved across MOC rebuilds)\n"
)


TOPIC_ASPECT_SKELETON = (
    "---\n"
    "slug: {slug}\n"
    "aspect: {aspect}\n"
    "date: {today}\n"
    "last_touched: {today}\n"
    "---\n\n"
    "# {title} — {aspect} ({today})\n\n"
    "## Context\n\n"
    "> 1-2 lines describing what this aspect captures.\n\n"
    "## [exp]\n"
    "(empty)\n\n"
    "## [ref]\n"
    "(empty)\n\n"
    "## [decision]\n"
    "(empty)\n\n"
    "## [reflection]\n"
    "(empty)\n"
)


def render(topic_type: str, slug: str, title: str, today: str,
           parents: list[str] | None = None, summary: str = "") -> str:
    """Render the universal `00-README.md` body (frontmatter + MOC skeleton).

    `topic_type` is preserved in frontmatter (semantic tag for lint/recall);
    unknown values are kept as-is. Body shape is identical for all types per
    F18 lock — one skeleton, no template-drift.
    """
    if topic_type not in TYPES:
        topic_type = "misc"
    parents = parents or []
    summary = summary or "Cốt lõi 1 dòng (TODO)."
    body = TOPIC_README_SKELETON.format(title=title, summary=summary)
    return _frontmatter(slug, title, today, parents, topic_type) + body


def render_aspect(slug: str, aspect: str, title: str, today: str) -> str:
    """Render a fresh `YYYY-MM-DD-<aspect>.md` body from the aspect skeleton."""
    return TOPIC_ASPECT_SKELETON.format(
        slug=slug, aspect=aspect, title=title, today=today
    )
