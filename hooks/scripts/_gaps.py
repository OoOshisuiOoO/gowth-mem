#!/usr/bin/env python3
"""Gap ledger (v4.9): the open questions of one workspace, `<ws>/docs/questions.md`.

A gap is something the vault cannot answer yet:
  research — an agent can find the answer (vault, then primary docs/code/web)
             and must save it as a [ref] with Source: before closing it;
  ask      — only the user knows.

The file is managed — every write re-renders it normalised:

    # Open questions — <ws>

    > Gaps in this workspace's memory. ...            (3 managed header lines)

    ## Open
    - Q-1a2b3c4d · research · 2026-11-07 · <question> — why: <evidence> — from: curator · tried 2026-11-09×2
    ## Closed
    - Q-9f8e7d6c · ask · 2026-11-02→2026-11-07 · <question> → <answer> — evidence: <source> — reason: answered

Lines start `- Q-` (never `- [`), so no entry parser, prune rule or gate scan
reads them as memory entries. Parsing is line-based with str.partition on
` · `, ` — ` and ` → ` — no backtracking regex. ID = `Q-` + sha1 of the
normalised question (casefold, collapsed whitespace, trailing `?.!` stripped)
[:8]; a question re-added after it was closed (`--force`) gets the next free
salted id, so the closed record and the new open item never share one.

The vault carries `workspaces/*/docs/questions.md merge=union` in
.gitattributes (`_canon.py`): two machines appending at once merge line-wise
instead of writing SYNC-CONFLICT.md, and `parse()` folds the union back. It is
TOTAL (any text parses):
  * a question's state comes from its LINE SHAPE (`opened→closed` = closed),
    not from the heading above it — a union merge can leave a new open line
    below `## Closed`; it is re-rendered under `## Open`;
  * a closed line wins over every open line with the same id;
  * repeated open (or closed) lines with one id collapse to the first;
  * unknown lines are kept verbatim in their section, in order (blank lines
    and the managed title / header block are regenerated).

Field text is normalised before it is stored: whitespace collapsed, and the
spaces around the separator glyphs `—` `·` (and `→` in a question) removed,
so stored text can never be split as a separator ("done — tick it?" is stored
as "done—tick it?").

CLI (the status goes to stdout; rc 0; argparse usage errors rc 2):
  _gaps.py [--ws <ws>] add --kind research|ask "<question>" [--why "<evidence>"] [--from curator|session|user] [--force]
           (`add research "<question>"` is the same as `add --kind research "<question>"`)
           -> "Q-xxxxxxxx\\tadded|duplicate|similar:Q-yyyyyyyy|rejected:<rule>"
  _gaps.py [--ws <ws>] list [--kind research|ask] [--closed] [--json]
  _gaps.py [--ws <ws>] answer Q-xxxxxxxx "<answer>" --evidence "<path|url|commit|user>"
           -> "Q-xxxxxxxx\\tanswered|not_found|rejected:<rule>"
  _gaps.py [--ws <ws>] close Q-xxxxxxxx --evidence "<…>" --reason obsolete|duplicate|answered-elsewhere|escalated
           -> "Q-xxxxxxxx\\tclosed|not_found|rejected:<rule>"
  _gaps.py [--ws <ws>] tried Q-xxxxxxxx --note "<why not answerable now>"
           -> "Q-xxxxxxxx\\ttried:<n>|not_found|rejected:<rule>"
(`--ws` is also accepted after the subcommand.)

Rules: question 12..300 chars after whitespace collapse (`too_short` /
`too_long`); why / evidence / note <= 500, answer 1..1000 (`too_long` /
`too_short`); `_privacy.sanitize` on every field — a secret is
`secret_leak`; `[gowth-mem:` in any field is `control_token` (a quoted hook
directive could re-trigger a dispatch); `gate.english_only` -> `not_english`;
answer / close need `--evidence` (`no_evidence`); a `research` answer must
cite a vault path, URL or commit (`weak_evidence` if only `user`);
`similar:<id>` when the casefolded `\\w{4,}` token Jaccard against an OPEN
question is >= 0.6, unless `--force`. Other refusals: `bad_ws`, `bad_kind`,
`bad_from`, `bad_reason`, `no_note`, `already_closed`, `no_vault`,
`no_workspace`, `symlink`, `not_a_file`, `outside_workspace`, `sanitize_failed`,
`lock_timeout`.

Writes: lazy create; `file_lock(f"gaps-{ws}", 5)` from read to write (never
nested with `entries-<ws>`); `_atomic.safe_write`; best-effort reindex.

Library: parse(text) -> {"open": [Q], "closed": [Q]}; open_items(ws) -> [Q]
(file order); add / answer / close / tried / list_items. Q = {"id", "kind",
"opened", "closed", "question", "why", "source", "answer", "evidence",
"reason", "tries", "last_tried"}.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import safe_write  # type: ignore
from _debug import log_debug  # type: ignore
from _home import active_workspace, gowth_home, read_settings, setting, workspace_dir  # type: ignore
from _lock import file_lock  # type: ignore

FILE_NAME = "questions.md"
KINDS = ("research", "ask")
SOURCES = ("curator", "session", "user")
CLOSE_REASONS = ("obsolete", "duplicate", "answered-elsewhere", "escalated")
ANSWERED = "answered"

QUESTION_MIN = 12
QUESTION_MAX = 300
FIELD_MAX = 500          # why / evidence / note
ANSWER_MAX = 1000
SIMILAR_MIN = 0.6
LOCK_TIMEOUT = 5.0
CONTROL_TOKEN = "[gowth-mem:"

# The ONLY regex here: the token set of the similarity rule (spec: casefolded
# \w{4,} sets). One greedy class run per token, no nesting — linear.
_TOKEN_RE = re.compile(r"\w{4,}")

SEP = " · "
KEY_SEP = " — "
ANSWER_SEP = " → "
DATE_ARROW = "→"
TRIED_SEP = " · tried "
TIMES = "×"
OPEN_KEYS = ("why", "from")
CLOSED_KEYS = ("evidence", "reason")

TITLE_PREFIX = "# Open questions"
HEADER = (
    "> Gaps in this workspace's memory. `research` = an agent can find the answer "
    "(vault, then primary docs/code/web) and",
    "> must save it as a [ref] with Source: before closing. `ask` = only the user knows. "
    "Managed by `_gaps.py`",
    "> (/mem-ops gap); answer with `/mem-ops gap answer <id> \"<answer>\" --evidence \"<source>\"`.",
)
OPEN_H = "## Open"
CLOSED_H = "## Closed"

_DIGITS = frozenset("0123456789")
_HEX = frozenset("0123456789abcdef")
_WS_FIRST = frozenset("abcdefghijklmnopqrstuvwxyz0123456789")
_WS_CHARS = _WS_FIRST | frozenset("._-")


# ── small pure helpers (no regex) ─────────────────────────────────────────

def _is_date(s: str) -> bool:
    return (len(s) == 10 and s[4] == "-" and s[7] == "-"
            and all(c in _DIGITS for c in s[:4] + s[5:7] + s[8:]))


def _is_qid(s: str) -> bool:
    return len(s) == 10 and s.startswith("Q-") and all(c in _HEX for c in s[2:])


def valid_ws(ws) -> bool:
    """Same contract as `_topic.WS_RE` (`^[a-z0-9][a-z0-9._-]{0,63}$`)."""
    return (isinstance(ws, str) and 0 < len(ws) <= 64 and ws[0] in _WS_FIRST
            and all(c in _WS_CHARS for c in ws))


def _collapse(s) -> str:
    return " ".join(str(s or "").split())


def _clean(s, *, question: bool = False) -> str:
    """Collapse whitespace; remove the spaces around the separator glyphs so a
    stored field can never contain ` — ` / ` · ` (or ` → ` in a question)."""
    out = _collapse(s)
    for g in (("—", "·", "→") if question else ("—", "·")):
        if g in out:
            out = out.replace(f" {g} ", g).replace(f" {g}", g).replace(f"{g} ", g)
    return out


def _norm(question: str) -> str:
    return _collapse(question).casefold().rstrip("?.! ")


def qid_for(question: str, salt: int = 0) -> str:
    """`Q-` + sha1(normalised question)[:8]; salt >= 2 for a re-added question."""
    data = _norm(question) if salt < 2 else f"{_norm(question)}\x00{salt}"
    return "Q-" + hashlib.sha1(data.encode("utf-8")).hexdigest()[:8]


def _norm_qid(s) -> str:
    s = str(s or "").strip()
    if s[:2].upper() == "Q-":
        s = "Q-" + s[2:].lower()
    return s


def _blank() -> dict:
    return {"id": "", "kind": "", "opened": "", "closed": "", "question": "", "why": "",
            "source": "", "answer": "", "evidence": "", "reason": "", "tries": 0,
            "last_tried": ""}


def _today() -> str:
    return date.today().isoformat()


# ── line grammar ───────────────────────────────────────────────────────────

def _strip_keys(rest: str, allowed: tuple) -> tuple:
    """Consume trailing ` — <key>: <value>` segments, right to left. Stops at the
    first segment that is not an allowed key, repeats a key, or is empty — so
    whatever stays to the left re-renders and re-parses to the same fields."""
    found: dict = {}
    while True:
        head, sep, tail = rest.rpartition(KEY_SEP)
        if not sep:
            break
        key, colon, value = tail.partition(": ")
        value = value.strip()
        if not colon or key not in allowed or key in found or not value:
            break
        found[key] = value
        rest = head
    return rest, found


def _parse_line(line: str):
    """(Q, is_closed) for a well-formed `- Q-` line, else None."""
    if not line.startswith("- Q-"):
        return None
    parts = line[2:].split(SEP, 3)
    if len(parts) != 4:
        return None
    qid, kind, dates, rest = parts
    if not _is_qid(qid) or kind not in KINDS:
        return None
    q = _blank()
    q["id"], q["kind"] = qid, kind
    opened, arrow, closed = dates.partition(DATE_ARROW)
    if arrow:
        if not (_is_date(opened) and _is_date(closed)):
            return None
        q["opened"], q["closed"] = opened, closed
        rest, found = _strip_keys(rest, CLOSED_KEYS)
        q["evidence"] = found.get("evidence", "")
        q["reason"] = found.get("reason", "")
        text, sep, ans = rest.partition(ANSWER_SEP)
        q["question"] = text.strip()
        q["answer"] = ans.strip() if sep else ""
        return q, True
    if not _is_date(dates):
        return None
    q["opened"] = dates
    head, sep, tail = rest.rpartition(TRIED_SEP)
    if sep:
        day, x, n = tail.partition(TIMES)
        if x and _is_date(day) and 0 < len(n) <= 6 and all(c in _DIGITS for c in n) and int(n) > 0:
            q["tries"], q["last_tried"] = int(n), day
            rest = head
    rest, found = _strip_keys(rest, OPEN_KEYS)
    q["why"] = found.get("why", "")
    q["source"] = found.get("from", "")
    q["question"] = rest.strip()
    return q, False


def render_line(q: dict) -> str:
    """The canonical line for one question (inverse of `_parse_line`)."""
    if q.get("closed"):
        out = (f"- {q['id']}{SEP}{q['kind']}{SEP}{q['opened']}{DATE_ARROW}{q['closed']}"
               f"{SEP}{q['question']}")
        if q.get("answer"):
            out += ANSWER_SEP + q["answer"]
        if q.get("evidence"):
            out += f"{KEY_SEP}evidence: {q['evidence']}"
        if q.get("reason"):
            out += f"{KEY_SEP}reason: {q['reason']}"
        return out
    out = f"- {q['id']}{SEP}{q['kind']}{SEP}{q['opened']}{SEP}{q['question']}"
    if q.get("why"):
        out += f"{KEY_SEP}why: {q['why']}"
    if q.get("source"):
        out += f"{KEY_SEP}from: {q['source']}"
    if int(q.get("tries") or 0) > 0 and q.get("last_tried"):
        out += f"{TRIED_SEP}{q['last_tried']}{TIMES}{int(q['tries'])}"
    return out


# ── document model ─────────────────────────────────────────────────────────

class _Doc:
    """pre: unknown lines above the first section heading; open / closed:
    [("q", Q) | ("raw", line)] in render order."""

    def __init__(self) -> None:
        self.pre: list = []
        self.open: list = []
        self.closed: list = []

    def questions(self, section: str) -> list:
        return [p for k, p in getattr(self, section) if k == "q"]

    def find(self, section: str, qid: str):
        for i, (k, p) in enumerate(getattr(self, section)):
            if k == "q" and p["id"] == qid:
                return i
        return None

    def render(self, ws: str) -> str:
        out = [f"{TITLE_PREFIX} — {ws}", "", *HEADER, ""]
        if self.pre:
            out += self.pre + [""]
        out.append(OPEN_H)
        out += [render_line(p) if k == "q" else p for k, p in self.open]
        out.append(CLOSED_H)
        out += [render_line(p) if k == "q" else p for k, p in self.closed]
        return "\n".join(out) + "\n"


def _load(text: str) -> _Doc:
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    section = "pre"
    items: list = []          # (section, kind, payload)
    for line in lines:
        s = line.strip()
        if s == OPEN_H:
            section = "open"
            continue
        if s == CLOSED_H:
            section = "closed"
            continue
        parsed = _parse_line(line.rstrip())
        if parsed is not None:
            q, is_closed = parsed
            items.append(("closed" if is_closed else "open", "q", q))
            continue
        if not s:
            continue                      # blank lines are regenerated
        if section == "pre" and (s.startswith(TITLE_PREFIX) or s.startswith(">")):
            continue                      # the managed title + header block
        items.append((section, "raw", line))
    winner: dict = {}                     # id -> index of its first closed line
    for i, (sec, kind, p) in enumerate(items):
        if kind == "q" and sec == "closed" and p["id"] not in winner:
            winner[p["id"]] = i
    seen_open: set = set()
    doc = _Doc()
    for i, (sec, kind, p) in enumerate(items):
        if kind == "q":
            if sec == "closed":
                if winner.get(p["id"]) != i:
                    continue              # a repeated closed line
            elif p["id"] in winner or p["id"] in seen_open:
                continue                  # closed wins / repeated open line
            else:
                seen_open.add(p["id"])
        if sec == "pre":
            doc.pre.append(p)
        else:
            getattr(doc, sec).append((kind, p))
    return doc


def parse(text) -> dict:
    """TOTAL parse of a questions.md text: {"open": [Q], "closed": [Q]}."""
    doc = _load(text if isinstance(text, str) else "")
    return {"open": [dict(q) for q in doc.questions("open")],
            "closed": [dict(q) for q in doc.questions("closed")]}


# ── paths, reads ───────────────────────────────────────────────────────────

def questions_path(ws: str) -> Path:
    return workspace_dir(ws) / "docs" / FILE_NAME


def _read(path: Path) -> str:
    try:
        if path.is_symlink() or not path.is_file():
            return ""
        return path.read_text(errors="replace")
    except OSError:
        return ""


def list_items(ws: str, *, closed: bool = False, kind: str = "") -> list:
    if not valid_ws(ws):
        return []
    items = parse(_read(questions_path(ws)))["closed" if closed else "open"]
    return [q for q in items if not kind or q["kind"] == kind]


def open_items(ws: "str | None" = None) -> list:
    """Open questions of `ws` (default: active) in file order; [] on any error."""
    try:
        return list_items(ws or active_workspace())
    except Exception as exc:
        log_debug("gaps", f"open_items failed: {exc}")
        return []


# ── validation ─────────────────────────────────────────────────────────────

def _field_rule(fields: dict, settings: dict) -> str:
    """'' or the first refusal: secret_leak (or sanitize_failed when the filter
    itself failed — fail closed), control_token, not_english."""
    from _privacy import sanitize  # type: ignore
    for value in fields.values():
        if value:
            _clean_text, n = sanitize(value)
            if n > 0:
                return "secret_leak"
            if n < 0:
                return "sanitize_failed"
    for value in fields.values():
        if value and CONTROL_TOKEN in value.casefold():
            return "control_token"
    if setting("gate.english_only", bool, False, settings=settings):
        from _gate import VIETNAMESE_RE  # type: ignore
        for value in fields.values():
            if value and len(VIETNAMESE_RE.findall(value)) > 2:
                return "not_english"
    return ""


def _token_ok(tok: str) -> bool:
    """URL, commit (7-40 hex with a digit, optionally `repo@sha`) or a path."""
    low = tok.casefold()
    if low.startswith(("http://", "https://")) and len(tok) > 10:
        return True
    sha = tok.rpartition("@")[2]
    if 7 <= len(sha) <= 40 and all(c in _HEX for c in sha.lower()) and any(c in _DIGITS for c in sha):
        return True
    name = tok.rsplit(":", 1)[0] if ":" in tok and tok.rsplit(":", 1)[1].isdigit() else tok
    last = name.rstrip("/").rpartition("/")[2]
    stem, dot, ext = last.rpartition(".")
    return bool(dot and stem and 1 <= len(ext) <= 8 and ext.isalnum()
                and ("/" in name or ext.lower() == "md"))


def strong_evidence(evidence: str) -> bool:
    """True when the evidence cites a vault path, URL or commit — not just `user`."""
    for raw in str(evidence or "").split():
        tok = raw.strip("()[]<>{},;\"'`").rstrip(".!?:")
        if tok and _token_ok(tok):
            return True
    return False


def _tokens(text: str) -> set:
    return set(_TOKEN_RE.findall(text.casefold()))


def _similar(doc: _Doc, question: str) -> str:
    mine = _tokens(question)
    if not mine:
        return ""
    best, best_id = 0.0, ""
    for q in doc.questions("open"):
        theirs = _tokens(q["question"])
        if not theirs:
            continue
        j = len(mine & theirs) / len(mine | theirs)
        if j > best:
            best, best_id = j, q["id"]
    return best_id if best >= SIMILAR_MIN else ""


def _target(ws: str):
    """(path, '') when `ws`'s ledger may be written, else (None, rule)."""
    gh = gowth_home()
    if not gh.is_dir():
        return None, "no_vault"
    wsd = workspace_dir(ws)
    if not wsd.is_dir() and ws != active_workspace():
        return None, "no_workspace"        # a typo'd --ws must not mint a workspace
    path = wsd / "docs" / FILE_NAME
    if path.is_symlink():
        return None, "symlink"
    if path.exists() and not path.is_file():
        return None, "not_a_file"
    docs = wsd / "docs"
    if docs.exists():
        try:
            docs.resolve().relative_to(wsd.resolve())
        except (ValueError, OSError):
            return None, "outside_workspace"
    return path, ""


