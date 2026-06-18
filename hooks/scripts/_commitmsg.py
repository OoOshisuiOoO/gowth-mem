#!/usr/bin/env python3
"""Deterministic commit-message generator (v3.6) — make the memory repo's git
history a real, drill-downable audit trail.

The plugin auto-commits ~/.gowth-mem/ on hooks. The old messages
("auto-sync from Mac", "pre-compact snapshot from Mac") told no story, so
`git log` was useless for understanding what knowledge changed. This builds a
structured Conventional-Commits-style message **from the staged diff alone**
(no LLM, fully deterministic — same diff → same message):

    add(trade): +2 [decision] +1 lesson in exness-100k-ea

    Why: lock acct 109680411 (because Real6 is the canonical funded book)

    - 4 files changed, +37 / -2 lines
    - Focus: trade/exness-100k-ea, trade/journal
    - Largest: exness-100k-ea/2026-06-18-mm.md (+21/-0)
    When: 2026-06-18 (knowledge date)

    Workspace: trade
    Topics: exness-100k-ea
    Entries: +3 ~0 -0
    Files: 4
    Why-Code: record-decision
    Machine: mac
    Context: stop-sync

The v3.9 `Why:` line (WHY) + subject (WHAT) + `When:` (WHEN) make a single
`git log` entry self-explaining before anyone opens the diff. So
`git log --grep 'Workspace: trade'`, `git log --grep '^archive('`,
`git log --grep 'Why-Code: verify-claim'`,
`git log -- workspaces/trade/...`, `git log --stat`, and `git blame` all stay
useful. Grounded in deep research (Perplexity 2026-06-18, backend dcc8cb10;
Gemini conv c_c23d12acdbbec4a3): Conventional Commits + path-bucket
classification + git-trailer footers.

Public API:
    build_message(gh: Path, *, host=None, context="", fallback="sync") -> str
"""
from __future__ import annotations

import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _debug import log_debug  # type: ignore

SUBJECT_MAX = 72
HUNK_SCAN_LINE_CAP = 4000   # stop tag-scanning huge diffs (forget/migration bulk)
TAG_RE = re.compile(r"\[(decision|exp|ref|tool|reflection|skill-ref|secret-ref|goal|hypothesis)\]", re.IGNORECASE)
WS_RE = re.compile(r"^workspaces/([^/]+)/(.*)$")
DATED_ASPECT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9-]+\.md$", re.IGNORECASE)

# v3.9 — derive a human "Why:" deterministically from the knowledge diff so a
# single `git log` entry explains the MOTIVATION before anyone opens the diff.
DATE_IN_NAME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})-[a-z0-9-]+\.md$", re.IGNORECASE)
GOAL_TITLE_RE = re.compile(r"^(?:#{2,6}\s*|[-*]\s*)?\[goal\]\s*(.+)", re.IGNORECASE)
DECISION_TITLE_RE = re.compile(r"^(?:#{2,6}\s*|[-*]\s*)?\[decision\]\s*(.+)", re.IGNORECASE)
HYP_TITLE_RE = re.compile(r"^(?:#{2,6}\s*|[-*]\s*)?\[hypothesis\]\s*(.+)", re.IGNORECASE)
RATIONALE_RE = re.compile(r"\b(?:because|since|so that|in order to|due to|vì|bởi|để)\b\s+\S.*", re.IGNORECASE)
WHY_MAX = 160


def _run(gh: Path, *args: str) -> str:
    try:
        r = subprocess.run(["git", "-C", str(gh), *args],
                           capture_output=True, text=True, timeout=10)
        return r.stdout if r.returncode == 0 else ""
    except Exception as e:
        log_debug("commitmsg", f"git {' '.join(args)} failed: {e}")
        return ""


