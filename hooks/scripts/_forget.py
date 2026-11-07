#!/usr/bin/env python3
"""Active forgetting (v3.6): enforce the journal raw-memory TTL.

Journals are the HIPPOCAMPAL BUFFER — ephemeral working memory, not durable
knowledge. The data-quality canon (`shared/research/data-quality-2026.md` §3)
sets `journal raw: 7 days raw, then distill; hard cutoff`. Nothing enforced
that cutoff, so raw `[auto-precompact-dump]` transcript accumulated unbounded
(observed: a single journal grew to 1.8 MB / 26,812 lines, never read by the
agent). This module is the missing "active forgetting" / synaptic-pruning step
from CLS theory: fast-write buffer → consolidate the signal → forget the raw.

What it does, per workspace journal/ dir:
  1. SALVAGE — deterministically lift any genuinely-curated entries (v4.9: all
     three on-disk shapes, `entry_blocks()` — `- [type]` bullets, `## [type]`
     blocks, bare `[type]` lines) that pass minimal gates into
     `<ws>/journal/_salvage.md` (SHA1-deduped, with a provenance line), so
     structured signal is never lost to archiving. Raw transcript prose (no
     `[type]` entry head) is NOT salvaged — that's the noise we are forgetting.
  2. ARCHIVE — gzip journal files older than `raw_ttl_days` (or over the hard
     byte cap) into `~/.gowth-mem/.archive/journal/<ws>/`, then remove the
     original. Recoverable two ways: the gz archive AND the memory-repo git
     history (journals are committed before removal).

Safety:
  - Never touches today's / within-TTL journals (the live buffer).
  - Atomic writes, per-workspace file lock, audit log line.
  - `--dry-run` prints the plan and changes nothing.
  - Missing ~/.gowth-mem/ → exit 0, no traceback (graceful-missing rule).

CLI:
  python3 _forget.py [--all-workspaces] [--dry-run] [--ttl-days N]
                     [--max-bytes N] [--no-salvage] [--quiet]
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import safe_write  # type: ignore
from _debug import log_debug  # type: ignore
from _home import (gowth_home, is_dated_aspect_filename, is_topic_folder,  # type: ignore
                   journal_dir, list_workspaces, read_settings, workspace_dir)
from _lock import file_lock  # type: ignore

DEFAULT_TTL_DAYS = 7          # canon §3: raw journal lives 7 days, then forget
DEFAULT_MAX_BYTES = 75_000    # canon §2: hard distill ceiling ~50-75 KB / file
DEFAULT_ASPECT_THRESHOLD_DAYS = 90   # v4.1 retention: aspects >3 months → archive
DEFAULT_ASPECT_KEEP_NEWEST = 3       # a topic ALWAYS keeps its newest N aspects
SALVAGE_FILE = "_salvage.md"

# ── curated-entry parser (v4.9 spec §8) ─────────────────────────────────────
# The 9 schema types (_index.KNOWN_TAGS = _card.ENTRY_TYPES = the _gate rules;
# no aliases exist). An entry head is one of three on-disk shapes; raw
# transcript prose (`### [assistant]`, `## [auto-precompact-dump]`, `[lesson]`)
# has none, so it is never salvaged.
ENTRY_TYPES = ("decision", "exp", "ref", "tool", "reflection", "skill-ref",
               "secret-ref", "goal", "hypothesis")
_TYPES = "|".join(ENTRY_TYPES)
# Matched with re.match on ONE line: each quantifier is a single-class run
# followed by a literal, so a line costs O(len) whatever it holds. A list item
# may be ordered and its type token wrapped in `…`/**…** (live: a gated batch
# of `1. \`[reflection]\` …` items in a review aspect).
_BULLET_HEAD_RE = re.compile(r"[ \t]*(?:[-*+]|\d{1,9}[.)])[ \t]+(?:\*\*|`)?\[(?:%s)\](?:\*\*|`)?"
                             % _TYPES, re.IGNORECASE)
_HEADING_HEAD_RE = re.compile(r"(#{1,6})[ \t]*\[(?:%s)\]" % _TYPES, re.IGNORECASE)
_BARE_HEAD_RE = re.compile(r"[ \t]*\[(?:%s)\]" % _TYPES, re.IGNORECASE)
_HEADING_RE = re.compile(r"(#{1,6})(?:[ \t]|$)")            # an ATX heading line
_FENCE_RE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)")          # a fence opener/closer
# The line every salvaged block is written with. It is NOT part of the block
# above it and ends that block, so a re-run parses the salvage file back to
# the very blocks it lifted (seen-set hashes match → nothing is added twice).
PROVENANCE_PREFIX = "(salvaged from archived "
PROVENANCE_INDENT = "  "
SALVAGE_SECTION = "## Salvaged from archived aspects"
_SALVAGE_SECTION_H1 = "# Salvaged from archived aspects"   # after an open `# [type]` block
_SALVAGE_HEADINGS = (SALVAGE_SECTION, _SALVAGE_SECTION_H1, "## salvaged ")  # + journal/_salvage.md
DUMP_HEADER_RE = re.compile(r"^##\s+\[auto-precompact-dump\]", re.IGNORECASE)
MIN_ENTRY_CHARS = 20          # canon §1: body < 20 chars → drop

# v4.0: session logs (journal/sessions/*.md) hold `## [self-review]` blocks —
# lift them before archiving so the honest-review scores survive forgetting.
REVIEW_BLOCK_RE = re.compile(r"^##\s+\[self-review\]", re.IGNORECASE)


def _norm_hash(s: str) -> str:
    return hashlib.sha1(re.sub(r"\s+", " ", s).strip().lower().encode()).hexdigest()


def _settings_forget() -> dict:
    try:
        s = read_settings()
        return s.get("journal", {}) if isinstance(s, dict) else {}
    except Exception:
        return {}


def _is_provenance(line: str) -> bool:
    s = line.strip()
    return s.startswith(PROVENANCE_PREFIX) and s.endswith(")")


def _provenance(kind: str, name: str) -> str:
    """`  (salvaged from archived <kind> <file name>)` — one line, always."""
    return f"{PROVENANCE_INDENT}{PROVENANCE_PREFIX}{kind} {' '.join(name.split())})"


def _is_barrier(line: str) -> bool:
    """A fence never spans these lines: what `_forget` writes between salvaged
    blocks (provenance lines, salvage section headings). A fence opener inside
    one salvaged block therefore pairs exactly as it did in its source file."""
    return _is_provenance(line) or line.startswith(_SALVAGE_HEADINGS)


def _frontmatter_end(lines: list) -> int:
    """Index of the first line after a leading `---` … `---` YAML block, else 0
    (`_index.FRONTMATTER_RE`'s shape; an unclosed opener is not frontmatter,
    and none spans a salvage barrier — a stray `---` atop a lessons.md must
    not pair with a `---` inside an entry salvaged below it)."""
    if not lines or lines[0] != "---":
        return 0
    for i in range(1, len(lines)):
        if lines[i].rstrip(" \t") == "---":
            return i + 1
        if _is_barrier(lines[i]):
            return 0
    return 0


def _fence_ids(lines: list, start: int) -> list:
    """fid[i] > 0 when lines[i] is part of a CLOSED fenced code block (opener and
    closer included; one id per fence), else 0. CommonMark pairing (≤3-space
    indent, ≥3 backticks or tildes, a closer of the same char at least as long
    with nothing after it) — except that an opener with no closer is plain text,
    not a fence to the end of the file: a stray ``` must never hide the entries
    after it from salvage. Linear: closers are found through a suffix maximum,
    and a scan only ever runs to the closer it then jumps past."""
    n = len(lines)
    fid = [0] * n
    seg = start
    next_id = 1
    while seg < n:
        end = seg
        while end < n and not _is_barrier(lines[end]):
            end += 1
        marks = []                                   # (index, char, length, can_close)
        for i in range(seg, end):
            m = _FENCE_RE.match(lines[i])
            if m:
                run, rest = m.group(1), m.group(2)
                if run[0] == "`" and "`" in rest:
                    continue                         # not a fence (backtick info string)
                marks.append((i, run[0], len(run), not rest.strip()))
        if marks:
            after = [None] * len(marks)              # longest closer per char AFTER marks[k]
            best = {"`": 0, "~": 0}
            for k in range(len(marks) - 1, -1, -1):
                after[k] = (best["`"], best["~"])
                _, ch, ln, can_close = marks[k]
                if can_close and ln > best[ch]:
                    best[ch] = ln
            k = 0
            while k < len(marks):
                i, ch, ln, _ = marks[k]
                if after[k][0 if ch == "`" else 1] < ln:
                    k += 1                           # never closed: plain text
                    continue
                k2 = k + 1
                while not (marks[k2][1] == ch and marks[k2][2] >= ln and marks[k2][3]):
                    k2 += 1
                for t in range(i, marks[k2][0] + 1):
                    fid[t] = next_id
                next_id += 1
                k = k2 + 1
        seg = end + 1
    return fid


