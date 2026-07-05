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
  1. SALVAGE — deterministically lift any genuinely-curated entries (bullet
     lines `- [decision]/[ref]/[tool]/[exp]/...`) that pass minimal gates into
     `<ws>/journal/_salvage.md` (SHA1-deduped), so structured signal is never
     lost to archiving. Raw transcript prose (no leading `- [type]` bullet) is
     NOT salvaged — that's the noise we are forgetting.
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
from _atomic import atomic_write  # type: ignore
from _debug import log_debug  # type: ignore
from _home import gowth_home, journal_dir, list_workspaces, read_settings  # type: ignore
from _lock import file_lock  # type: ignore

DEFAULT_TTL_DAYS = 7          # canon §3: raw journal lives 7 days, then forget
DEFAULT_MAX_BYTES = 75_000    # canon §2: hard distill ceiling ~50-75 KB / file
SALVAGE_FILE = "_salvage.md"

# A curated entry = bullet line opening with a 7-type prefix. Raw transcript
# (assistant/user prose under `### [assistant]`) has no such bullet → not salvaged.
ENTRY_RE = re.compile(
    r"^\s*[-*]\s+\[(?:decision|exp|ref|tool|reflection|skill-ref|secret-ref|goal|hypothesis)\]",
    re.IGNORECASE,
)
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


def _entry_blocks(text: str) -> list[str]:
    """Return curated entry blocks (a bullet line + its indented continuation)."""
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        if ENTRY_RE.match(lines[i]):
            block = [lines[i]]
            j = i + 1
            while j < n and (lines[j].startswith((" ", "\t"))) and lines[j].strip():
                block.append(lines[j])
                j += 1
            out.append("\n".join(block))
            i = j
        else:
            i += 1
    return out


def _salvage_entries(journal_files: list[Path], jd: Path, dry_run: bool) -> int:
    """Lift curated entries from the given journals into journal/_salvage.md.

    Dedupes by SHA1 of the normalized entry body across the existing salvage
    file AND within this run. Returns count of new entries salvaged.
    """
    salvage_path = jd / SALVAGE_FILE
    seen: set[str] = set()
    existing = ""
    if salvage_path.is_file():
        try:
            existing = salvage_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            existing = ""
        for blk in _entry_blocks(existing):
            seen.add(hashlib.sha1(re.sub(r"\s+", " ", blk).strip().lower().encode()).hexdigest())

    new_blocks: list[str] = []
    for f in journal_files:
        try:
            txt = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for blk in _entry_blocks(txt):
            body = blk.strip()
            if len(re.sub(r"^\s*[-*]\s+\[[a-z-]+\]\s*", "", body, flags=re.IGNORECASE)) < MIN_ENTRY_CHARS:
                continue
            h = hashlib.sha1(re.sub(r"\s+", " ", body).strip().lower().encode()).hexdigest()
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
    addition = f"\n\n## salvaged {stamp}\n\n" + "\n".join(new_blocks) + "\n"
    atomic_write(salvage_path, (existing + addition) if existing else (header + addition))
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
    this run. Returns count of new blocks salvaged.
    """
    if not session_files:
        return 0
    salvage_path = jd / SALVAGE_FILE
    seen: set[str] = set()
    existing = ""
    if salvage_path.is_file():
        try:
            existing = salvage_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            existing = ""
        for blk in _extract_review_blocks(existing):
            seen.add(_norm_hash(blk))

    new_blocks: list[str] = []
    for f in session_files:
        try:
            txt = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for blk in _extract_review_blocks(txt):
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
    atomic_write(salvage_path, (existing + addition) if existing else (header + addition))
    return len(new_blocks)


def _archive_one(f: Path, ws: str, gh: Path, dry_run: bool, subdir: str = "") -> bool:
    """Gzip `f` into .archive/journal/<ws>/[<subdir>/] then remove the original."""
    try:
        mtime = int(f.stat().st_mtime)
    except OSError:
        return False
    arc_dir = gh / ".archive" / "journal" / ws
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
        for f in sorted(session_dir.glob("*.md")):
            if f.name.startswith("_"):
                continue
            try:
                st = f.stat()
            except OSError:
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
    args = ap.parse_args()

    gh = gowth_home()
    if not gh.is_dir():
        if not args.quiet:
            print("no ~/.gowth-mem directory; nothing to forget")
        return 0

    cfg = _settings_forget()
    ttl_days = args.ttl_days if args.ttl_days is not None else int(cfg.get("raw_ttl_days", DEFAULT_TTL_DAYS))
    max_bytes = args.max_bytes if args.max_bytes is not None else int(cfg.get("max_bytes", DEFAULT_MAX_BYTES))
    salvage = (not args.no_salvage) and bool(cfg.get("salvage", True))

    from _home import active_workspace  # local import; avoids cycle at module load
    workspaces = list_workspaces() if args.all_workspaces else [active_workspace()]

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

    if args.quiet:
        return 0

    prefix = "[dry-run] " if args.dry_run else ""
    if not rows:
        print(f"{prefix}forget: no journals older than {ttl_days}d or over {max_bytes} bytes. Buffer is clean.")
        return 0
    mb = total_bytes / 1_000_000
    print(f"{prefix}forget: archived {total_arch} journal file(s) ({mb:.1f} MB freed from active recall), "
          f"salvaged {total_salv} curated entr(y/ies).")
    for r in rows:
        print(f"  [{r['ws']}] {len(r['candidates'])} file(s) → .archive/journal/{r['ws']}/  "
              f"(+{r['salvaged']} salvaged)")
    if not args.dry_run:
        print("  recoverable via: gzip -d the .archive copy, or `git -C ~/.gowth-mem log` history.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