def _mutate(ws: str, fn) -> str:
    """Run fn(doc) -> (status, changed) under the ledger lock; write when changed."""
    path, rule = _target(ws)
    if path is None:
        return f"rejected:{rule}"
    try:
        with file_lock(f"gaps-{ws}", timeout=LOCK_TIMEOUT):
            existing = _read(path)
            doc = _load(existing)
            status, changed = fn(doc)
            if changed:
                safe_write(path, doc.render(ws))
    except TimeoutError:
        return "rejected:lock_timeout"
    if changed:
        try:
            from _index import reindex_paths  # type: ignore
            reindex_paths([path])
        except Exception:
            pass
    return status


# ── operations ─────────────────────────────────────────────────────────────

def add(ws: str, kind: str, question: str, why: str = "", source: str = "session",
        force: bool = False, *, today: "str | None" = None,
        settings: "dict | None" = None) -> tuple:
    """Open a question. Returns (id, status)."""
    s = read_settings() if settings is None else settings
    text, why_c, src = _clean(question, question=True), _clean(why), _clean(source)
    qid = qid_for(text)
    if not valid_ws(ws):
        return qid, "rejected:bad_ws"
    if kind not in KINDS:
        return qid, "rejected:bad_kind"
    if src and src not in SOURCES:
        return qid, "rejected:bad_from"
    rule = _field_rule({"question": text, "why": why_c}, s)
    if rule:
        if rule == "secret_leak":
            from _privacy import sanitize  # type: ignore
            qid = qid_for(sanitize(text)[0])
        return qid, f"rejected:{rule}"
    if len(text) < QUESTION_MIN:
        return qid, "rejected:too_short"
    if len(text) > QUESTION_MAX or len(why_c) > FIELD_MAX:
        return qid, "rejected:too_long"
    day = today or _today()
    norm = _norm(text)
    result = {"id": qid}

    def apply(doc: _Doc):
        for q in doc.questions("open"):
            if _norm(q["question"]) == norm:
                result["id"] = q["id"]
                return "duplicate", False
        if not force:
            for q in doc.questions("closed"):
                if _norm(q["question"]) == norm:
                    result["id"] = q["id"]
                    return "duplicate", False
            other = _similar(doc, text)
            if other:
                return f"similar:{other}", False
        taken = {q["id"] for q in doc.questions("open") + doc.questions("closed")}
        new_id, salt = qid, 1
        while new_id in taken and salt < 10_000:
            salt += 1
            new_id = qid_for(text, salt)
        q = _blank()
        q.update(id=new_id, kind=kind, opened=day, question=text, why=why_c, source=src)
        doc.open.append(("q", q))
        result["id"] = new_id
        return "added", True

    status = _mutate(ws, apply)
    return result["id"], status