def _head(line: str) -> tuple:
    """(shape, heading level) of an entry head line, or (None, 0)."""
    if "[" not in line:
        return None, 0
    m = _HEADING_HEAD_RE.match(line)
    if m:
        return "block", len(m.group(1))
    if _BULLET_HEAD_RE.match(line):
        return "bullet", 0
    if _BARE_HEAD_RE.match(line):
        return "bare", 0
    return None, 0


def _bullet_end(lines: list, i: int, fid: list) -> int:
    """End (exclusive) of the bullet entry at `i`: its indented continuation —
    blank lines inside it included when indented text follows (a list item's
    later paragraphs), plus a fenced code block that starts on an indented line
    or right below the entry (a `[tool]` entry's command). Never a provenance
    line; trailing blank lines are not part of it."""
    n = len(lines)
    end = j = i + 1
    while j < n:
        ln = lines[j]
        f = fid[j]
        if f and (j == 0 or fid[j - 1] != f):         # a fence opens here
            if j == end or ln[:1] in (" ", "\t"):
                while j < n and fid[j] == f:
                    j += 1
                end = j
                continue
            break
        if not ln.strip():
            j += 1
            continue
        if ln[:1] in (" ", "\t") and not _is_provenance(ln):
            j += 1
            end = j
            continue
        break
    return end