def _bucket(rel: str) -> tuple[str, str | None, str | None]:
    """Classify a path under workspaces/ or shared/. Returns (bucket, ws, topic)."""
    if rel.startswith("shared/"):
        return "shared", None, None
    if rel in ("settings.json", "config.json", "state.json") or rel.endswith(".json"):
        return "meta", None, None
    m = WS_RE.match(rel)
    if not m:
        return "other", None, None
    ws, sub = m.group(1), m.group(2)
    if sub.startswith("journal/"):
        return "journal", ws, None
    if sub == "docs/handoff.md":
        return "handoff", ws, None
    if sub == "docs/handoff-archive.md":
        return "handoff-archive", ws, None
    if sub.startswith("docs/"):
        return "docs", ws, None
    parts = sub.split("/")
    if len(parts) >= 2:
        topic = parts[0]
        fname = parts[-1]
        if fname == "00-README.md":
            return "moc", ws, topic
        if fname == "lessons.md":
            return "lessons", ws, topic
        if DATED_ASPECT_RE.match(fname):
            return "aspect", ws, topic
        return "topic", ws, topic
    return "ws-root", ws, None


def _parse_namestatus(text: str) -> list[tuple[str, str]]:
    """Return [(status, path)] from `--name-status -M`. Renames → use new path."""
    out = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        path = parts[-1]  # new path for renames (R100\told\tnew)
        out.append((status[0], path))  # first char: A/M/D/R/C
    return out


def _parse_numstat(text: str) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        a, d = parts[0], parts[1]
        path = parts[-1]
        try:
            out[path] = (int(a) if a != "-" else 0, int(d) if d != "-" else 0)
        except ValueError:
            out[path] = (0, 0)
    return out


def _count_tags(gh: Path) -> tuple[Counter, Counter]:
    """Count [type] tags on added/removed lines (capped for huge diffs)."""
    added, removed = Counter(), Counter()
    text = _run(gh, "diff", "--cached", "--unified=0", "--no-color", "-M")
    for i, line in enumerate(text.splitlines()):
        if i > HUNK_SCAN_LINE_CAP:
            break
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            for m in TAG_RE.findall(line):
                added[m.lower()] += 1
        elif line.startswith("-"):
            for m in TAG_RE.findall(line):
                removed[m.lower()] += 1
    return added, removed


def _fmt_tags(c: Counter) -> str:
    return " ".join(f"+{n} [{t}]" for t, n in c.most_common())


def _added_lines(gh: Path) -> list[str]:
    """Text of lines ADDED in the staged diff (leading '+' stripped, capped)."""
    out: list[str] = []
    text = _run(gh, "diff", "--cached", "--unified=0", "--no-color", "-M")
    for i, line in enumerate(text.splitlines()):
        if i > HUNK_SCAN_LINE_CAP:
            break
        if line.startswith("+++"):
            continue
        if line.startswith("+"):
            out.append(line[1:])
    return out


def _derive_why(added: list[str], tags_add: Counter, tags_rem: Counter,
                ctype: str) -> tuple[str, str]:
    """Map the knowledge diff → (human Why sentence, grep-able Why-Code).

    Deterministic, no LLM: prefer a literal rationale lifted from the added
    content (goal/decision title, because-clause), else a template keyed by the
    dominant change. Mirrors the Perplexity 2026-06-19 field→rationale mapping.
    """
    # Strongest signal: an unverified claim became a verified fact.
    if tags_rem.get("hypothesis") and tags_add.get("ref"):
        return ("promote unverified [hypothesis] → verified [ref] after confirmation",
                "verify-claim")

    goal_title = decision_title = hyp_title = rationale = ""
    for ln in added:
        s = ln.strip()
        if not s:
            continue
        if not goal_title:
            m = GOAL_TITLE_RE.match(s)
            if m:
                goal_title = m.group(1).strip(" #")
        if not decision_title:
            m = DECISION_TITLE_RE.match(s)
            if m:
                decision_title = m.group(1).strip(" #")
        if not hyp_title:
            m = HYP_TITLE_RE.match(s)
            if m:
                hyp_title = m.group(1).strip(" #")
        if not rationale:
            m = RATIONALE_RE.search(s)
            if m:
                rationale = m.group(0).strip()

    if goal_title:
        return (f"capture/track objective — {goal_title}"[:WHY_MAX], "capture-objective")
    if decision_title:
        why = f"{decision_title} ({rationale})" if rationale else f"record decision — {decision_title}"
        return (why[:WHY_MAX], "record-decision")
    if rationale:
        return (rationale[:WHY_MAX], "record-decision")
    if hyp_title:
        return (f"log unverified claim pending verification — {hyp_title}"[:WHY_MAX], "log-hypothesis")

    # Template fallbacks keyed by the dominant change type.
    if ctype == "archive":
        return ("forget stale raw journal past the 7d TTL (hippocampal buffer → archive)", "forget-stale")
    if ctype == "prune":
        return ("remove superseded / duplicate entries", "prune-stale")
    if ctype == "consolidate":
        return ("regenerate topic MOC index from current aspects", "rebuild-moc")
    if tags_add.get("ref"):
        return ("record verified finding (cited Source)", "record-finding")
    if tags_add.get("goal"):
        return ("record new objective", "capture-objective")
    if ctype == "add":
        return ("capture new knowledge from this session", "capture")
    return ("revise existing knowledge", "revise")


