#!/usr/bin/env python3
"""Topic cards (v4.9) — the curated, budgeted current truth of one topic.

A card lives INSIDE the topic README's `## TL;DR`, after the one-line summary —
the region `_moc` rebuilds keep byte-for-byte:

    ## TL;DR

    > <one-line summary>

    <!-- gowth-mem:card v=1 updated=2026-11-07 -->
    ### Core
    - <1-3 lines: what this topic is, the current truth>
    ### State (as of 2026-11-07)
    - <dated current facts, newest first>
    ### Routing
    | Question | Start | Then |
    |---|---|---|
    | <question> | [[<aspect-or-lessons>]] | <next file or check> |
    ### Gotchas
    - <rule paid for, with the incident date>
    ### Sources
    - <path:line | URL | commit>
    <!-- /gowth-mem:card -->

Library — pure and linear: every scan is line-based string work (the one regex
used is `_gate`'s Vietnamese character class), so nothing here can backtrack
on a hostile README:
  card_state(text)  -> ("none"|"ok"|"unbalanced"|"duplicate"|"outside", region|None)
  card_region(text) -> (start, end) | None     the begin line through the end line
  card_spans(text)  -> [(start, end)]          every card, corrupt ones too (`_moc`)
  has_card(text)    -> bool
  tldr_span(text)   -> (start, end) | None     the TL;DR as `_moc` locates it (first hit)
  tldr_heading_bad(text) -> bool               a "## TL;DR" whose first hit is no heading line
  read_card(readme_path, max_bytes=None) -> {"body", "updated", "path"} | None
  lint(body, settings=None) -> (ok, reason)
  strip_card(text)        the README text routing scores (`_topic._pick_topic`)
  drop_marker_lines(text) the text the indexer chunks (`_index._index_one`)
  validate_issues(text, settings=None)  the `card-*` findings of `_validate.py`
  topic_status(readme, settings=None)   one `--check` row

CLI — writes hold file_lock("moc", 10), the lock `_moc` rebuilds take:
  _card.py --ws <ws> --topic <slug> --show             # the card body (nothing if none); rc 0
  _card.py --ws <ws> --topic <slug> --set FILE|-       # "<readme>\t<status>"
  _card.py --ws <ws> --topic <slug> --summary "<line>" # sets the TL;DR one-liner
  _card.py --ws <ws> --check [--json]                  # card findings for every topic
Status: written | confirmed (same body: only `updated=` moves to today) |
unchanged | rejected:<reason> — bad_ws, no_topic (a reserved workspace dir
such as `memory` included), lock_timeout, markers_corrupt, tldr_heading (the
README's first "## TL;DR" is no heading line of its own: mid-line, `### TL;DR`,
inside the frontmatter), archive_failed, unreadable, secret_leak /
sanitize_failed (also when `safe_write`'s sanitize would change the WHOLE
README — a hand-pasted secret, a `<private>` … `</private>` pair: nothing is
written), or a lint rule (`private_tag`: a curated field never holds a
`<private>` / `</private>` tag). A replaced card is archived verbatim to
`<topic>/.history/card-<sha12 of the old region>.txt` first; a `_moc` rebuild
archives a card it would drop the same way (header `moc-rebuild`).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from contextlib import contextmanager
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import atomic_write, safe_write  # type: ignore
from _home import (  # type: ignore
    TOPIC_README,
    active_workspace,
    gowth_home,
    is_dated_aspect_filename,
    iter_topic_landings,
    read_settings,
    setting,
)
from _lock import file_lock  # type: ignore

BEGIN_PREFIX = "<!-- gowth-mem:card"
END_MARKER = "<!-- /gowth-mem:card -->"
CARD_VERSION = "1"
TLDR_HEADING = "## TL;DR"
# The README sections `_moc` regenerates / locates by text search: a card must
# never contain them (a region that does has swallowed part of the README).
_MOC_SECTIONS = ("## Aspects (auto)", "## Cross-links (manual)")
# The skeleton's placeholder (`_topic_templates.render`, `_moc`); a summary
# equal to it is a TODO. `_validate._is_pristine_skeleton` keys on it too.
TODO_SUMMARY = "Cốt lõi 1 dòng (TODO)."
CONTROL_TOKEN = "[gowth-mem:"
# `_prune` deletes an entry carrying one of these, and the canon forbids them
# in curated text: a card states the current truth, it does not retire entries.
FORBIDDEN = ("(superseded", "(deprecated", "(obsolete")
ENTRY_TYPES = frozenset({"decision", "exp", "ref", "tool", "reflection", "skill-ref",
                         "secret-ref", "goal", "hypothesis"})
DEFAULT_MAX_CHARS = 2000
SUMMARY_MAX_CHARS = 160
LOCK_TIMEOUT = 10.0
_HINT = "gowth-mem:card"   # in both markers: a README without it has no card


# ── line scanning ───────────────────────────────────────────────────────────

def _line_spans(text: str) -> list:
    """(start, end) of every "\\n"-separated line; `end` excludes the "\\n".
    `_moc` splits sections on "\\n" too, so both agree on what a line is."""
    spans = []
    pos = 0
    n = len(text)
    while True:
        nl = text.find("\n", pos)
        if nl < 0:
            spans.append((pos, n))
            return spans
        spans.append((pos, nl))
        pos = nl + 1


def is_begin_marker(line: str) -> bool:
    """`<!-- gowth-mem:card[ attrs] -->` alone on its line."""
    s = line.strip()
    if len(s) < len(BEGIN_PREFIX) + 3 or not s.startswith(BEGIN_PREFIX) or not s.endswith("-->"):
        return False
    rest = s[len(BEGIN_PREFIX):-3]
    return rest == "" or rest[0] in " \t"


def is_end_marker(line: str) -> bool:
    return line.strip() == END_MARKER


def is_marker_line(line: str) -> bool:
    return is_begin_marker(line) or is_end_marker(line)


def _marker_attrs(line: str) -> dict:
    """`v=1 updated=2026-11-07` → {"v": "1", "updated": "2026-11-07"} (first wins)."""
    s = line.strip()
    out: dict = {}
    for tok in s[len(BEGIN_PREFIX):-3].split():
        k, sep, v = tok.partition("=")
        if sep and k and k not in out:
            out[k] = v
    return out


def _is_iso_date(v: str) -> bool:
    return (isinstance(v, str) and len(v) == 10 and v[4] == "-" and v[7] == "-"
            and (v[:4] + v[5:7] + v[8:]).isascii() and (v[:4] + v[5:7] + v[8:]).isdigit())


def _frontmatter_end(text: str) -> int:
    """Offset just past the closing `---` of a leading frontmatter block, 0
    when there is none: an opening `---` line, then up to the first later line
    that starts with `---` — the block `_frontmatter._FRONT_RE` and `_index`
    strip. Linear (two `find`s; the regex can backtrack on blank-line runs)."""
    if not text.startswith("---"):
        return 0
    nl = text.find("\n")
    if nl < 0 or text[3:nl].strip():
        return 0
    close = text.find("\n---", nl + 1)
    return close + 4 if close >= 0 else 0


def tldr_span(text: str, spans: list | None = None):
    """(start, end) of the `## TL;DR` section — its heading line up to the next
    line that starts with `## ` (EOF if none): the section a `_moc` rebuild
    keeps verbatim. None when the README has no TL;DR heading.

    The heading is located the way `_moc` locates it — the FIRST "## TL;DR" in
    the text — so a suffixed heading (`## TL;DR:`, `## TL;DR — …`,
    `## TL;DR (2026)`) is THE TL;DR here too (review cards#2: an exact-match
    lookup missed it, `--set`/`--summary` added a second TL;DR above it, and
    the next rebuild dropped the user's). None as well when that first hit is
    no heading line of the body — mid-line, inside `### TL;DR`, or inside the
    frontmatter (`tldr_heading_bad`): `_moc` would still take its TL;DR from
    there, so no card is ever placed by another reading.
    `spans` is accepted for the callers' convenience and not needed."""
    idx = text.find(TLDR_HEADING)
    if idx < 0:
        return None
    start = text.rfind("\n", 0, idx) + 1
    if text[start:idx].strip(" \t") or idx < _frontmatter_end(text):
        return None
    nxt = text.find("\n## ", idx)
    return start, (nxt + 1 if nxt >= 0 else len(text))


def tldr_heading_bad(text: str) -> bool:
    """True when the text holds "## TL;DR" but its first hit is no heading
    line — `_moc` would take the TL;DR from there, so a card or summary write
    refuses (`rejected:tldr_heading`) rather than add a second one."""
    return isinstance(text, str) and TLDR_HEADING in text and tldr_span(text) is None


def card_state(text: str):
    """("none"|"ok"|"unbalanced"|"duplicate"|"outside", region|None).

    `ok` = exactly one begin and one end marker line, in that order, the
    begin inside the TL;DR section; region = (start of the begin line, end of
    the end line). The end may sit past a hand-typed `## ` line — that is a
    lint finding (`bad_heading`), and `_moc` keeps the whole region anyway —
    but a region holding the README's own `## Aspects (auto)` / `## Cross-links
    (manual)` heading has swallowed part of the README: `outside`."""
    if not isinstance(text, str) or _HINT not in text:
        return "none", None
    spans = _line_spans(text)
    begins: list = []
    ends: list = []
    for a, b in spans:
        line = text[a:b]
        if is_begin_marker(line):
            begins.append((a, b))
        elif is_end_marker(line):
            ends.append((a, b))
    if not begins and not ends:
        return "none", None
    if len(begins) != len(ends):
        return "unbalanced", None
    if len(begins) > 1:
        return "duplicate", None
    (ba, _bb), (ea, eb) = begins[0], ends[0]
    if ea < ba:
        return "unbalanced", None
    tl = tldr_span(text, spans)
    if tl is None or not (tl[0] < ba < tl[1]):
        return "outside", None
    for a, _b in spans:
        if ba < a < ea and text.startswith(_MOC_SECTIONS, a):
            return "outside", None
    return "ok", (ba, eb)


def card_region(text: str):
    """(start, end) of a well-formed card region, or None (none or corrupt)."""
    state, region = card_state(text)
    return region if state == "ok" else None


def has_card(text: str) -> bool:
    return card_region(text) is not None


def card_spans(text: str) -> list:
    """(start, end) of EVERY card in `text`, corrupt ones included — what a
    `_moc` rebuild must not drop unseen (review cards#4): a begin-marker line
    through the first end-marker line after it. The scan for that end stops
    at another begin marker or a section `_moc` regenerates/locates (`## Aspects
    (auto)`, `## Cross-links (manual)`): a card does not own those. With no end
    marker the span runs to the first `## ` line (as `strip_card`), else to
    where the scan stopped. Linear: every line is scanned at most twice."""
    if not isinstance(text, str) or _HINT not in text:
        return []
    spans = _line_spans(text)
    out: list = []
    n = len(spans)
    i = 0
    while i < n:
        a, b = spans[i]
        if not is_begin_marker(text[a:b]):
            i += 1
            continue
        j = i + 1
        end = None
        h2 = None
        while j < n:
            ja, jb = spans[j]
            line = text[ja:jb]
            if is_end_marker(line):
                end = jb
                j += 1
                break
            if is_begin_marker(line) or text.startswith(_MOC_SECTIONS, ja):
                break
            if h2 is None and text.startswith("## ", ja):
                h2 = ja
            j += 1
        if end is None:
            end = h2 if h2 is not None else (spans[j][0] if j < n else len(text))
        out.append((a, end))
        i = j
    return out


def _region_parts(text: str, region) -> tuple:
    """(begin line, raw body, end line) of a well-formed region."""
    seg = text[region[0]:region[1]]
    first = seg.find("\n")
    last = seg.rfind("\n")
    body = seg[first + 1:last] if last > first else ""
    return seg[:first], body, seg[last + 1:]


def normalize_body(body) -> str:
    """The stored form: LF line ends, no trailing whitespace on a line, no
    leading/trailing blank lines. `--show` prints it; `--set` compares it."""
    if not isinstance(body, str):
        return ""
    lines = [ln.rstrip() for ln in body.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    first = 0
    while first < len(lines) and not lines[first]:
        first += 1
    last = len(lines)
    while last > first and not lines[last - 1]:
        last -= 1
    return "\n".join(lines[first:last])


def card_body(text: str):
    """The normalised body of a well-formed card, or None."""
    region = card_region(text)
    if region is None:
        return None
    return normalize_body(_region_parts(text, region)[1])


def render_region(body: str, updated: str) -> str:
    return f"{BEGIN_PREFIX} v={CARD_VERSION} updated={updated} -->\n{body}\n{END_MARKER}"


def strip_card(text: str) -> str:
    """`text` without card lines (markers included) — what routing scores, so
    a card's Routing/Sources vocabulary never pulls unrelated entries into
    its topic. A begin marker with no end stops at the next `## ` line."""
    if not isinstance(text, str) or _HINT not in text:
        return text
    out = []
    in_card = False
    for line in text.split("\n"):
        if in_card:
            if is_end_marker(line):
                in_card = False
                continue
            if not line.startswith("## "):
                continue
            in_card = False
        elif is_begin_marker(line):
            in_card = True
            continue
        elif is_end_marker(line):
            continue
        out.append(line)
    return "\n".join(out)


def drop_marker_lines(text: str) -> str:
    """`text` without the two marker lines (the card body stays) — what the
    indexer chunks, so `gowth-mem card updated` never becomes searchable."""
    if not isinstance(text, str) or _HINT not in text:
        return text
    return "\n".join(ln for ln in text.split("\n") if not is_marker_line(ln))


def summary_span(text: str, spans: list | None = None, tl=None, region=None):
    """(start, end) of the TL;DR's one-line summary: its first `>` line outside
    the card. None when the TL;DR has none (or there is no TL;DR)."""
    spans = spans if spans is not None else _line_spans(text)
    tl = tl if tl is not None else tldr_span(text, spans)
    if tl is None:
        return None
    for a, b in spans:
        if a <= tl[0]:
            continue
        if a >= tl[1]:
            break
        if region is not None and region[0] <= a <= region[1]:
            continue
        if text[a:b].lstrip().startswith(">"):
            return a, b
    return None


def clean_summary(line) -> str:
    s = (line or "").strip() if isinstance(line, str) else ""
    while s.startswith(">"):
        s = s[1:].lstrip()
    return s


def summary_text(text: str):
    """The summary line without its `>`, or None when the TL;DR has none."""
    state, region = card_state(text)
    span = summary_span(text, region=region if state == "ok" else None)
    return clean_summary(text[span[0]:span[1]]) if span else None


# ── lint ────────────────────────────────────────────────────────────────────

def _max_chars(settings) -> int:
    v = setting("topic_card.max_chars", int, DEFAULT_MAX_CHARS, settings=settings)
    return v if isinstance(v, int) and v > 0 else DEFAULT_MAX_CHARS


def _is_bad_heading(line: str) -> bool:
    """An H1/H2 line (≤3 leading spaces, as Markdown reads it) — `_moc` ends
    the TL;DR at a `## ` line, and an H1 is the README's title."""
    k = 0
    while k < 3 and k < len(line) and line[k] == " ":
        k += 1
    s = line[k:].rstrip()
    if s in ("#", "##"):
        return True
    return s.startswith(("# ", "## ", "#\t", "##\t"))


def _drop_hash_run(s: str):
    """`s` without a leading `#` run (≤6, as `TAG_RE`'s `#{2,6}` and the
    indexer's `#{1,3}` heading allow) and the Unicode whitespace after it;
    None for a longer run (no parser reads `####### [x]` as an entry)."""
    if s[:1] != "#":
        return s
    k = len(s) - len(s.lstrip("#"))
    return s[k:].lstrip() if k <= 6 else None


def _is_entry_part(s: str) -> bool:
    """One `splitlines()` part: [ws] [#-run] [ws] [bullet] [ws] [#-run] [ws]
    `[type]` — the union of `_index` (content `^(?:[-*]\\s*)?(?:#{2,6}\\s*)?\\[`
    after `.lstrip()`, and `^#{1,3}\\s+` headings whose text is tested the same
    way: `### - [x]`, `## ## [x]`), the spec's `^\\s*[-*]?\\s*\\[`, `_gate`,
    `_prune`, `_dedup`, `_memfile` (`## - [decision]`)."""
    s = _drop_hash_run(s.lstrip())
    if s is None:
        return False
    if s[:1] in ("-", "*"):
        s = s[1:].lstrip()
    s = _drop_hash_run(s)
    if s is None or not s.startswith("["):
        return False
    close = s.find("]", 1, 13)
    return close > 1 and s[1:close].lower() in ENTRY_TYPES


def _is_entry_line(line: str) -> bool:
    """`[type]` entry shape (`- [decision] …`, `[ref] …`, `### [exp] …`): the
    parsers of `_index`, `_gate`, `_prune` would read it as an entry.

    Seen the way the parsers see it (review cards#1): `_index.split_chunks`
    splits on `str.splitlines()` (VT, FF, \\x1c-\\x1e, \\x85, U+2028, U+2029
    end a line there, not only "\\n"), and their `\\s` is Unicode whitespace
    (NBSP, EMSP, U+3000 …) = `str.lstrip()`. Linear: a constant number of
    passes over each part. tests/test_cards_fix.py holds a differential fuzz
    against `_index.split_chunks` + `_extract_tag`."""
    for part in line.splitlines() or (line,):
        if _is_entry_part(part):
            return True
    return False


def _structure_issues(body: str) -> list:
    """[(kind, 1-based body line)] for kinds comment / bad_heading / entry_line.
    `## Cross-links (manual)` anywhere in a line is a bad heading too: `_moc`
    finds that section by plain text search, so a mention inside the card
    would be taken for the section and the README rebuilt around it."""
    out = []
    for i, line in enumerate(body.split("\n"), 1):
        if "<!--" in line:
            out.append(("comment", i))
        if _is_bad_heading(line) or _MOC_SECTIONS[1] in line:
            out.append(("bad_heading", i))
        if _is_entry_line(line):
            out.append(("entry_line", i))
    return out


def _secret_count(text: str) -> int:
    try:
        from _privacy import sanitize  # type: ignore
        _clean, n = sanitize(text)
        return n
    except Exception:
        return -1


def _private_tag(text: str):
    """True when `text` holds a `<private>` or `</private>` tag in any spelling
    `_privacy` pairs (IGNORECASE: `<prıvate>`, `</PRİVATE>` — a casefold()
    substring test misses those); None when the patterns cannot be loaded.

    Review gaps-docs#3: each field is sanitised on its own, so `<private>` in
    the summary and `</private>` in a later card body both passed — and
    `safe_write`'s whole-file sanitize then paired them across lines and
    replaced everything between (the begin marker included) with
    `[REDACTED:private-block]`. A curated field never needs the tag."""
    try:
        from _privacy import has_private_tag  # type: ignore
        return has_private_tag(text)
    except Exception:
        return None


def _not_english(text: str, settings) -> bool:
    if not setting("gate.english_only", bool, False, settings=settings):
        return False
    from _gate import VIETNAMESE_RE  # type: ignore
    return len(VIETNAMESE_RE.findall(text)) > 2   # `_gate.evaluate`'s tolerance


def lint(body, settings=None):
    """(ok, reason) for a card body; the first failing rule wins:
    empty · control_token · private_tag (sanitize_failed) · secret_leak
    (sanitize_failed) · comment:<line> · bad_heading:<line> · entry_line:<line>
    · forbidden:<word> · not_english · over_budget:<n>/<max> (code points of
    the normalised body)."""
    s = settings if isinstance(settings, dict) else read_settings()
    body = normalize_body(body)
    if not body:
        return False, "empty"
    if CONTROL_TOKEN in body.lower():
        return False, "control_token"
    tag = _private_tag(body)
    if tag is None:
        return False, "sanitize_failed"
    if tag:
        return False, "private_tag"
    n = _secret_count(body)
    if n < 0:
        return False, "sanitize_failed"
    if n > 0:
        return False, "secret_leak"
    issues = _structure_issues(body)
    for kind in ("comment", "bad_heading", "entry_line"):
        for k, ln in issues:
            if k == kind:
                return False, f"{kind}:{ln}"
    low = body.lower()
    for word in FORBIDDEN:
        if word in low:
            return False, f"forbidden:{word[1:]}"
    if _not_english(body, s):
        return False, "not_english"
    mx = _max_chars(s)
    if len(body) > mx:
        return False, f"over_budget:{len(body)}/{mx}"
    return True, ""


def lint_summary(line, settings=None):
    """(ok, reason, cleaned) for a TL;DR one-liner: one line, ≤160 chars after
    a leading `>` is dropped, no control token, no comment, no `<private>` /
    `</private>` tag (`private_tag`), no secret, English when
    `gate.english_only`."""
    s = settings if isinstance(settings, dict) else read_settings()
    clean = clean_summary(line)
    if not clean:
        return False, "empty", ""
    if len(clean.splitlines()) > 1:
        return False, "multiline", ""
    if CONTROL_TOKEN in clean.lower():
        return False, "control_token", ""
    if "<!--" in clean:
        return False, "comment", ""
    if _MOC_SECTIONS[1] in clean:        # `_moc` finds the manual section by text search
        return False, "reserved_heading", ""
    tag = _private_tag(clean)
    if tag is None:
        return False, "sanitize_failed", ""
    if tag:
        return False, "private_tag", ""
    n = _secret_count(clean)
    if n < 0:
        return False, "sanitize_failed", ""
    if n > 0:
        return False, "secret_leak", ""
    if _not_english(clean, s):
        return False, "not_english", ""
    if len(clean) > SUMMARY_MAX_CHARS:
        return False, f"too_long:{len(clean)}/{SUMMARY_MAX_CHARS}", ""
    return True, "", clean


def validate_issues(text: str, settings=None) -> list:
    """`_validate.py` findings for one README — only when a marker exists:
    card-markers:<unbalanced|duplicate>, card-outside-tldr,
    card-over-budget:<n>/<max>, card-bad-heading:<line>,
    card-entry-line:<line>, card-control-token (README line numbers)."""
    state, region = card_state(text)
    if state == "none":
        return []
    if state in ("unbalanced", "duplicate"):
        return [f"card-markers:{state}"]
    if state == "outside":
        return ["card-outside-tldr"]
    s = settings if isinstance(settings, dict) else read_settings()
    begin_line = text.count("\n", 0, region[0]) + 1
    body = _region_parts(text, region)[1]
    issues = []
    nb = normalize_body(body)
    mx = _max_chars(s)
    if len(nb) > mx:
        issues.append(f"card-over-budget:{len(nb)}/{mx}")
    for kind, ln in _structure_issues(body):
        if kind == "bad_heading":
            issues.append(f"card-bad-heading:{begin_line + ln}")
        elif kind == "entry_line":
            issues.append(f"card-entry-line:{begin_line + ln}")
    if CONTROL_TOKEN in body.lower():
        issues.append("card-control-token")
    return issues


# ── reading ─────────────────────────────────────────────────────────────────

def read_card(readme_path, max_bytes: int | None = None):
    """{"body", "updated", "path"} of the README's card, or None (no card,
    corrupt markers, unreadable). `max_bytes` reads only the file's head — a
    card cut off by it reads as none."""
    p = Path(readme_path)
    try:
        if max_bytes:
            with open(p, "rb") as fh:
                text = fh.read(int(max_bytes)).decode("utf-8", errors="ignore")
        else:
            text = p.read_text(encoding="utf-8", errors="ignore")
    except (OSError, ValueError):
        return None
    region = card_region(text)
    if region is None:
        return None
    begin, body, _end = _region_parts(text, region)
    updated = _marker_attrs(begin).get("updated", "")
    return {"body": normalize_body(body), "updated": updated if _is_iso_date(updated) else "",
            "path": str(p)}


def _vault_rel(p: Path) -> str:
    try:
        return p.resolve().relative_to(gowth_home().resolve()).as_posix()
    except (ValueError, OSError):
        return str(p)


def _newest_aspect_date(folder: Path) -> str:
    newest = ""
    try:
        for e in folder.iterdir():
            if is_dated_aspect_filename(e.name) and e.name[:10] > newest:
                newest = e.name[:10]
    except OSError:
        pass
    return newest


_STATUS_ORDER = ("invalid", "over-budget", "stale", "todo-summary", "none", "ok")


def topic_status(readme, settings=None) -> dict:
    """One `--check` row: card none|ok|invalid, summary todo|ok, stale (card
    `updated=` older than the newest dated aspect), the validator issues, and
    a single `status` (first of invalid, over-budget, stale, todo-summary,
    none, ok that applies)."""
    s = settings if isinstance(settings, dict) else read_settings()
    p = Path(readme)
    row = {"topic": p.parent.name, "path": _vault_rel(p), "card": "none", "summary": "todo",
           "updated": "", "chars": 0, "max": _max_chars(s), "stale": False, "issues": [],
           "status": "none"}
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        row.update(card="invalid", status="invalid", issues=["unreadable"])
        return row
    state, region = card_state(text)
    issues = validate_issues(text, s)
    row["issues"] = issues
    summ = summary_text(text)
    row["summary"] = "todo" if (not summ or summ == TODO_SUMMARY) else "ok"
    over = any(i.startswith("card-over-budget") for i in issues)
    if state == "ok":
        begin, body, _end = _region_parts(text, region)
        updated = _marker_attrs(begin).get("updated", "")
        row["updated"] = updated if _is_iso_date(updated) else ""
        row["chars"] = len(normalize_body(body))
        structural = [i for i in issues if not i.startswith("card-over-budget")]
        row["card"] = "invalid" if structural else "ok"
        newest = _newest_aspect_date(p.parent)
        row["stale"] = bool(row["updated"] and newest and newest > row["updated"])
    elif state != "none":
        row["card"] = "invalid"
    flags = {
        "invalid": row["card"] == "invalid",
        "over-budget": over,
        "stale": row["stale"],
        "todo-summary": row["summary"] == "todo",
        "none": row["card"] == "none",
        "ok": True,
    }
    row["status"] = next(k for k in _STATUS_ORDER if flags[k])
    return row


def check(ws: str, settings=None) -> list:
    """`topic_status` for every topic landing of `ws` (a legacy-only landing —
    `<dir>/<dir>.md`, no README — cannot hold a card: status `legacy`)."""
    s = settings if isinstance(settings, dict) else read_settings()
    rows = []
    for landing in sorted(iter_topic_landings(ws)):
        if landing.name != TOPIC_README:
            rows.append({"topic": landing.parent.name, "path": _vault_rel(landing), "card": "none",
                         "summary": "todo", "updated": "", "chars": 0, "max": _max_chars(s),
                         "stale": False, "issues": [], "status": "legacy"})
            continue
        rows.append(topic_status(landing, s))
    return rows


# ── writing ─────────────────────────────────────────────────────────────────

class _LockTimeout(Exception):
    """The moc lock could not be taken (never an OSError from inside)."""


@contextmanager
def _locked(name: str, timeout: float):
    cm = file_lock(name, timeout)
    try:
        cm.__enter__()
    except TimeoutError as exc:
        raise _LockTimeout(str(exc)) from exc
    try:
        yield
    finally:
        cm.__exit__(None, None, None)


def resolve_readme(ws, slug):
    """(README path, None) for an existing topic, else (None, reason): `bad_ws`,
    or `no_topic` — no such topic, a domain, an ambiguous name, a folder that
    resolves outside the workspace, a legacy-only landing (no 00-README.md),
    or a symlinked README. Same guards as `_lesson.py --topic`; never creates."""
    from _topic import WS_RE, _inside_workspace, resolve_topic_folder  # type: ignore
    if not isinstance(ws, str) or not WS_RE.match(ws):
        return None, "bad_ws"
    if not isinstance(slug, str) or not slug:
        return None, "no_topic"
    try:
        folder = resolve_topic_folder(slug, ws=ws, ensure=False)
    except ValueError:
        return None, "no_topic"
    readme = folder / TOPIC_README
    try:
        if (not folder.is_dir() or not _inside_workspace(folder, ws)
                or readme.is_symlink() or not readme.is_file()):
            return None, "no_topic"
    except OSError:
        return None, "no_topic"
    return readme, None


def _splice_block(text: str, ins: int, block: str) -> str:
    """Insert `block` at line boundary `ins` with one blank line around it."""
    before, after = text[:ins], text[ins:]
    if before:
        if not before.endswith("\n"):
            before += "\n"
        if not before.endswith("\n\n"):
            before += "\n"
    trail = "\n" if (not after or after.startswith("\n")) else "\n\n"
    return before + block + trail + after


def _after_line(text: str, end: int) -> int:
    """The index just past the line that ends at `end` (its "\\n" included)."""
    return end + 1 if end < len(text) and text[end] == "\n" else end


def _with_tldr(text: str, summary: str) -> str:
    """Add `## TL;DR` + `> summary` to a README that has none: after the H1
    when the H1 is its first heading, else at the top of the body."""
    from _frontmatter import _FRONT_RE  # type: ignore
    m = _FRONT_RE.match(text)
    ins = m.end() if m else 0
    for a, b in _line_spans(text):
        if a < ins:
            continue
        if text.startswith("#", a):
            if text.startswith("# ", a):
                ins = _after_line(text, b)
            break
    return _splice_block(text, ins, f"{TLDR_HEADING}\n\n> {summary}")


def _card_insert_at(text: str, spans: list, tl) -> int:
    """After the summary paragraph (the `>` lines from the TL;DR's first `>`
    line on), else right after the TL;DR heading line."""
    heading_end = None
    quote_end = None
    for a, b in spans:
        if a < tl[0]:
            continue
        if a == tl[0]:
            heading_end = b
            continue
        if a >= tl[1]:
            break
        quoted = text[a:b].lstrip().startswith(">")
        if quote_end is not None:
            if not quoted:
                break
            quote_end = b
        elif quoted:
            quote_end = b
    end = quote_end if quote_end is not None else heading_end
    return _after_line(text, end)


def _restamp(begin_line: str, today: str) -> str:
    """The begin marker with only `updated=` set to `today` (other attributes,
    indentation and a CR kept)."""
    lead = begin_line[:len(begin_line) - len(begin_line.lstrip())]
    tail = "\r" if begin_line.endswith("\r") else ""
    toks = []
    seen = False
    for tok in begin_line.strip()[len(BEGIN_PREFIX):-3].split():
        if tok.startswith("updated="):
            if not seen:
                toks.append(f"updated={today}")
                seen = True
            continue
        toks.append(tok)
    if not seen:
        toks.append(f"updated={today}")
    return f"{lead}{BEGIN_PREFIX} {' '.join(toks)} -->{tail}"


def _archive(readme: Path, old_region: str, why: str = "card-replace") -> bool:
    """Write the OLD region verbatim (sanitised: `.history/` syncs) to
    `<topic>/.history/card-<sha12>.txt` and read it back. False = do not
    replace the card (the old text would exist nowhere but git). `why` names
    the writer in the header: `card-replace` (`--set`) or `moc-rebuild` (a
    `_moc` rebuild about to drop a card that is not where a rebuild keeps
    it)."""
    hist = readme.parent / ".history"
    if hist.is_symlink():
        return False
    sha12 = hashlib.sha1(old_region.encode("utf-8")).hexdigest()[:12]
    content = f"# verbatim archive ({why}) of {_vault_rel(readme)}\n{old_region}\n"
    try:
        from _privacy import sanitize  # type: ignore
        cleaned, n = sanitize(content)
    except Exception:
        return False
    if n < 0 or not isinstance(cleaned, str):
        return False
    for i in range(1, 51):
        dst = hist / (f"card-{sha12}.txt" if i == 1 else f"card-{sha12}-{i}.txt")
        if dst.is_symlink():
            continue
        if dst.exists():
            try:
                if dst.read_text() == cleaned:
                    return True     # this exact region is archived already
            except (OSError, UnicodeDecodeError):
                pass
            continue
        try:
            atomic_write(dst, cleaned)
            return dst.read_text() == cleaned
        except (OSError, UnicodeDecodeError):
            return False
    return False


def _read_strict(readme: Path):
    """The README text, or None — never a lossy decode we would write back."""
    try:
        return readme.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _redaction_refusal(new_text: str):
    """None when `safe_write`'s whole-file sanitize would leave `new_text`
    unchanged; else the refusal (`rejected:secret_leak`, or
    `rejected:sanitize_failed` when the filter crashed) — and the caller
    writes NOTHING. Review gaps-docs#3: a `<private>` in one write and a
    `</private>` in another (or in a hand-edited section) were paired across
    lines by that sanitize and everything between them redacted — the card's
    begin marker, `## Aspects (auto)`, `## Cross-links (manual)` — while the
    CLI printed `written`. Every lint passes field by field; this checks the
    file that would actually be written."""
    try:
        from _privacy import sanitize  # type: ignore
        clean, n = sanitize(new_text)
    except Exception:
        return "rejected:sanitize_failed"
    if not isinstance(n, int) or n < 0:
        return "rejected:sanitize_failed"
    if n > 0 or clean != new_text:
        return "rejected:secret_leak"
    return None


def _apply_card(readme: Path, body: str, today: str) -> str:
    text = _read_strict(readme)
    if text is None:
        return "rejected:unreadable"
    state, region = card_state(text)
    if state not in ("none", "ok"):
        return "rejected:markers_corrupt"
    if state == "ok":
        begin, old_body, _end = _region_parts(text, region)
        if normalize_body(old_body) == body:
            if _marker_attrs(begin).get("updated") == today:
                return "unchanged"
            a = region[0]
            new_text = text[:a] + _restamp(begin, today) + text[a + len(begin):]
            refused = _redaction_refusal(new_text)
            if refused:
                return refused
            safe_write(readme, new_text)
            return "confirmed"
        new_text = text[:region[0]] + render_region(body, today) + text[region[1]:]
        refused = _redaction_refusal(new_text)
        if refused:
            return refused
        if not _archive(readme, text[region[0]:region[1]]):
            return "rejected:archive_failed"
    else:
        if tldr_span(text) is None:
            if tldr_heading_bad(text):    # `_moc` would take its TL;DR from that hit
                return "rejected:tldr_heading"
            text = _with_tldr(text, TODO_SUMMARY)   # only when there is no "## TL;DR" at all
        spans = _line_spans(text)
        tl = tldr_span(text, spans)
        if tl is None:                    # defensive: the inserted heading must be THE TL;DR
            return "rejected:tldr_heading"
        new_text = _splice_block(text, _card_insert_at(text, spans, tl), render_region(body, today))
        refused = _redaction_refusal(new_text)
        if refused:
            return refused
    safe_write(readme, new_text)
    return "written"


def _apply_summary(readme: Path, summary: str) -> str:
    text = _read_strict(readme)
    if text is None:
        return "rejected:unreadable"
    state, region = card_state(text)
    if state not in ("none", "ok"):
        return "rejected:markers_corrupt"
    line = f"> {summary}"
    spans = _line_spans(text)
    tl = tldr_span(text, spans)
    if tl is None:
        if tldr_heading_bad(text):        # `_moc` would take its TL;DR from that hit
            return "rejected:tldr_heading"
        new_text = _with_tldr(text, summary)   # only when there is no "## TL;DR" at all
    else:
        span = summary_span(text, spans, tl, region)
        if span is not None:
            a, b = span
            old = text[a:b]
            if old.strip() == line:
                return "unchanged"
            new_text = text[:a] + line + ("\r" if old.endswith("\r") else "") + text[b:]
        else:
            heading_end = next(b for a, b in spans if a == tl[0])
            new_text = _splice_block(text, _after_line(text, heading_end), line)
    refused = _redaction_refusal(new_text)
    if refused:
        return refused
    safe_write(readme, new_text)
    return "written"


def _after_write(ws: str, readme: Path) -> None:
    """Best-effort follow-ups of a README write: make it recallable now, and
    (v4.9 §6.1) rewrite the machine-local short-prompt gate file
    `.curator/cards-terms-<ws>` so a short prompt naming this topic can reach
    its card (`_recall_prompt.refresh_card_terms` ensures the `.curator/`
    ignore line first)."""
    try:
        from _index import reindex_paths  # type: ignore
        reindex_paths([readme])
    except Exception:
        pass
    try:
        from _recall_prompt import refresh_card_terms  # type: ignore
        refresh_card_terms(ws)
    except Exception:
        pass


def set_card(ws, slug, body, settings=None, today: str | None = None):
    """(README | None, status). See the module docstring for the statuses."""
    s = settings if isinstance(settings, dict) else read_settings()
    readme, why = resolve_readme(ws, slug)
    if readme is None:
        return None, f"rejected:{why}"
    body = normalize_body(body)
    ok, reason = lint(body, s)
    if not ok:
        return readme, f"rejected:{reason}"
    today = today or date.today().isoformat()
    try:
        with _locked("moc", LOCK_TIMEOUT):
            status = _apply_card(readme, body, today)
    except _LockTimeout:
        return readme, "rejected:lock_timeout"
    if status in ("written", "confirmed"):
        _after_write(ws, readme)
    return readme, status


def set_summary(ws, slug, line, settings=None):
    """(README | None, status) — replaces the TL;DR's first `>` line outside
    the card (the TODO placeholder included) or inserts one."""
    s = settings if isinstance(settings, dict) else read_settings()
    readme, why = resolve_readme(ws, slug)
    if readme is None:
        return None, f"rejected:{why}"
    ok, reason, clean = lint_summary(line, s)
    if not ok:
        return readme, f"rejected:{reason}"
    try:
        with _locked("moc", LOCK_TIMEOUT):
            status = _apply_summary(readme, clean)
    except _LockTimeout:
        return readme, "rejected:lock_timeout"
    if status == "written":
        _after_write(ws, readme)
    return readme, status


# ── CLI ─────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="_card.py", description="Topic cards (v4.9).")
    ap.add_argument("--ws", help="workspace (default: active)")
    ap.add_argument("--topic", help="topic slug (an existing topic folder)")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--show", action="store_true", help="print the card body")
    mode.add_argument("--set", metavar="FILE|-", help="replace the card body (from FILE or stdin)")
    mode.add_argument("--summary", metavar="LINE", help="set the TL;DR one-line summary")
    mode.add_argument("--check", action="store_true", help="card findings for every topic")
    ap.add_argument("--json", action="store_true", help="with --check: JSON rows")
    args = ap.parse_args(argv)
    ws = args.ws if args.ws is not None else active_workspace()   # an explicit "" is bad_ws

    if args.check:
        from _topic import WS_RE  # type: ignore
        if not WS_RE.match(ws):
            print("rejected:bad_ws")
            return 0
        rows = check(ws)
        if args.json:
            print(json.dumps(rows, indent=2, ensure_ascii=False))
            return 0
        counts: dict = {}
        for r in rows:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
            print("\t".join([r["status"], r["topic"], r["path"]] + list(r["issues"])))
        print(f"cards ws={ws}: {len(rows)} topic(s) — "
              + (", ".join(f"{k} {counts[k]}" for k in sorted(counts)) or "none"))
        return 0

    if not args.topic:
        ap.error("--topic is required with --show, --set and --summary")

    if args.show:
        readme, why = resolve_readme(ws, args.topic)
        if readme is None:
            print(f"rejected:{why}", file=sys.stderr)
            return 0
        card = read_card(readme)
        if card is not None:
            print(card["body"])
        else:
            try:
                state = card_state(readme.read_text(encoding="utf-8", errors="ignore"))[0]
            except OSError:
                state = "none"
            if state != "none":     # stdout stays empty; say why on stderr
                print(f"markers_corrupt ({state}) — see _validate.py --scan", file=sys.stderr)
        return 0

    if args.set is not None:
        try:
            body = sys.stdin.read() if args.set == "-" else Path(args.set).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            print("-\trejected:unreadable_input")
            return 0
        readme, status = set_card(ws, args.topic, body)
    else:
        readme, status = set_summary(ws, args.topic, args.summary)
    print(f"{readme if readme is not None else '-'}\t{status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