def _region_end(lines: list, i: int, fid: list, stop_level: int, stop_at_entry: bool) -> int:
    """End (exclusive) of a heading entry block (`stop_level` = its own level:
    for `## [type]` it ends only at the next `#`/`##` heading, H3+ stay inside)
    or of a bare `[type]` entry (`stop_level` 6 and `stop_at_entry`: it also
    ends at the next entry head). Fenced lines never end it; a provenance line
    always does."""
    n = len(lines)
    end = j = i + 1
    while j < n:
        ln = lines[j]
        if not fid[j]:
            if _is_provenance(ln):
                break
            m = _HEADING_RE.match(ln)
            if m and len(m.group(1)) <= stop_level:
                break
            if stop_at_entry and _head(ln)[0] is not None:
                break
        if ln.strip():
            end = j + 1
        j += 1
    return end


def entry_spans(text: str) -> list:
    """The curated entries of a markdown file as `(shape, first line, end line,
    block text)`, line numbers indexing `text.splitlines()`. PURE.

    Shapes (spec §8):
      - "bullet": `- [type] …` (also `*`/`+`/`1.`, any indent, the type token
        optionally in backticks or bold) + its indented continuation
        (`_bullet_end`);
      - "block":  `## [type] …` through the line before the next heading of the
        same or a higher rank — for `##` the next `#` or `##` heading (H3+ stay
        inside); any ATX level works alike: a live `# [exp] …` entry carries its
        later updates as `##` sections, which stay inside it;
      - "bare":   `[type] …` + every following line up to the next entry head,
        heading or provenance line — the live bare entries carry their
        `Source:`/`Status:`/`Verify:` lines and later paragraphs (UPDATE,
        DISPROVEN, Resolved …) below the head line, often after a blank line.
    A heading or entry head inside a closed fenced code block is content, the
    YAML frontmatter is skipped, a trailing `(salvaged from archived …)` line
    is never part of a block (`_is_provenance`), and blocks are right-stripped.
    """
    lines = text.splitlines()
    start = _frontmatter_end(lines)
    fid = _fence_ids(lines, start)
    out = []
    i = start
    n = len(lines)
    while i < n:
        if fid[i]:
            i += 1
            continue
        shape, level = _head(lines[i])
        if shape is None:
            i += 1
            continue
        if shape == "bullet":
            j = _bullet_end(lines, i, fid)
        elif shape == "block":
            j = _region_end(lines, i, fid, level, False)
        else:
            j = _region_end(lines, i, fid, 6, True)
        out.append((shape, i, j, "\n".join(lines[i:j]).rstrip()))
        i = j
    return out


def entry_blocks_with_shape(text: str) -> list:
    """`[(shape, block text)]` — see `entry_spans`."""
    return [(shape, block) for shape, _a, _b, block in entry_spans(text)]


def entry_blocks(text: str) -> list:
    """The curated entry blocks of a markdown file, in file order — the ONE
    parser behind aspect salvage, journal salvage and their seen-sets."""
    return [block for _shape, _a, _b, block in entry_spans(text)]


def _body_len(block: str) -> int:
    """Length of an entry's text after its head marker (`- [type]`, `## [type]`,
    `[type]`), for the canon §1 minimum."""
    first, _, rest = block.partition("\n")
    m = _HEADING_HEAD_RE.match(first) or _BULLET_HEAD_RE.match(first) or _BARE_HEAD_RE.match(first)
    body = first[m.end():] if m else first
    return len((body + ("\n" + rest if rest else "")).strip())


def _clean(text: str) -> str:
    """`_privacy.sanitize(text)` — what `safe_write` stores. Salvage hashes and
    writes the CLEAN form, so the seen-set read back from a sanitized file
    matches the next run's candidates. Fails open (raw text), like safe_write."""
    try:
        from _privacy import sanitize  # type: ignore
        out, n = sanitize(text)
    except Exception:
        return text
    return out if isinstance(out, str) and n >= 0 else text


def _salvage_entries(journal_files: list[Path], jd: Path, dry_run: bool) -> int:
    """Lift curated entries (`entry_blocks`, all three shapes) from the given
    journals into journal/_salvage.md, each followed by a provenance line.

    Dedupes by SHA1 of the normalized (privacy-sanitized) entry across the
    existing salvage file AND within this run. Returns count of new entries
    salvaged. An existing salvage file that cannot be read raises (the caller
    then archives nothing) — it was replaced by the new entries alone before.
    """
    salvage_path = jd / SALVAGE_FILE
    existing = ""
    if salvage_path.is_file():
        existing = salvage_path.read_text(encoding="utf-8", errors="replace")
    seen = {_norm_hash(blk) for blk in entry_blocks(_clean(existing))}

    new_blocks: list[str] = []
    for f in journal_files:
        try:
            txt = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for blk in entry_blocks(_clean(txt)):
            if _body_len(blk) < MIN_ENTRY_CHARS:
                continue
            h = _norm_hash(blk)
            if h in seen:
                continue
            seen.add(h)
            new_blocks.append(blk.strip() + "\n" + _provenance("journal", f.name))

    if not new_blocks or dry_run:
        return len(new_blocks)

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = (
        "# _salvage.md — curated entries lifted from archived journals\n\n"
        "_Auto-generated by `_forget.py`. Review and route into topic/docs files "
        "via `/mem-distill`, then delete the routed lines. Deduped by SHA1._\n"
    ) if not existing.strip() else ""
    addition = f"\n\n## salvaged {stamp}\n\n" + "\n".join(new_blocks) + "\n"
    safe_write(salvage_path, (existing + addition) if existing else (header + addition))
    return len(new_blocks)