def build_message(gh: Path, *, host: str | None = None, context: str = "",
                  fallback: str = "sync") -> str:
    """Build a deterministic, structured commit message from the staged diff.

    Returns a fallback one-liner if the diff can't be read.
    """
    name_status = _parse_namestatus(_run(gh, "diff", "--cached", "--name-status", "-M"))
    numstat = _parse_numstat(_run(gh, "diff", "--cached", "--numstat", "-M"))
    if not name_status:
        return f"{fallback}: housekeeping" + (f"\n\nMachine: {host}" if host else "")

    statuses = Counter()
    buckets = Counter()
    ws_set: set[str] = set()
    topics: set[str] = set()
    per_file_lines: dict[str, tuple[int, int]] = {}
    n_add = n_del = n_mod = n_ren = 0
    journal_deletes = 0
    handoff_archive_touched = False

    for status, path in name_status:
        bucket, ws, topic = _bucket(path)
        buckets[bucket] += 1
        if ws:
            ws_set.add(ws)
        if topic:
            topics.add(topic)
        if status == "A":
            n_add += 1
        elif status == "D":
            n_del += 1
            if bucket == "journal":
                journal_deletes += 1
        elif status == "R":
            n_ren += 1
        else:
            n_mod += 1
        if bucket == "handoff-archive":
            handoff_archive_touched = True
        per_file_lines[path] = numstat.get(path, (0, 0))

    tags_add, tags_rem = _count_tags(gh)
    added_lines = _added_lines(gh)
    knowledge_dates = sorted({m.group(1) for p in per_file_lines
                              for m in [DATE_IN_NAME_RE.search(p)] if m})
    total_add = sum(a for a, _ in per_file_lines.values())
    total_del = sum(d for _, d in per_file_lines.values())
    n_files = len(name_status)

    # ── dominant type ──────────────────────────────────────────────
    if journal_deletes >= 3 or handoff_archive_touched:
        ctype = "archive"
    elif n_del > (n_add + n_mod):
        ctype = "prune"                       # file-level deletions dominate
    elif tags_rem and not tags_add and total_del > total_add:
        ctype = "prune"                       # entries removed, none added (line-level prune)
    elif buckets.get("moc", 0) and not (tags_add or tags_rem or n_add):
        ctype = "consolidate"                 # MOC auto-regen only
    elif n_add > n_mod:
        ctype = "add"
    elif n_mod or tags_add or tags_rem:
        ctype = "update"
    else:
        ctype = fallback

    # ── scope ──────────────────────────────────────────────────────
    if len(ws_set) == 1:
        scope = next(iter(ws_set))
    elif len(ws_set) > 1:
        scope = "multi"
    elif buckets.get("shared"):
        scope = "shared"
    elif buckets.get("meta"):
        scope = "meta"
    else:
        scope = "vault"

    # ── subject summary ────────────────────────────────────────────
    bits: list[str] = []
    if ctype == "archive" and journal_deletes:
        bits.append(f"forget {journal_deletes} raw journal{'s' if journal_deletes != 1 else ''}")
    if handoff_archive_touched:
        bits.append("rotate handoff")
    if tags_add:
        bits.append(_fmt_tags(tags_add))
    if tags_rem and not tags_add:
        bits.append(" ".join(f"-{n} [{t}]" for t, n in tags_rem.most_common()))
    if buckets.get("lessons") and "lesson" not in " ".join(bits):
        bits.append(f"{buckets['lessons']} lesson{'s' if buckets['lessons'] != 1 else ''}")
    if buckets.get("journal") and not journal_deletes and not bits:
        bits.append("journal snapshot")
    if not bits:
        # generic verb by dominant bucket
        top_bucket = buckets.most_common(1)[0][0] if buckets else "files"
        verb = {"add": "add", "update": "update", "prune": "prune",
                "archive": "archive", "consolidate": "rebuild", "sync": "sync"}.get(ctype, "update")
        bits.append(f"{verb} {top_bucket}")
    if topics:
        tl = sorted(topics)
        bits.append("in " + ", ".join(tl[:2]) + (f" +{len(tl) - 2}" if len(tl) > 2 else ""))

    summary = "; ".join(bits)
    subject = f"{ctype}({scope}): {summary}"
    if len(subject) > SUBJECT_MAX:
        subject = subject[: SUBJECT_MAX - 1].rstrip() + "…"

    # ── body: WHY (motivation) → WHAT (stats) → WHEN (knowledge date) ──
    why_text, why_code = _derive_why(added_lines, tags_add, tags_rem, ctype)
    body: list[str] = [f"Why: {why_text}", ""]
    body.append(f"- {n_files} file{'s' if n_files != 1 else ''} changed, +{total_add} / -{total_del} lines")
    focus = [b for b, _ in buckets.most_common(3) if b not in ("meta",)]
    if focus:
        body.append("- Focus: " + ", ".join(focus))
    largest = sorted(per_file_lines.items(), key=lambda kv: kv[1][0] + kv[1][1], reverse=True)[:3]
    largest = [(p, ad) for p, ad in largest if ad[0] + ad[1] > 0]
    if largest:
        body.append("- Largest: " + ", ".join(
            f"{Path(p).name} (+{a}/-{d})" for p, (a, d) in largest))
    if knowledge_dates:
        when = (knowledge_dates[0] if len(knowledge_dates) == 1
                else f"{knowledge_dates[0]}..{knowledge_dates[-1]}")
        body.append(f"When: {when} (knowledge date)")

    # ── trailers (grep-able) ───────────────────────────────────────
    trailers: list[str] = []
    if ws_set:
        trailers.append("Workspace: " + ", ".join(sorted(ws_set)))
    if topics:
        trailers.append("Topics: " + ", ".join(sorted(topics)[:8]))
    if tags_add or tags_rem:
        ec = " ".join(filter(None, [
            " ".join(f"+{n} {t}" for t, n in tags_add.most_common()),
            " ".join(f"-{n} {t}" for t, n in tags_rem.most_common()),
        ]))
        trailers.append("Entries: " + ec)
    trailers.append(f"Files: {n_files} (+{n_add} ~{n_mod} -{n_del}" + (f" R{n_ren}" if n_ren else "") + ")")
    trailers.append(f"Why-Code: {why_code}")
    if host:
        trailers.append(f"Machine: {host}")
    if context:
        trailers.append(f"Context: {context}")

    return subject + "\n\n" + "\n".join(body) + "\n\n" + "\n".join(trailers)


if __name__ == "__main__":
    # Manual smoke test: print the message for the currently-staged diff.
    import argparse
    from _home import gowth_home  # type: ignore
    ap = argparse.ArgumentParser()
    ap.add_argument("--cwd", default=str(gowth_home()))
    ap.add_argument("--host", default="local")
    ap.add_argument("--context", default="manual")
    a = ap.parse_args()
    print(build_message(Path(a.cwd), host=a.host, context=a.context))