def _close_with(ws: str, qid: str, fields: dict, rule_fields: dict, *, status_ok: str,
                need_strong: bool, today: "str | None", settings: "dict | None") -> tuple:
    s = read_settings() if settings is None else settings
    if not valid_ws(ws):
        return qid, "rejected:bad_ws"
    rule = _field_rule(rule_fields, s)
    if rule:
        return qid, f"rejected:{rule}"
    day = today or _today()

    def apply(doc: _Doc):
        i = doc.find("open", qid)
        if i is None:
            return ("rejected:already_closed" if doc.find("closed", qid) is not None
                    else "not_found"), False
        q = doc.open[i][1]
        if need_strong and q["kind"] == "research" and not strong_evidence(fields["evidence"]):
            return "rejected:weak_evidence", False
        del doc.open[i]
        q.update(closed=day, why="", source="", tries=0, last_tried="", **fields)
        doc.closed.append(("q", q))
        return status_ok, True

    return qid, _mutate(ws, apply)


def answer(ws: str, qid: str, answer_text: str, evidence: str, *,
           today: "str | None" = None, settings: "dict | None" = None) -> tuple:
    """Close an open question with its answer. Returns (id, status)."""
    qid = _norm_qid(qid)
    ans, ev = _clean(answer_text), _clean(evidence)
    if not ev:
        return qid, "rejected:no_evidence"
    if not ans:
        return qid, "rejected:too_short"
    if len(ans) > ANSWER_MAX or len(ev) > FIELD_MAX:
        return qid, "rejected:too_long"
    return _close_with(ws, qid, {"answer": ans, "evidence": ev, "reason": ANSWERED},
                       {"answer": ans, "evidence": ev}, status_ok="answered",
                       need_strong=True, today=today, settings=settings)


