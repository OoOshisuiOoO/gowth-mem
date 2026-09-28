#!/usr/bin/env python3
"""v4.8: the managed block inside <ws>/memory/MEMORY.md (Claude Code auto-memory).

Why this file exists — measured on Claude Code 2.1.283 (2026-09-28):
  * any hook additionalContext / SessionStart stdout of >= 10,000 chars is
    persisted to a tool-results file and the model receives a 2,000-char
    preview. The 15,589-char bootstrap hit that in 193 sessions: handoff.md
    never reached the model.
  * auto-memory MEMORY.md (first 200 lines / 25 KB) is attached with the
    CLAUDE.md instructions bundle at session start, on resume and after every
    compaction, and is NOT subject to that preview rule.

So the working set travels through MEMORY.md. gowth-mem owns the block between
BEGIN and END; everything after END is Claude's own auto-memory index and is
preserved byte-for-byte. The block is deterministic (no timestamps, hostnames
or session ids), so every machine regenerates identical bytes from the synced
vault and git never conflicts on it.

Layout of the block:
    <!-- gowth-mem:begin ws=<ws> -->
    [gowth-mem:bootstrap workspace=<ws> vX.Y.Z]      (+ drift nudge lines)
    ## Rules                 <= 25 fixed lines
    ## Handoff               <= 60 lines, newest first (_handoff.digest)
    ## Topics                <= 40 lines: slug — summary (last_touched)
    ## Recent decisions      <= 10 lines from the index, last 7 days
    ## Secrets (pointers only)  <= 8 lines of env-var NAMES
    ## Using memory          3 fixed lines
    <!-- gowth-mem:end -->

Budget: MAX_LINES / MAX_CHARS, shrunk further when Claude's free zone grows so
the whole file stays under the host's 200-line read limit. Shrink order:
Recent decisions -> Topics (to 10) -> Handoff (to 20) -> Secrets -> Topics ->
Handoff. Rules and Using memory are never cut (the floor).
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import safe_write  # type: ignore
from _debug import log_debug  # type: ignore
from _frontmatter import parse_file  # type: ignore
from _handoff import digest as handoff_digest  # type: ignore
from _home import (  # type: ignore
    RESERVED_SUBDIRS,
    docs_dir,
    gowth_home,
    secrets_md,
    setting,
    workspace_dir,
)
from _lock import file_lock  # type: ignore
from _version import drift_nudge, version_tag  # type: ignore

BEGIN_PREFIX = "<!-- gowth-mem:begin"
BEGIN_FMT = "<!-- gowth-mem:begin ws={ws} -->"
END = "<!-- gowth-mem:end -->"

MAX_LINES = 130
MAX_CHARS = 12_000
HOST_LINE_LIMIT = 200      # Claude Code reads the first 200 lines of MEMORY.md
HOST_MARGIN = 10           # headroom under that limit
HANDOFF_LINES = 60
TOPIC_LINES = 40
DECISION_LINES = 10
SECRET_LINES = 8
HOOK_BOOTSTRAP_CHARS = 8_500

_RULES = [
    "gowth-mem is this machine's persistent memory: one vault (~/.gowth-mem), git-synced, "
    "one active workspace per session.",
    "This block is generated from the vault. Change the vault (handoff, topics), not this "
    "block; your own notes go BELOW the end marker.",
    "Handoff = current state. Read it first; before ending a session update docs/handoff.md "
    "([doing]/[next]/[blocker] bullets with host + date, or /mem-handoff).",
    "Topics hold curated entries typed [decision] [exp] [ref] [tool] [reflection] [skill-ref] "
    "[secret-ref] [goal] [hypothesis].",
    "Write memory through the plugin, never by editing topic files by hand: "
    "/mem-save <type> \"<entry>\" (gate + tags + dedup + index).",
    "[ref] needs a Source:, [decision] a rationale, [tool] a version; a hedged claim is a "
    "[hypothesis] with a Verify: path.",
    "Recall: related entries are injected automatically per prompt; for more run "
    "/mem-recall <query> or Read the topic's 00-README.md listed below.",
    "Secrets are POINTERS (env-var names), never values. Check the pointers below before "
    "asking the user for a credential.",
    "Do not ask the user for anything already in the handoff or a topic; do not re-derive a "
    "decision recorded here.",
    "Curated entries are stored in English; the journal may be bilingual.",
]

_USING = [
    "- /mem-recall <query> — search all curated memory (BM25, deterministic, no LLM)",
    "- Read ~/.gowth-mem/workspaces/<ws>/<slug>/00-README.md before working on a topic listed above",
    "- /mem-save <type> \"<entry>\" to remember; /mem-handoff to update the handoff before ending",
]

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_ENV_RE = re.compile(r"`([A-Z][A-Z0-9_]{3,})`")


# ─── paths ───────────────────────────────────────────────────────────────

def memory_dir(ws: str) -> Path:
    return workspace_dir(ws) / "memory"


def memfile_path(ws: str) -> Path:
    return memory_dir(ws) / "MEMORY.md"


# ─── sections ────────────────────────────────────────────────────────────

def rules_digest() -> list[str]:
    return list(_RULES)


def _topic_readmes(ws: str) -> list[Path]:
    root = workspace_dir(ws)
    if not root.is_dir():
        return []
    out: list[Path] = []
    for p in root.rglob("00-README.md"):
        try:
            rel = p.relative_to(root)
        except ValueError:
            continue
        parts = rel.parts[:-1]
        if not parts or any(x.startswith(".") or x in RESERVED_SUBDIRS for x in parts):
            continue
        out.append(p)
    return out


def _summary_line(fm: dict, body: str, title: str) -> str:
    s = fm.get("summary")
    if isinstance(s, str) and s.strip():
        text = s.strip()
    else:
        text = ""
        for ln in body.splitlines():
            t = ln.strip()
            if not t or t.startswith("#") or t.startswith("_") or t.startswith("<!--"):
                continue
            text = t
            break
        if not text:
            text = title
    text = re.sub(r"\s+", " ", text)
    return text if len(text) <= 100 else text[:99] + "…"


def topic_index(ws: str, max_lines: int = TOPIC_LINES) -> list[str]:
    """`- <slug> — <summary> (last_touched YYYY-MM-DD)`, newest touched first.

    Walks every 00-README.md under the workspace (nested topics as
    parent/child), skipping reserved subdirs — `iter_topic_landings` stops at
    the first landing, so a topic nested inside a topic would be missed.
    """
    root = workspace_dir(ws)
    rows: list[tuple[str, str, str]] = []
    for readme in _topic_readmes(ws):
        try:
            fm, body = parse_file(readme)
        except Exception:
            fm, body = {}, ""
        if not isinstance(fm, dict):
            fm = {}
        slug = readme.parent.relative_to(root).as_posix()
        title = fm.get("title") if isinstance(fm.get("title"), str) else readme.parent.name
        touched = fm.get("last_touched") or fm.get("updated") or fm.get("created") or ""
        touched = str(touched)[:10]
        rows.append((touched, slug, f"- {slug} — {_summary_line(fm, body, title)}"
                                    f" (last_touched {touched or 'unknown'})"))
    rows.sort(key=lambda r: (r[0], r[1]))
    rows.reverse()          # newest touched first; equal dates: slug descending is fine
    # keep slug ascending within equal dates
    ordered: list[str] = []
    i = 0
    while i < len(rows):
        j = i
        while j < len(rows) and rows[j][0] == rows[i][0]:
            j += 1
        ordered.extend(r[2] for r in sorted(rows[i:j], key=lambda r: r[1]))
        i = j
    return ordered[:max_lines]


def recent_decisions(ws: str, days: int = 7, max_lines: int = DECISION_LINES) -> list[str]:
    """`- YYYY-MM-DD [decision] <title> — <path>` from the index, newest first.
    Empty when the index is missing (fail-open, like every read path)."""
    try:
        from _query import query_by_type  # type: ignore
        rows = query_by_type(ws, "decision", "", limit=max_lines * 3, days=days)
    except Exception as exc:
        log_debug("memfile", f"recent_decisions failed: {exc}")
        return []
    out: list[str] = []
    seen: set = set()
    for r in rows:
        path = str(r.get("path") or "")
        if not path or path in seen:
            continue
        seen.add(path)
        heading = (r.get("heading") or "").strip()
        if not heading:
            content = (r.get("content") or "").strip()
            heading = content.splitlines()[0] if content else ""
        heading = re.sub(r"^\s*(?:##\s*)?(?:-\s*)?\[decision\]\s*", "", heading).strip()
        heading = re.sub(r"\s+", " ", heading)
        if len(heading) > 100:
            heading = heading[:99] + "…"
        m = _DATE_RE.search(Path(path).name)
        date = m.group(1) if m else ""
        out.append(f"- {date + ' ' if date else ''}[decision] {heading} — {path}")
        if len(out) >= max_lines:
            break
    return out


def secret_pointers(max_lines: int = SECRET_LINES) -> list[str]:
    """Env-var NAMES found in backticks in shared/secrets.md, six per line.
    Values never enter the block: only the `NAME` tokens are read."""
    p = secrets_md()
    if not p.is_file():
        return []
    try:
        text = p.read_text(errors="ignore")
    except OSError:
        return []
    names: list[str] = []
    for n in _ENV_RE.findall(text):
        if n not in names:
            names.append(n)
    lines: list[str] = []
    for i in range(0, len(names), 6):
        lines.append("- ENV: " + ", ".join(names[i:i + 6]))
        if len(lines) >= max_lines:
            break
    return lines


# ─── rendering ───────────────────────────────────────────────────────────

def _header(ws: str) -> list[str]:
    lines = [f"[gowth-mem:bootstrap workspace={ws}{version_tag()}]"]
    try:
        nudge = drift_nudge()
    except Exception:
        nudge = ""
    for ln in (nudge or "").splitlines():
        if ln.strip():
            lines.append(ln.rstrip())
    return lines


def _compose(header: list[str], sections: list[tuple[str, list[str]]],
             begin: str, end: str) -> str:
    out: list[str] = []
    if begin:
        out.append(begin)
    out.extend(header)
    for title, lines in sections:
        if not lines:
            continue
        out.append(title)
        out.extend(lines)
    if end:
        out.append(end)
    return "\n".join(out) + "\n"


# (section index, minimum kept) — applied in order until the block fits.
_SHRINK_STEPS = (("decisions", 0), ("topics", 10), ("handoff", 20), ("secrets", 0),
                 ("topics", 0), ("handoff", 0))


def _fit(ws: str, *, max_lines: int, max_chars: int, free_zone_lines: int,
         order: tuple, begin: str, end: str) -> str:
    """Render the sections in `order`, shrinking until the block fits."""
    # handoff `## <date>` headers are demoted one level so the block's own
    # `## ` sections stay the only H2 lines in MEMORY.md
    parts = {
        "rules": rules_digest(),
        "handoff": [("#" + ln) if ln.startswith("## ") else ln
                    for ln in handoff_digest(ws, max_lines=HANDOFF_LINES)],
        "topics": topic_index(ws, max_lines=TOPIC_LINES),
        "decisions": recent_decisions(ws, max_lines=DECISION_LINES),
        "secrets": secret_pointers(max_lines=SECRET_LINES),
        "using": list(_USING),
    }
    titles = {
        "rules": "## Rules", "handoff": "## Handoff", "topics": "## Topics",
        "decisions": "## Recent decisions", "secrets": "## Secrets (pointers only)",
        "using": "## Using memory",
    }
    header = _header(ws)

    def build() -> str:
        return _compose(header, [(titles[k], parts[k]) for k in order], begin, end)

    budget = min(max_lines, HOST_LINE_LIMIT - HOST_MARGIN - max(0, free_zone_lines))
    text = build()
    for name, keep in _SHRINK_STEPS:
        if text.count("\n") <= budget:
            break
        if len(parts[name]) > keep:
            parts[name] = parts[name][:keep]
            text = build()
    # character budget: trim the longest shrinkable section one line at a time
    shrinkable = ("handoff", "topics", "decisions", "secrets")
    while len(text) > max_chars:
        longest = max(shrinkable, key=lambda k: sum(len(x) for x in parts[k]))
        if not parts[longest]:
            break
        parts[longest] = parts[longest][:-1]
        text = build()
    return text


_BLOCK_ORDER = ("rules", "handoff", "topics", "decisions", "secrets", "using")
_HOOK_ORDER = ("handoff", "rules", "topics", "decisions", "secrets", "using")


def render(ws: str, *, max_lines: int = MAX_LINES, max_chars: int = MAX_CHARS,
           free_zone_lines: int = 0) -> str:
    """The managed block, markers included, trailing newline. Pure."""
    return _fit(ws, max_lines=max_lines, max_chars=max_chars,
                free_zone_lines=free_zone_lines, order=_BLOCK_ORDER,
                begin=BEGIN_FMT.format(ws=ws), end=END)


def floor_lines() -> int:
    """Line count of the smallest possible block (Rules + Using memory only)."""
    header = _header("x")
    return _compose(header, [("## Rules", rules_digest()), ("## Using memory", list(_USING))],
                    BEGIN_FMT.format(ws="x"), END).count("\n")


def render_hook_bootstrap(ws: str, max_chars: int = HOOK_BOOTSTRAP_CHARS) -> str:
    """Fallback SessionStart payload when native memory is not wired: same
    sections, no markers, handoff before rules, under the host preview rule."""
    return _fit(ws, max_lines=HOST_LINE_LIMIT, max_chars=max_chars, free_zone_lines=0,
                order=_HOOK_ORDER, begin="", end="")


# ─── file handling ───────────────────────────────────────────────────────

def split(text: str) -> tuple[str, str]:
    """(block, free_zone). Without BOTH markers the whole text is free zone —
    a begin marker with no end (hand-edited file) is never treated as a block."""
    b = text.find(BEGIN_PREFIX)
    e = text.find(END)
    if b < 0 or e < 0 or e < b:
        return "", text
    end = e + len(END)
    if text[end:end + 1] == "\n":
        end += 1
    return text[b:end], text[:b] + text[end:]


def write(ws: str) -> bool:
    """Regenerate the block; rewrite the file only when the block changed.
    Returns True when the file was written."""
    p = memfile_path(ws)
    try:
        with file_lock(f"memfile-{ws}", timeout=10.0):
            old = p.read_text(errors="ignore") if p.is_file() else ""
            old_block, free = split(old)
            free_lines = free.count("\n") + (1 if free and not free.endswith("\n") else 0)
            new_block = render(ws,
                               max_lines=setting("memfile.max_lines", int, MAX_LINES),
                               max_chars=setting("memfile.max_chars", int, MAX_CHARS),
                               free_zone_lines=free_lines)
            if old_block and hashlib.sha1(old_block.encode()).hexdigest() == \
                    hashlib.sha1(new_block.encode()).hexdigest():
                return False
            p.parent.mkdir(parents=True, exist_ok=True)
            safe_write(p, new_block + free)
            return True
    except Exception as exc:
        log_debug("memfile", f"write failed for ws={ws}: {exc}")
        return False


def sources_changed(ws: str) -> bool:
    """True when any input of the block is newer than the file (or no file)."""
    p = memfile_path(ws)
    if not p.is_file():
        return True
    try:
        ref = p.stat().st_mtime
    except OSError:
        return True
    candidates = [docs_dir(ws) / "handoff.md", secrets_md(), gowth_home() / "index.db",
                  Path(__file__).resolve().parent.parent.parent / ".claude-plugin" / "plugin.json"]
    candidates.extend(_topic_readmes(ws))
    for c in candidates:
        try:
            if c.is_file() and c.stat().st_mtime > ref:
                return True
        except OSError:
            continue
    return False


def _cli() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="gowth-mem v4.8 MEMORY.md managed block")
    ap.add_argument("--ws", default="", help="workspace (default: active)")
    ap.add_argument("--print", action="store_true", help="render to stdout, write nothing")
    ap.add_argument("--hook", action="store_true", help="render the hook fallback bootstrap")
    args = ap.parse_args()
    from _home import active_workspace  # type: ignore
    ws = args.ws or active_workspace()
    if args.hook:
        sys.stdout.write(render_hook_bootstrap(ws))
        return 0
    if args.print:
        sys.stdout.write(render(ws))
        return 0
    changed = write(ws)
    print(f"memfile: {'written' if changed else 'unchanged'} {memfile_path(ws)}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