def _extract_review_blocks(text: str) -> list[str]:
    """Return `## [self-review] …` blocks (heading through the next `## ` heading)."""
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        if REVIEW_BLOCK_RE.match(lines[i]):
            block = [lines[i]]
            j = i + 1
            while j < n and not lines[j].startswith("## "):
                block.append(lines[j])
                j += 1
            out.append("\n".join(block).rstrip())
            i = j
        else:
            i += 1
    return out


def _salvage_reviews(session_files: list[Path], jd: Path, dry_run: bool) -> int:
    """Lift `## [self-review]` blocks from session logs into journal/_salvage.md.

    SHA1-deduped against existing review blocks in the salvage file and within
    this run (sanitized form, as `safe_write` stores it). Returns count of new
    blocks salvaged. An unreadable existing salvage file raises (see
    `_salvage_entries`).
    """
    if not session_files:
        return 0
    salvage_path = jd / SALVAGE_FILE
    existing = ""
    if salvage_path.is_file():
        existing = salvage_path.read_text(encoding="utf-8", errors="replace")
    seen = {_norm_hash(blk) for blk in _extract_review_blocks(_clean(existing))}

    new_blocks: list[str] = []
    for f in session_files:
        try:
            txt = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for blk in _extract_review_blocks(_clean(txt)):
            body = blk.strip()
            if len(body) < MIN_ENTRY_CHARS:
                continue
            h = _norm_hash(body)
            if h in seen:
                continue
            seen.add(h)
            new_blocks.append(body)

    if not new_blocks or dry_run:
        return len(new_blocks)

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = (
        "# _salvage.md — curated entries lifted from archived journals\n\n"
        "_Auto-generated by `_forget.py`. Review and route into topic/docs files "
        "via `/mem-distill`, then delete the routed lines. Deduped by SHA1._\n"
    ) if not existing.strip() else ""
    addition = f"\n\n## salvaged reviews {stamp}\n\n" + "\n\n".join(new_blocks) + "\n"
    safe_write(salvage_path, (existing + addition) if existing else (header + addition))
    return len(new_blocks)


def _archive_one(f: Path, ws: str, gh: Path, dry_run: bool, subdir: str = "",
                 root: str = "journal") -> bool:
    """Gzip `f` into .archive/<root>/<ws>/[<subdir>/] then remove the original."""
    try:
        mtime = int(f.stat().st_mtime)
    except OSError:
        return False
    arc_dir = gh / ".archive" / root / ws
    if subdir:
        arc_dir = arc_dir / subdir
    arc_path = arc_dir / f"{f.stem}-{mtime}.md.gz"
    if dry_run:
        return True
    try:
        arc_dir.mkdir(parents=True, exist_ok=True)
        raw = f.read_bytes()
        with gzip.open(arc_path, "wb") as gz:
            gz.write(raw)
        # Verify the archive is readable before deleting the source.
        with gzip.open(arc_path, "rb") as gz:
            if gz.read(64) != raw[:64]:
                log_debug("forget", f"archive verify mismatch for {f}; keeping original")
                return False
        f.unlink()
        return True
    except Exception as e:
        log_debug("forget", f"archive failed for {f}: {e}")
        return False