def close(ws: str, qid: str, evidence: str, reason: str, *,
          today: "str | None" = None, settings: "dict | None" = None) -> tuple:
    """Close an open question without an answer. Returns (id, status)."""
    qid = _norm_qid(qid)
    ev = _clean(evidence)
    if reason not in CLOSE_REASONS:
        return qid, "rejected:bad_reason"
    if not ev:
        return qid, "rejected:no_evidence"
    if len(ev) > FIELD_MAX:
        return qid, "rejected:too_long"
    return _close_with(ws, qid, {"answer": "", "evidence": ev, "reason": reason},
                       {"evidence": ev}, status_ok="closed", need_strong=False,
                       today=today, settings=settings)


def tried(ws: str, qid: str, note: str, *, today: "str | None" = None,
          settings: "dict | None" = None) -> tuple:
    """Record one unsuccessful attempt (` · tried <date>×<n>`). Returns (id, status).
    The note is validated but not stored — it belongs in the caller's worklog."""
    qid = _norm_qid(qid)
    s = read_settings() if settings is None else settings
    text = _clean(note)
    if not valid_ws(ws):
        return qid, "rejected:bad_ws"
    if not text:
        return qid, "rejected:no_note"
    if len(text) > FIELD_MAX:
        return qid, "rejected:too_long"
    rule = _field_rule({"note": text}, s)
    if rule:
        return qid, f"rejected:{rule}"
    day = today or _today()

    def apply(doc: _Doc):
        i = doc.find("open", qid)
        if i is None:
            return ("rejected:already_closed" if doc.find("closed", qid) is not None
                    else "not_found"), False
        q = doc.open[i][1]
        q["tries"] = int(q.get("tries") or 0) + 1
        q["last_tried"] = day
        return f"tried:{q['tries']}", True

    return qid, _mutate(ws, apply)


