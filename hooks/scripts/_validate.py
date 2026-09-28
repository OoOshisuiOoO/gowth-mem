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
import os
import re
import sys
import time
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


# ── v4.7.6: README-only folders minted by the pre-4.7.6 routing bug ──────────
#
# route()/derive_topic_slug() took the topic slug from the best-matching file's
# frontmatter `slug:` — for a dated aspect that is `<topic>-<aspect>` (set by
# fix_aspect above) — and ensure_topic_folder() created
# `<ws>/<topic>-<aspect>/00-README.md` beside the real topic (31 in the live
# vault). Machines still on an older version keep minting them until upgraded,
# so this is a repeatable repair, not a one-off script.

_TOLERATED_JUNK_EXTRAS = frozenset({".DS_Store"})
_README = "00-README.md"
_DATE_FIELDS_RE = re.compile(r"^(created|last_touched): .*$", re.MULTILINE)


def _is_pristine_skeleton(text: str, slug: str) -> bool:
    """True iff `text` is, byte for byte, the README `ensure_topic_folder(slug)`
    writes with every default — `type: misc`, `status: draft`, the default
    title, empty parents/links/aliases/tags, the TODO TL;DR — ignoring only
    the two dates. Any curation (an alias, a type, a title, a status, one word
    in the body) makes it a topic someone kept, never junk."""
    try:
        from _topic_templates import render  # type: ignore
        skeleton = render("misc", slug, slug.replace("-", " ").title(),
                          date.today().isoformat(), [], "")
        norm = lambda t: _DATE_FIELDS_RE.sub(r"\1: -", t).strip()  # noqa: E731
        return norm(text) == norm(skeleton)
    except Exception:
        return False


