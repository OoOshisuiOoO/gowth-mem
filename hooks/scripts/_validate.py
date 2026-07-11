#!/usr/bin/env python3
"""File-level schema validator (v3.7) — learned from supremor / vault-keeper.

gowth-mem's `_gate.py` validates ENTRY content (line-level). Nothing validated
FILE structure: frontmatter required fields, naming, reserved-path placement —
so 32 hand-written aspect files ended up with no frontmatter (no type/date/topic),
invisible to the agent's wikilink/recall layer and the auto-MOC.

The trueprofit `supremor` vault enforces exactly this via the
`claude-code-vault-keeper` validator: every doc declares a template; the
validator checks frontmatter fields + `$path` + naming after every edit, with a
`vault.heal` detector→patch loop. This module brings that discipline to gowth-mem,
adapted to its v3 file types — deterministic, no LLM.

Checks (per v3 file type):
  <slug>/00-README.md (MOC)      → frontmatter needs: slug, title, type, status
  <slug>/YYYY-MM-DD-<aspect>.md  → frontmatter needs: type=aspect, date, topic, slug, title
  <slug>/lessons.md              → light: has a `## ` entry heading
  Naming  → topic slug + aspect slug match ^[a-z0-9][a-z0-9-]{0,59}$
  Path    → topic files live inside a topic folder (not ws-root, not reserved subdir)

CLI:
  python3 _validate.py --scan [--ws X | --all] [--json]   # report violations
  python3 _validate.py --fix  [--ws X | --all]            # deterministically add
                                                          # missing aspect frontmatter
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import atomic_write  # type: ignore
from _home import (  # type: ignore
    RESERVED_SUBDIRS, active_workspace, gowth_home, list_workspaces, workspace_dir,
)

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")
DATED_ASPECT_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-([a-z0-9][a-z0-9-]{0,59})\.md$")
H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)

REQUIRED = {
    "moc": ["slug", "title", "type", "status"],
    "aspect": ["type", "date", "topic", "slug", "title"],
}


def _frontmatter(text: str) -> dict | None:
    """Return parsed frontmatter dict, or None if the file has no `---` block."""
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    block = text[3:end]
    fm: dict = {}
    for line in block.splitlines():
        m = re.match(r"^([a-zA-Z_][\w-]*):\s*(.*)$", line)
        if m:
            fm[m.group(1)] = m.group(2).strip()
    return fm


def _classify(p: Path, ws_root: Path) -> str | None:
    """Return 'moc' | 'aspect' | 'lessons' | None for a topic file."""
    try:
        rel = p.relative_to(ws_root)
    except ValueError:
        return None
    if rel.parts and rel.parts[0] in RESERVED_SUBDIRS:
        return None
    if p.parent == ws_root:
        return None  # legacy flat file at ws root — out of scope
    if p.name == "00-README.md":
        return "moc"
    if p.name == "lessons.md":
        return "lessons"
    if DATED_ASPECT_RE.match(p.name):
        return "aspect"
    return None


def validate_file(p: Path, ws_root: Path) -> list[str]:
    kind = _classify(p, ws_root)
    if kind is None:
        return []
    try:
        text = p.read_text(errors="ignore")
    except Exception:
        return ["unreadable"]
    issues: list[str] = []

    # Naming.
    if kind == "aspect":
        m = DATED_ASPECT_RE.match(p.name)
        if m and not SLUG_RE.match(m.group(2)):
            issues.append(f"bad-aspect-slug:{m.group(2)}")
    topic = p.parent.name
    if not SLUG_RE.match(topic):
        issues.append(f"bad-topic-slug:{topic}")

    if kind == "lessons":
        if "## " not in text:
            issues.append("lessons-no-entries")
        return issues

    # Frontmatter required fields (moc / aspect).
    fm = _frontmatter(text)
    if fm is None:
        issues.append("missing-frontmatter")
        return issues
    for field in REQUIRED[kind]:
        if not fm.get(field):
            issues.append(f"missing-field:{field}")
    if kind == "aspect" and fm.get("type") and fm["type"] != "aspect":
        issues.append(f"wrong-type:{fm['type']}!=aspect")
    return issues


def _iter_topic_files(ws: str) -> list[Path]:
    root = workspace_dir(ws)
    if not root.is_dir():
        return []
    out: list[Path] = []
    for p in root.rglob("*.md"):
        rel = p.relative_to(root)
        if rel.parts and rel.parts[0] in RESERVED_SUBDIRS:
            continue
        out.append(p)
    return out


def scan_workspace(ws: str) -> list[dict]:
    root = workspace_dir(ws)
    out: list[dict] = []
    for p in _iter_topic_files(ws):
        issues = validate_file(p, root)
        if issues:
            out.append({"file": str(p), "ws": ws, "issues": issues})
    return out


def fix_aspect(p: Path) -> bool:
    """Bring an aspect file's frontmatter to conformance — deterministically, from path.

    Handles BOTH cases: no frontmatter → prepend a full block; partial frontmatter
    → add only the missing required fields + correct a wrong `type`. Every value
    derives from the path (topic=parent folder, date+aspect=filename, slug=topic-aspect,
    title=first H1 or aspect titleized). Preserves existing fields/order. Idempotent.
    """
    m = DATED_ASPECT_RE.match(p.name)
    if not m:
        return False
    text = p.read_text(errors="ignore")
    d, aspect = m.group(1), m.group(2)
    topic = p.parent.name
    h1 = H1_RE.search(text)
    title = h1.group(1).strip() if h1 else aspect.replace("-", " ").strip().title()
    today = date.today().isoformat()
    # v4.1.2: clamp to the SLUG_RE 60-char cap — a long topic+aspect pair
    # produced a 71-char slug that route() later passed to
    # ensure_topic_folder → ValueError (live crash routing a reflection).
    derived_slug = f"{topic}-{aspect}"[:60].rstrip("-")
    derived = {
        "slug": derived_slug, "title": title, "type": "aspect",
        "date": d, "topic": topic, "aspect": aspect, "status": "active",
        "created": d, "last_touched": today,
    }
    field_order = ["slug", "title", "type", "date", "topic", "aspect",
                   "status", "created", "last_touched", "links", "tags"]
    extras = {"links": "[]", "tags": "[]"}

    if not text.startswith("---"):
        block = "---\n" + "".join(
            f"{k}: {derived.get(k, extras.get(k, ''))}\n" for k in field_order) + "---\n\n"
        atomic_write(p, block + text.lstrip("\n"))
        return True

    # Partial frontmatter: merge in missing required fields + fix wrong type.
    end = text.find("\n---", 3)
    if end == -1:
        return False
    fm_inner = text[3:end].strip("\n")
    body = text[end + 4:]
    present: set[str] = set()
    out_lines: list[str] = []
    changed = False
    for line in fm_inner.splitlines():
        mm = re.match(r"^([a-zA-Z_][\w-]*):\s*(.*)$", line)
        if mm:
            key, val = mm.group(1), mm.group(2).strip()
            present.add(key)
            if key == "type" and val not in ("aspect", ""):
                out_lines.append("type: aspect")
                changed = True
                continue
        out_lines.append(line)
    for k in ("type", "date", "topic", "slug", "title", "aspect", "status"):
        if k not in present:
            out_lines.append(f"{k}: {derived[k]}")
            changed = True
    if not changed:
        return False
    atomic_write(p, "---\n" + "\n".join(out_lines) + "\n---" + body)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="File-level schema validator (vault-keeper-style).")
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--fix", action="store_true", help="Add missing frontmatter to aspect files")
    ap.add_argument("--ws")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not gowth_home().is_dir():
        print("no ~/.gowth-mem directory")
        return 0
    wss = list_workspaces() if args.all else [args.ws or active_workspace()]

    if args.fix:
        fixed = 0
        for ws in wss:
            root = workspace_dir(ws)
            for p in _iter_topic_files(ws):
                if _classify(p, root) == "aspect" and fix_aspect(p):
                    fixed += 1
                    print(f"  +frontmatter: {p.relative_to(gowth_home())}")
        print(f"validate --fix: added frontmatter to {fixed} aspect file(s).")
        return 0

    findings: list[dict] = []
    for ws in wss:
        findings.extend(scan_workspace(ws))
    if args.json:
        print(json.dumps(findings, indent=2))
        return 0
    if not findings:
        print("validate: all topic files conform (frontmatter + naming + placement).")
        return 0
    byissue: dict[str, int] = {}
    for f in findings:
        for i in f["issues"]:
            key = i.split(":")[0]
            byissue[key] = byissue.get(key, 0) + 1
    print(f"validate: {len(findings)} file(s) with schema issues:")
    for k, n in sorted(byissue.items(), key=lambda x: -x[1]):
        print(f"  {n:>4}  {k}")
    print("  (run --fix to auto-add aspect frontmatter; --json for file detail)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