# ── CLI ────────────────────────────────────────────────────────────────────

def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="_gaps.py",
                                 description="Gap ledger: <ws>/docs/questions.md (open questions).")
    ap.add_argument("--ws", help="workspace (default: active)")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("add", help="open a question")
    p.add_argument("--ws", dest="ws_sub")
    p.add_argument("--kind", default="", help="research | ask")
    p.add_argument("question", nargs="+",
                   help='"<question>" (`add research "<q>"` = `add --kind research "<q>"`)')
    p.add_argument("--why", default="")
    p.add_argument("--from", dest="source", default="session", help="curator | session | user")
    p.add_argument("--force", action="store_true", help="skip the similarity check; re-open a closed question")

    p = sub.add_parser("list", help="list open (or --closed) questions")
    p.add_argument("--ws", dest="ws_sub")
    p.add_argument("--kind", default="")
    p.add_argument("--closed", action="store_true")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("answer", help="close a question with its answer")
    p.add_argument("--ws", dest="ws_sub")
    p.add_argument("id")
    p.add_argument("answer", nargs="+")
    p.add_argument("--evidence", default="")

    p = sub.add_parser("close", help="close a question without an answer")
    p.add_argument("--ws", dest="ws_sub")
    p.add_argument("id")
    p.add_argument("--evidence", default="")
    p.add_argument("--reason", default="")

    p = sub.add_parser("tried", help="record an unsuccessful attempt")
    p.add_argument("--ws", dest="ws_sub")
    p.add_argument("id")
    p.add_argument("--note", default="")

    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 0
    ws = getattr(args, "ws_sub", None) or args.ws or active_workspace()

    if args.cmd == "list":
        items = list_items(ws, closed=args.closed, kind=args.kind) if valid_ws(ws) else []
        if args.json:
            print(json.dumps(items, ensure_ascii=False, indent=2))
        elif not items:
            print(f"no {'closed' if args.closed else 'open'} questions in {ws}")
        else:
            for q in items:
                print(render_line(q)[2:])
        return 0
    if args.cmd == "add":
        kind, words = args.kind, list(args.question)
        if not kind and len(words) > 1 and words[0] in KINDS:
            kind, words = words[0], words[1:]          # the /mem-ops gap add <kind> "<q>" form
        qid, status = add(ws, kind, " ".join(words), args.why, args.source, args.force)
    elif args.cmd == "answer":
        qid, status = answer(ws, args.id, " ".join(args.answer), args.evidence)
    elif args.cmd == "close":
        qid, status = close(ws, args.id, args.evidence, args.reason)
    else:
        qid, status = tried(ws, args.id, args.note)
    print(f"{qid}\t{status}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