def _iter_topic_files_under(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    out: list[Path] = []
    for p in root.rglob("*.md"):
        rel = p.relative_to(root)
        if rel.parts and rel.parts[0] in RESERVED_SUBDIRS:
            continue
        out.append(p)
    return out


def _frontmatter_slugs(root: Path) -> dict[str, list[Path]]:
    """Frontmatter `slug:` → files carrying it — the exact values the old
    route() mistook for topic names (it trusted ANY file's `slug:`, not only
    fix_aspect's `<topic>-<aspect>`: the live junk also came from renamed,
    moved and research-imported aspects and from a lessons.md)."""
    slugs: dict[str, list[Path]] = {}
    for p in _iter_topic_files_under(root):
        try:
            fm = _frontmatter(p.read_text(errors="ignore")) or {}
        except Exception:
            continue
        s = fm.get("slug")
        if isinstance(s, str) and s:
            slugs.setdefault(s, []).append(p)
    return slugs


def _plain_file(p: Path) -> bool:
    return p.is_file() and not p.is_symlink()


def _junk_reason(folder: Path, root: Path, slugs: dict[str, list[Path]]) -> Path | None:
    """The file whose frontmatter slug minted `folder`, or None if `folder` is
    not provably junk. ALL must hold — anything less is left alone:
      1. a real top-level directory of the workspace (not a symlink — a link
         could point anywhere, inside the vault or out of it);
      2. it holds nothing but a plain-file `00-README.md` (a plain Finder
         `.DS_Store` is tolerated);
      3. that README is a pristine default skeleton (`_is_pristine_skeleton`);
      4. its name is the frontmatter `slug:` of a file in ANOTHER folder.
    """
    try:
        if folder.is_symlink() or not folder.is_dir():
            return None
        if folder.resolve().parent != root.resolve():
            return None
        names = {x.name for x in folder.iterdir()}
    except OSError:
        return None
    if _README not in names or names - {_README} - _TOLERATED_JUNK_EXTRAS:
        return None
    if not _plain_file(folder / _README) or any(
            not _plain_file(folder / x) for x in names & _TOLERATED_JUNK_EXTRAS):
        return None   # a symlink (dangling ones included) is never "a Finder file"
    source = next((f for f in slugs.get(folder.name, []) if f.parent != folder), None)
    if source is None:
        return None
    try:
        text = (folder / _README).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return source if _is_pristine_skeleton(text, folder.name) else None


def junk_topic_folders(ws: str, slugs: dict[str, list[Path]] | None = None) -> list[dict]:
    """Top-level README-only folders minted by the routing bug (see above).
    Junk is always a direct child of the workspace: the old code called
    ensure_topic_folder(slug) with no parents."""
    root = workspace_dir(ws)
    if not root.is_dir():
        return []
    slugs = _frontmatter_slugs(root) if slugs is None else slugs
    out: list[dict] = []
    for d in sorted(root.iterdir()):
        if d.name.startswith(".") or d.name in RESERVED_SUBDIRS:
            continue
        source = _junk_reason(d, root, slugs)
        if source is not None:
            out.append({"folder": str(d), "ws": ws, "minted_from": str(source)})
    return out


_HELD_RE = re.compile(r"^\.00-README\.md\.pruning-(\d+)$")


def _restore(held: Path, readme: Path, pristine: bool) -> None:
    """Put a README we took out of play back — never over a newer one. If a
    newer README appeared meanwhile, a pristine held copy is redundant and
    goes; an EDITED one is kept, visibly, as `00-README.conflict-<pid>.md`."""
    try:
        os.link(held, readme)       # fails if a README reappeared meanwhile
        held.unlink()
        return
    except FileExistsError:
        if pristine:
            held.unlink(missing_ok=True)
            return
        keep = readme.with_name(f"00-README.conflict-{os.getpid()}.md")
        try:
            os.rename(held, keep)
            print(f"validate: a README was edited during a junk prune — kept as {keep}",
                  file=sys.stderr)
            return
        except OSError:
            pass
    except OSError:
        try:
            if not readme.exists():
                os.rename(held, readme)
                return
        except OSError:
            pass
    try:
        from _debug import log_debug  # type: ignore
        log_debug("validate", f"could not restore {held} to {readme}")
    except Exception:
        pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True   # exists but not ours, or unknowable: never race a live prune
    return True


_HELD_STALE_S = 3600   # a prune holds a README for milliseconds; an hour means abandoned


def _abandoned(held: Path) -> bool:
    """Its prune is gone: the pid is dead, or the hold is older than an hour —
    pids repeat (containers, long uptimes) and mean nothing for a leftover
    synced from another machine. `st_ctime` is the rename time (mtime is the
    README's old modification time, which rename keeps)."""
    try:
        if time.time() - held.stat().st_ctime > _HELD_STALE_S:
            return True
    except OSError:
        return False
    m = _HELD_RE.match(held.name)
    return bool(m) and not _pid_alive(int(m.group(1)))


def abandoned_prunes(root: Path) -> list[Path]:
    """Held READMEs whose prune is gone and whose folder has no README — what
    `--scan` reports and `--prune-junk` puts back."""
    out: list[Path] = []
    try:
        folders = [d for d in root.iterdir() if d.is_dir() and not d.is_symlink()]
    except OSError:
        return out
    for d in folders:
        try:
            held = [x for x in d.iterdir() if _HELD_RE.match(x.name) and _plain_file(x)]
        except OSError:
            continue
        out.extend(h for h in held if not (d / _README).exists() and _abandoned(h))
    return out


def _recover_abandoned_prunes(root: Path) -> int:
    """A prune killed between its rename and its delete leaves a folder whose
    README sits aside as `.00-README.md.pruning-<pid>` — invisible to index
    and MOC. Put it back when that prune is gone and the folder has no README;
    anything else is left alone."""
    restored = 0
    for h in abandoned_prunes(root):
        if not (h.parent / _README).exists():
            _restore(h, h.parent / _README, pristine=False)
            restored += 1
    return restored


def _put_back(held: Path, readme: Path, folder: Path, text: str | None) -> None:
    """Best effort: make sure `folder` keeps its README after a failed prune."""
    try:
        if held.exists():
            _restore(held, readme, pristine=text is not None
                     and _is_pristine_skeleton(text, folder.name))
        elif text is not None and folder.is_dir() and not readme.exists():
            atomic_write(readme, text)
    except Exception:
        pass


def remove_junk_folder(folder: Path, ws: str,
                       slugs: dict[str, list[Path]] | None = None) -> bool:
    """Delete one junk folder — only after re-checking every condition on the
    bytes actually being deleted. The vault is shared by concurrent sessions,
    so: the README is RENAMED aside inside the folder (atomic; nobody writes to
    that name) and the held bytes re-verified — an edit that raced the check
    is restored; the folder is re-listed right before the delete — anything
    that arrived keeps it; files are unlinked one by one and the folder
    removed with `rmdir` — if even that loses a race, the README is written
    back from the verified bytes; and an interrupt at any point puts the
    README back. Never a recursive delete; the memory repo's git history keeps
    the skeleton.
    """
    folder = Path(folder)
    root = workspace_dir(ws)
    slugs = _frontmatter_slugs(root) if slugs is None else slugs
    if _junk_reason(folder, root, slugs) is None:
        return False
    readme = folder / _README
    held = folder / f".{_README}.pruning-{os.getpid()}"
    try:
        os.rename(readme, held)
    except OSError:
        return False
    text: str | None = None
    try:
        try:
            text = held.read_text(encoding="utf-8") if _plain_file(held) else None
        except (OSError, UnicodeDecodeError):
            text = None
        if text is None or not _is_pristine_skeleton(text, folder.name):
            _restore(held, readme, pristine=False)   # edited meanwhile: a topic now
            return False
        others = {x.name for x in folder.iterdir()} - {held.name}
        if others - _TOLERATED_JUNK_EXTRAS or any(not _plain_file(folder / x) for x in others):
            _restore(held, readme, pristine=True)    # something arrived: keep the landing
            return False
        for x in others & _TOLERATED_JUNK_EXTRAS:   # never anything else, whatever the check above says
            (folder / x).unlink(missing_ok=True)
        held.unlink()
        try:
            folder.rmdir()
        except OSError:
            # A file landed in the last instant: give the folder its landing back.
            try:
                if folder.is_dir() and not readme.exists():
                    atomic_write(readme, text)
            except OSError:
                pass
            return False
    except OSError:
        # One undeletable file (an immutable .DS_Store, a permission) must not
        # abort the whole run: put the README back and move on.
        _put_back(held, readme, folder, text)
        return False
    except BaseException:
        # Ctrl-C or a crash mid-prune: never leave the folder without its README.
        _put_back(held, readme, folder, text)
        raise
    try:
        from _index import reindex_paths  # type: ignore
        reindex_paths([readme])  # drops the index rows of the deleted README
    except Exception:
        pass
    return True


def _prune_junk(ws: str) -> int:
    """Remove the junk folders `--scan` reports for `ws` right now (same slug
    map, computed before any other repair in this run); rebuild `_MAP.md`."""
    root = workspace_dir(ws)
    try:
        from _sync import write_default_gitignore  # type: ignore
        write_default_gitignore(gowth_home())  # backfills `.*.pruning-*` on existing vaults
    except Exception:
        pass
    _recover_abandoned_prunes(root)
    slugs = _frontmatter_slugs(root)
    removed = 0
    for j in junk_topic_folders(ws, slugs):
        if remove_junk_folder(Path(j["folder"]), ws, slugs):
            removed += 1
            print(f"  -junk folder: {Path(j['folder']).relative_to(gowth_home())}"
                  f"  (minted from {Path(j['minted_from']).relative_to(root)})")
    if removed:
        try:
            from _moc import rebuild_workspace_moc  # type: ignore
            rebuild_workspace_moc(ws)  # _MAP.md listed the junk topics
        except Exception:
            pass
    return removed


def main() -> int:
    ap = argparse.ArgumentParser(description="File-level schema validator (vault-keeper-style).")
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--fix", action="store_true", help="Add missing frontmatter to aspect files")
    ap.add_argument("--prune-junk", action="store_true",
                    help="Delete the junk README-only folders --scan reports (v4.7.6)")
    ap.add_argument("--ws")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not gowth_home().is_dir():
        print("no ~/.gowth-mem directory")
        return 0
    wss = list_workspaces() if args.all else [args.ws or active_workspace()]

    if args.fix or args.prune_junk:
        # Prune FIRST: --fix stamps `slug:` onto aspects that had none, and
        # a slug written by this very run is no evidence that the old router
        # ever minted a folder from it (v4.7.6 review, H1).
        if args.prune_junk:
            removed = sum(_prune_junk(ws) for ws in wss)
            print(f"validate --prune-junk: removed {removed} junk README-only folder(s).")
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
        for j in junk_topic_folders(ws):
            findings.append({"file": str(Path(j["folder"]) / _README), "ws": ws,
                             "issues": [f"junk-topic-folder:{j['minted_from']}"]})
        for h in abandoned_prunes(workspace_dir(ws)):
            findings.append({"file": str(h), "ws": ws,
                             "issues": ["abandoned-prune:README held aside by a killed prune"]})
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
    print("  (--fix adds aspect frontmatter; --prune-junk deletes the junk-topic-folder "
          "entries listed here and restores abandoned-prune READMEs; --json for file detail)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