def _aspect_date(name: str):
    """datetime.date from a dated-aspect filename, or None."""
    try:
        return datetime.strptime(name[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _compose_lessons(existing: str, adds: list) -> str:
    """lessons.md with `adds` appended in the v4.1 section format: under a
    `## Salvaged from archived aspects` heading, each block + its provenance
    line. Appended straight below the file's last line only when that line is
    a provenance line (our own list continues); otherwise a fresh heading goes
    first, which ends whatever block the file ends in so nothing appended is
    read back as part of it — `#` when that block is a `# [type]` entry (only
    an H1 ends it), `##` otherwise."""
    tail = existing.rstrip() or "# Lessons"
    body = "\n".join(adds) + "\n"
    if _is_provenance(tail[tail.rfind("\n") + 1:]):
        return tail + "\n" + body
    heading = SALVAGE_SECTION
    spans = entry_spans(tail)
    if spans:
        shape, _first, end, block = spans[-1]
        m = _HEADING_HEAD_RE.match(block) if shape == "block" else None
        if m and len(m.group(1)) == 1 and end == len(tail.splitlines()):
            heading = _SALVAGE_SECTION_H1
    return tail + "\n" + heading + "\n\n" + body


def _salvage_to_lessons(topic: Path, aspect_files: list[Path], dry_run: bool,
                        reader=None) -> dict:
    """Lift every curated entry of the given aspects (`entry_blocks`: bullets,
    `## [type]` blocks, bare `[type]` lines) into the topic's lessons.md,
    verbatim (privacy-sanitized, as `safe_write` stores it) + a provenance
    line, SHA1-deduped against what lessons.md already holds and within the
    run. On a real run the caller holds `entries-<ws>` from this read to the
    write.

    Returns {"salvaged": n, "shapes": {shape: n}, "landed": [aspect paths],
    "unlanded": n}. Only `landed` aspects may be archived: each was read, and
    every one of its entries is in the lessons.md as written (or as it already
    was) — checked by parsing that text back. When the appended text would not
    parse back to the same entries, the run drops the aspects concerned and
    tries once more without them; if that is not clean either, or the append
    would change how the existing entries read (e.g. a `<private>` pair the
    sanitizer would cut across the seam), nothing is written and only aspects
    whose every entry is already in lessons.md land. Raises OSError when
    lessons.md exists but cannot be read (the caller archives nothing).
    """
    lessons = topic / "lessons.md"
    existing = lessons.read_text(encoding="utf-8", errors="ignore") if lessons.is_file() else ""
    have = {_norm_hash(b) for b in entry_blocks(_clean(existing))}

    files = []                       # (aspect, [(shape, block, key)]) — readable aspects only
    for f in aspect_files:
        try:
            text = reader(f) if reader is not None else f.read_text(encoding="utf-8", errors="ignore")
        except (OSError, EOFError, ValueError) as e:
            log_debug("forget", f"aspect unreadable, not archived: {f}: {e}")
            continue
        blocks = [(shape, b, _norm_hash(b)) for shape, b in entry_blocks_with_shape(_clean(text))
                  if len(b.strip()) >= MIN_ENTRY_CHARS]
        files.append((f, blocks))

    def attempt(group: list):
        seen = set(have)
        adds = []                    # (shape, text)
        for f, blocks in group:
            for shape, block, key in blocks:
                if key not in seen:
                    seen.add(key)
                    adds.append((shape, block + "\n" + _provenance("aspect", f.name)))
        final, got = None, have
        if adds:
            final = _clean(_compose_lessons(existing, [t for _, t in adds]))
            got = {_norm_hash(b) for b in entry_blocks(final)}
            if not have <= got:
                return None          # the append changed how existing entries read
        landed = [f for f, blocks in group if all(key in got for _s, _b, key in blocks)]
        return final, adds, landed

    result = attempt(files)
    if result is not None and len(result[2]) < len(files):
        ok = set(result[2])
        clean_group = [(f, b) for f, b in files if f in ok]
        result = attempt(clean_group)
        if result is not None and len(result[2]) < len(clean_group):
            result = None
    if result is None:
        # Write nothing; only aspects whose every entry is already there land.
        log_debug("forget", f"salvage into {lessons} would not read back intact; nothing "
                            f"written, only already-salvaged aspects archived in {topic.name}")
        result = (None, [], [f for f, blocks in files if all(key in have for _s, _b, key in blocks)])
    final, adds, landed = result
    if final is not None and not dry_run:
        safe_write(lessons, final)
    shapes: dict = {}
    for shape, _t in adds:
        shapes[shape] = shapes.get(shape, 0) + 1
    return {"salvaged": len(adds), "shapes": shapes, "landed": landed,
            "unlanded": len(aspect_files) - len(landed)}


def _salvage_and_archive(ws: str, folder: Path, candidates: list[Path], dry_run: bool,
                         gh: Path, lock_timeout: float | None = None) -> dict | None:
    """One topic's salvage + archive, under `entries-<ws>` on a real run (the
    caller holds `topic-forget-<ws>`: the order is always forget → entries).
    Archives only the aspects whose salvage landed. None = skipped this run
    (lessons.md unreadable or not written), {"busy": True} = the entries lock
    could not be taken in `lock_timeout` (default `_topic.ENTRIES_LOCK_TIMEOUT`):
    nothing archived either way. A dry run takes no entries lock, writes nothing."""
    if dry_run:
        try:
            r = _salvage_to_lessons(folder, candidates, True)
        except Exception as e:
            log_debug("forget", f"salvage plan failed for {ws}/{folder.name}: {e}")
            return None
        r["archived"] = sum(1 for f in r["landed"]
                            if _archive_one(f, ws, gh, True, subdir=folder.name, root="topics"))
        return r
    try:
        from _topic import EntriesLockTimeout, entries_lock  # type: ignore
    except Exception as e:
        log_debug("forget", f"entries lock unavailable, {ws}/{folder.name} skipped: {e}")
        return None
    try:
        with entries_lock(ws, lock_timeout):
            r = _salvage_to_lessons(folder, candidates, False)
            r["archived"] = sum(1 for f in r["landed"]
                                if _archive_one(f, ws, gh, False, subdir=folder.name, root="topics"))
            return r
    except EntriesLockTimeout as e:
        log_debug("forget", f"entries lock busy, {ws}/{folder.name} skipped this run: {e}")
        return {"busy": True}
    except Exception as e:
        log_debug("forget", f"salvage failed, nothing archived in {ws}/{folder.name}: {e}")
    return None


def forget_aspects(ws: str, threshold_days: int | None, keep_newest: int,
                   dry_run: bool, gh: Path, today: str | None = None) -> dict:
    """v4.1 retention: archive topic aspects older than the threshold.

    Age = FILENAME date (the knowledge date) — mtime is perturbed by
    maintenance (validate --fix, retag) and would mis-age files. Every topic
    keeps its newest `keep_newest` aspects regardless of age; 00-README.md and
    lessons.md are never candidates. Curated entries — v4.9: all three shapes
    (`entry_blocks`) — are salvaged into lessons.md BEFORE the gzip archive
    (required data survives in the consolidated layer; raw episodic detail is
    forgotten), and an aspect whose salvage did not land is never archived
    (`_salvage_and_archive`). `shapes` counts the salvaged entries per shape;
    `skipped` the topics left for the next run.
    """
    if threshold_days is None:
        try:
            threshold_days = int(read_settings().get("topic_layout", {})
                                 .get("archive_threshold_days", DEFAULT_ASPECT_THRESHOLD_DAYS))
        except Exception:
            threshold_days = DEFAULT_ASPECT_THRESHOLD_DAYS
    today_d = (datetime.strptime(today, "%Y-%m-%d").date() if today
               else datetime.now().date())

    shapes = {"bullet": 0, "block": 0, "bare": 0}
    ws_root = workspace_dir(ws)
    if not ws_root.is_dir():
        return {"ws": ws, "archived": 0, "salvaged": 0, "topics": 0, "candidates": 0,
                "skipped": 0, "shapes": shapes, "threshold_days": threshold_days}

    archived = salvaged = topics_touched = n_candidates = skipped = 0
    wait = None          # lock waits: the default until a holder proves busy, then try once
    for folder in sorted(p for p in ws_root.iterdir() if p.is_dir()):
        if not is_topic_folder(folder):
            continue
        dated = [f for f in folder.glob("*.md")
                 if is_dated_aspect_filename(f.name) and _aspect_date(f.name)]
        dated.sort(key=lambda f: f.name)          # filename date order
        protected = set(dated[-keep_newest:]) if keep_newest > 0 else set()
        candidates = [f for f in dated
                      if f not in protected
                      and (today_d - _aspect_date(f.name)).days > threshold_days]
        if not candidates:
            continue
        topics_touched += 1
        n_candidates += len(candidates)
        try:
            with file_lock(f"topic-forget-{ws}", timeout=5.0 if wait is None else wait):
                r = _salvage_and_archive(ws, folder, candidates, dry_run, gh, wait)
        except TimeoutError as e:
            log_debug("forget", f"aspect lock timeout for {ws}/{folder.name}: {e}")
            r = {"busy": True}
        if r is None or r.get("busy"):
            skipped += 1
            if r is not None:
                # the holder is busy: the rest of this workspace tries once
                # (a 5 s wait per topic would outlast the Stop hook's budget)
                wait = 0.0
            continue
        archived += r["archived"]
        salvaged += r["salvaged"]
        for shape, k in r["shapes"].items():
            shapes[shape] = shapes.get(shape, 0) + k
        if r["unlanded"]:
            skipped += 1
    return {"ws": ws, "archived": archived, "salvaged": salvaged,
            "topics": topics_touched, "candidates": n_candidates, "skipped": skipped,
            "shapes": shapes, "threshold_days": threshold_days}


ARCHIVE_GZ_MAX_BYTES = 20_000_000    # decompressed cap per archived aspect (a vault aspect is KBs)


def _read_gz(f: Path) -> str:
    """Text of one archived aspect (`.archive/topics/<ws>/<topic>/<name>.md.gz`)."""
    if f.is_symlink() or not f.is_file():
        raise OSError(f"not a regular file: {f}")
    with gzip.open(f, "rb") as gz:
        raw = gz.read(ARCHIVE_GZ_MAX_BYTES + 1)
    if len(raw) > ARCHIVE_GZ_MAX_BYTES:
        raise ValueError(f"archived aspect over {ARCHIVE_GZ_MAX_BYTES} bytes: {f}")
    return raw.decode("utf-8", errors="ignore")


def resalvage_archive(ws: str, dry_run: bool, gh: Path, lock_timeout: float | None = None) -> dict:
    """v4.9 recovery: lift the curated entries of ALREADY-archived aspects back
    into the synced layer. Before v4.9, aspect archival salvaged only `- [type]`
    bullets, so `## [type]` blocks and bare `[type]` lines of every archived
    aspect survived only in this machine's gitignored `.archive/topics/` (and
    the vault's git history). This reads each `.archive/topics/<ws>/<topic>/*.md.gz`
    and salvages every entry shape into the LIVE topic's lessons.md exactly as
    `forget_aspects` does now (verbatim, privacy-sanitized, SHA1-deduped against
    what lessons.md holds, one provenance line each), under `entries-<ws>`.

    It never deletes, moves or rewrites an archive file — the archive stays as
    it was. A topic whose live folder is gone, is a symlink, or resolves outside
    the workspace is reported in `missing` and left alone. Idempotent: a re-run
    finds every entry already present and writes nothing."""
    out = {"ws": ws, "topics": 0, "files": 0, "salvaged": 0, "unreadable": 0, "not_landed": 0,
           "missing": [], "busy": 0, "shapes": {"bullet": 0, "block": 0, "bare": 0}}
    arc_root = gh / ".archive" / "topics" / ws
    ws_root = workspace_dir(ws)
    if not arc_root.is_dir() or arc_root.is_symlink() or not ws_root.is_dir():
        return out
    try:
        ws_real = ws_root.resolve()
    except OSError:
        return out
    wait = None
    for arc in sorted(p for p in arc_root.iterdir() if p.is_dir() and not p.is_symlink()):
        gz = sorted(f for f in arc.glob("*.md.gz") if f.is_file() and not f.is_symlink())
        if not gz:
            continue
        live = ws_root / arc.name
        try:
            inside = live.resolve().is_relative_to(ws_real)
        except (OSError, ValueError):
            inside = False
        if live.is_symlink() or not live.is_dir() or not inside or not is_topic_folder(live):
            out["missing"].append(arc.name)
            continue
        out["topics"] += 1
        out["files"] += len(gz)
        try:
            if dry_run:
                r = _salvage_to_lessons(live, gz, True, reader=_read_gz)
            else:
                from _topic import EntriesLockTimeout, entries_lock  # type: ignore
                try:
                    with entries_lock(ws, wait if wait is not None else lock_timeout):
                        r = _salvage_to_lessons(live, gz, False, reader=_read_gz)
                except EntriesLockTimeout as e:
                    log_debug("forget", f"resalvage: entries lock busy, {ws}/{arc.name} skipped: {e}")
                    out["busy"] += 1
                    wait = 0.0
                    continue
        except Exception as e:
            log_debug("forget", f"resalvage failed for {ws}/{arc.name}: {e}")
            out["unreadable"] += 1
            continue
        out["salvaged"] += r["salvaged"]
        for shape, k in r["shapes"].items():
            out["shapes"][shape] = out["shapes"].get(shape, 0) + k
        out["not_landed"] += r["unlanded"]      # unreadable, or would not read back intact
    return out


def _curator_held(ws: str, ttl_days: int, now: float):
    """v4.9 curator hold (spec §4.3): while `curator.enabled`, a session log
    that still has unprocessed turns (`_curate.unprocessed`) is kept up to
    `curator.hold_days` past its TTL. Returns (stems held, hard cutoff
    timestamp) — a held log older than ttl + hold_days is archived anyway.
    Any failure → nothing held (archival as before)."""
    try:
        from _home import read_settings, setting  # type: ignore
        s = read_settings()                    # one snapshot for the knobs and the coverage
        if not setting("curator.enabled", bool, True, settings=s):
            return set(), now
        hold = setting("curator.hold_days", int, 30, settings=s)
        hold = max(0, min(365, hold if isinstance(hold, int) and not isinstance(hold, bool) else 30))
        if hold <= 0:
            return set(), now
        from _curate import unprocessed  # type: ignore
        return set(unprocessed(ws, s)), now - (ttl_days + hold) * 86400
    except Exception as e:
        log_debug("forget", f"curator hold skipped for {ws}: {e}")
        return set(), now


def forget_workspace(ws: str, ttl_days: int, max_bytes: int, salvage: bool,
                     dry_run: bool, gh: Path) -> dict:
    jd = journal_dir(ws)
    if not jd.is_dir():
        return {"ws": ws, "candidates": [], "archived": 0, "salvaged": 0, "bytes": 0}

    cutoff = time.time() - ttl_days * 86400
    now = time.time()
    today_name = datetime.now().strftime("%Y-%m-%d") + ".md"

    def _is_candidate(st) -> bool:
        too_old = st.st_mtime < cutoff
        # Size trigger only fires for files older than 1 day, so today's LIVE
        # journal (the active buffer, needed by bootstrap) is never archived
        # mid-session even if a big precompact dump landed in it.
        too_big = st.st_size > max_bytes and st.st_mtime < (now - 86400)
        return too_old or too_big

    freed = 0
    # Plain journals at journal/ root. Any `_`-prefixed file (e.g. _salvage.md,
    # _scores.md) is NEVER archived — it is curated, not raw buffer.
    plain_candidates: list[Path] = []
    for f in sorted(jd.glob("*.md")):
        if f.name.startswith("_") or f.name == today_name:
            continue
        try:
            st = f.stat()
        except OSError:
            continue
        if _is_candidate(st):
            plain_candidates.append(f)
            freed += st.st_size

    # v4.0: session logs at journal/sessions/. Same TTL/size rule; today's
    # session file is protected by its fresh mtime (never too_old, never >1d for
    # the size trigger). `_`-prefixed files exempt (same rule as above).
    session_candidates: list[Path] = []
    session_dir = jd / "sessions"
    if session_dir.is_dir():
        held, hold_cutoff = _curator_held(ws, ttl_days, now)   # v4.9 curator hold
        for f in sorted(session_dir.glob("*.md")):
            if f.name.startswith("_"):
                continue
            try:
                st = f.stat()
            except OSError:
                continue
            if f.stem in held and st.st_mtime >= hold_cutoff:
                continue
            if _is_candidate(st):
                session_candidates.append(f)
                freed += st.st_size

    candidates = plain_candidates + session_candidates
    if not candidates:
        return {"ws": ws, "candidates": [], "archived": 0, "salvaged": 0, "bytes": 0}

    salvaged = 0
    archived = 0
    try:
        with file_lock(f"journal-{ws}", timeout=5.0):
            if salvage:
                salvaged += _salvage_entries(plain_candidates, jd, dry_run)
                salvaged += _salvage_reviews(session_candidates, jd, dry_run)
            for f in plain_candidates:
                if _archive_one(f, ws, gh, dry_run):
                    archived += 1
            for f in session_candidates:
                if _archive_one(f, ws, gh, dry_run, subdir="sessions"):
                    archived += 1
    except TimeoutError as e:
        log_debug("forget", f"journal lock timeout for {ws}: {e}")
        return {"ws": ws, "candidates": candidates, "archived": 0, "salvaged": 0, "bytes": 0}

    return {"ws": ws, "candidates": candidates, "archived": archived,
            "salvaged": salvaged, "bytes": freed}


def main() -> int:
    ap = argparse.ArgumentParser(description="Enforce journal raw-memory TTL (active forgetting).")
    ap.add_argument("--all-workspaces", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ttl-days", type=int, default=None)
    ap.add_argument("--max-bytes", type=int, default=None)
    ap.add_argument("--no-salvage", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--aspects", action="store_true",
                    help="also archive topic aspects older than "
                         "topic_layout.archive_threshold_days (v4.1 retention)")
    ap.add_argument("--aspect-threshold-days", type=int, default=None)
    ap.add_argument("--keep-newest", type=int, default=DEFAULT_ASPECT_KEEP_NEWEST)
    ap.add_argument("--resalvage-archive", action="store_true",
                    help="v4.9 recovery: salvage every entry of ALREADY-archived aspects "
                         "(.archive/topics) into the live topics' lessons.md; deletes nothing; "
                         "use --dry-run first")
    args = ap.parse_args()

    gh = gowth_home()
    if not gh.is_dir():
        if not args.quiet:
            print("no ~/.gowth-mem directory; nothing to forget")
        return 0

    cfg = _settings_forget()
    ttl_days = args.ttl_days if args.ttl_days is not None else int(cfg.get("raw_ttl_days", DEFAULT_TTL_DAYS))
    max_bytes = args.max_bytes if args.max_bytes is not None else int(cfg.get("max_bytes", DEFAULT_MAX_BYTES))
    from _home import coerce_bool, setting  # type: ignore
    salvage = (not args.no_salvage) and coerce_bool(cfg.get("salvage"), True)

    from _home import active_workspace  # local import; avoids cycle at module load
    workspaces = list_workspaces() if args.all_workspaces else [active_workspace()]

    if args.resalvage_archive:
        prefix = "[dry-run] " if args.dry_run else ""
        total = 0
        for ws in workspaces:
            r = resalvage_archive(ws, args.dry_run, gh)
            total += r["salvaged"]
            if r["files"] or r["missing"]:
                sh = r["shapes"]
                print(f"{prefix}resalvage: [{ws}] {r['files']} archived aspect(s) in {r['topics']} topic(s) → "
                      f"{r['salvaged']} entr(y/ies) into lessons.md (bullet {sh.get('bullet', 0)}, "
                      f"block {sh.get('block', 0)}, bare {sh.get('bare', 0)})"
                      + (f"; {r['not_landed']} not landed" if r["not_landed"] else "")
                      + (f"; {r['unreadable']} topic(s) failed" if r["unreadable"] else "")
                      + (f"; {r['busy']} topic(s) busy — re-run later" if r["busy"] else ""))
                if r["missing"]:
                    print(f"  [{ws}] no live topic folder for: {', '.join(r['missing'][:20])}"
                          + (" …" if len(r["missing"]) > 20 else "") + " — left in .archive")
        print(f"{prefix}resalvage: {total} entr(y/ies) total; nothing was deleted or moved. "
              f"Run `_moc.py --ws <ws>` afterwards.")
        return 0

    total_arch = 0
    total_salv = 0
    total_bytes = 0
    rows: list[dict] = []
    for ws in workspaces:
        r = forget_workspace(ws, ttl_days, max_bytes, salvage, args.dry_run, gh)
        if r["candidates"]:
            rows.append(r)
        total_arch += r["archived"]
        total_salv += r["salvaged"]
        total_bytes += r["bytes"]

    # v4.1 aspect retention: explicit --aspects, or settings opt-in
    # topic_layout.auto_archive_enabled (so the Stop-hook run applies it).
    try:
        auto_aspects = setting("topic_layout.auto_archive_enabled", bool, False)
    except Exception:
        auto_aspects = False
    aspect_rows: list[dict] = []
    if args.aspects or auto_aspects:
        for ws in workspaces:
            ar = forget_aspects(ws, args.aspect_threshold_days, args.keep_newest,
                                args.dry_run, gh)
            if ar["candidates"]:            # also when nothing could be archived
                aspect_rows.append(ar)

    if args.quiet:
        return 0

    prefix = "[dry-run] " if args.dry_run else ""
    if not rows and not aspect_rows:
        print(f"{prefix}forget: no journals older than {ttl_days}d or over {max_bytes} bytes. Buffer is clean.")
        return 0
    if rows:
        mb = total_bytes / 1_000_000
        print(f"{prefix}forget: archived {total_arch} journal file(s) ({mb:.1f} MB freed from active recall), "
              f"salvaged {total_salv} curated entr(y/ies).")
        for r in rows:
            print(f"  [{r['ws']}] {len(r['candidates'])} file(s) → .archive/journal/{r['ws']}/  "
                  f"(+{r['salvaged']} salvaged)")
    for ar in aspect_rows:
        sh = ar["shapes"]
        print(f"{prefix}forget-aspects: [{ar['ws']}] archived {ar['archived']} aspect(s) "
              f">{ar['threshold_days']}d across {ar['topics']} topic(s), "
              f"salvaged {ar['salvaged']} entr(y/ies) into lessons.md "
              f"(bullet {sh.get('bullet', 0)}, block {sh.get('block', 0)}, bare {sh.get('bare', 0)})"
              f" — regen MOCs after.")
        if ar["skipped"]:
            print(f"  [{ar['ws']}] {ar['skipped']} topic(s) left for the next run — entries lock busy "
                  f"or salvage not verified; their unsalvaged aspects were NOT archived.")
    if not args.dry_run:
        print("  recoverable via: gzip -d the .archive copy, or `git -C ~/.gowth-mem log` history.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
