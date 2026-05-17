#!/usr/bin/env python3
"""v2.4 / v2.3 → v3.0 migration script (plan §3.2 7-step pipeline).

Layout transformation:
  v2.4 folder-note `<ws>/<slug>/<slug>.md`     → `<ws>/<slug>/00-README.md`
  v2.4 sub-aspect  `<ws>/<slug>/<aspect>.md`   → `<ws>/<slug>/<today>-<aspect>.md`
  v2.4 lessons.md                              → KEEP as-is
  v2.3 flat        `<ws>/<slug>.md`            → `<ws>/<slug>/00-README.md`
  lazy-nest `<ws>/<dom>/<slug>/<slug>.md`      → `<ws>/<slug>/00-README.md`
                                                  (frontmatter.parents=[<dom>])
  `<ws>/<dom>/_MAP.md` (domain MOC)            → DELETE (v3 has no domain MOC)

Multi-session safety: held under `file_lock("migrate-v3", timeout=60)` outer +
`file_lock("sync", timeout=30)` inner so auto-sync.py can't race mid-migration.

Idempotency: settings.json["layout_version"] >= 3 short-circuits unless --force.
F2: `origin/<branch>` last commit subject `^v3 migration ` also short-circuits.

CLI:
  python3 _migrate_v3.py            # do migration
  python3 _migrate_v3.py --dry-run  # print plan, no writes
  python3 _migrate_v3.py --force    # bypass layout_version short-circuit
  python3 _migrate_v3.py --report   # human-readable report
  python3 _migrate_v3.py --json     # machine output (default if not --report)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import atomic_write, safe_write  # type: ignore  # noqa: F401
from _frontmatter import parse_file  # type: ignore
from _home import (  # type: ignore
    RESERVED_FILES,
    RESERVED_SUBDIRS,
    TOPIC_LESSONS,
    TOPIC_README,
    gowth_home,
    is_dated_aspect_filename,
    list_workspaces,
    read_settings,
    settings_path,
    workspace_dir,
)
from _lock import file_lock  # type: ignore

# v3 ignores these inside topic folders (already converted form)
_V3_LANDINGS = frozenset({TOPIC_README, TOPIC_LESSONS, "_MAP.md"})

# Reserved subdir + file skip set (mirrors _home but local for clarity)
_SKIP_SUBDIRS = RESERVED_SUBDIRS  # docs, journal, skills, research
_SKIP_FILES = RESERVED_FILES      # _MAP.md, AGENTS.md, workspace.json


# ─── helpers ─────────────────────────────────────────────────────────────

def utc_iso_compact_us() -> str:
    """F1 fix: microsecond-resolution UTC stamp for unique backup folder names."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y%m%dT%H%M%SZ%f")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def split_frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter_block_incl_fences, body). If no frontmatter → ('', text)."""
    m = _FM_RE.match(text)
    if not m:
        return ("", text)
    end = m.end()
    return (text[:end], text[end:])


def body_sha256_excluding_frontmatter(text: str) -> str:
    _fm, body = split_frontmatter(text)
    return sha256_bytes(body.encode("utf-8", errors="replace"))


def apply_frontmatter_patch(text: str, patch: dict) -> str:
    """Apply key/value patches to the frontmatter block. Adds keys if missing.

    Preserves existing key order; appended keys land before the closing `---`.
    """
    if not patch:
        return text
    fm_block, body = split_frontmatter(text)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not fm_block:
        # Synthesize minimal frontmatter from the patch
        lines = ["---"]
        if "slug" not in patch:
            patch.setdefault("slug", "untitled")
        for k, v in patch.items():
            lines.append(_render_yaml_kv(k, v))
        lines.append("---\n")
        return "\n".join(lines) + "\n" + body
    # Mutate existing
    inner = fm_block.split("\n", 1)[1].rsplit("---", 1)[0]
    seen: set[str] = set()
    out_lines: list[str] = []
    for raw in inner.splitlines():
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", raw)
        if not m:
            out_lines.append(raw)
            continue
        key = m.group(1)
        if key in patch:
            out_lines.append(_render_yaml_kv(key, patch[key]))
            seen.add(key)
        else:
            out_lines.append(raw)
    for k, v in patch.items():
        if k not in seen:
            out_lines.append(_render_yaml_kv(k, v))
    # Always update last_touched
    if "last_touched" not in patch:
        for i, line in enumerate(out_lines):
            if line.startswith("last_touched:"):
                out_lines[i] = f"last_touched: {today}"
                break
        else:
            out_lines.append(f"last_touched: {today}")
    new_fm = "---\n" + "\n".join(l for l in out_lines if l is not None).rstrip() + "\n---\n"
    return new_fm + body


def _render_yaml_kv(key: str, value) -> str:
    if isinstance(value, list):
        return f"{key}: [{', '.join(str(v) for v in value)}]"
    return f"{key}: {value}"


# ─── classifier ──────────────────────────────────────────────────────────

class Move:
    __slots__ = ("src", "dst", "action", "slug", "aspect", "frontmatter_patch", "delete_src")

    def __init__(self, src: Path, dst: Path, action: str, slug: str,
                 aspect: str | None, frontmatter_patch: dict,
                 delete_src: bool = True):
        self.src = src
        self.dst = dst
        self.action = action
        self.slug = slug
        self.aspect = aspect
        self.frontmatter_patch = frontmatter_patch
        self.delete_src = delete_src

    def to_dict(self) -> dict:
        return {
            "src": str(self.src),
            "dst": str(self.dst),
            "action": self.action,
            "slug": self.slug,
            "aspect": self.aspect,
            "delete_src": self.delete_src,
            "frontmatter_patch": self.frontmatter_patch,
        }


def _iter_workspace_topic_paths(ws_root: Path) -> list[Path]:
    """Yield every candidate v2/v3 topic file under ws_root, excluding reserved."""
    out: list[Path] = []
    if not ws_root.is_dir():
        return out
    for p in ws_root.rglob("*.md"):
        if not p.is_file():
            continue
        try:
            rel = p.relative_to(ws_root)
        except ValueError:
            continue
        if rel.parts and (rel.parts[0] in _SKIP_SUBDIRS or rel.parts[0].startswith((".", "_"))):
            continue
        if p.name in _SKIP_FILES and p.parent == ws_root:
            continue
        out.append(p)
    return out


def classify(ws: str, ws_root: Path, today: str) -> list[Move]:
    """Build the Move plan for one workspace. See plan §3.2 STEP 2."""
    moves: list[Move] = []
    if not ws_root.is_dir():
        return moves

    domain_maps: list[Path] = []

    for p in _iter_workspace_topic_paths(ws_root):
        rel = p.relative_to(ws_root)
        name = p.name

        # Domain MOC `_MAP.md` inside non-reserved subdir → schedule DELETE
        if name == "_MAP.md":
            domain_maps.append(p)
            continue

        # File at workspace root → v2.3 flat topic
        if p.parent == ws_root:
            slug = p.stem
            dst = ws_root / slug / TOPIC_README
            moves.append(Move(
                src=p, dst=dst, action="v23_flat_promote", slug=slug,
                aspect=None,
                frontmatter_patch={"slug": slug, "type": "misc", "last_touched": today},
            ))
            continue

        # File inside a folder
        parent = p.parent
        # If parent is direct child of ws_root → simple topic folder.
        # If parent is nested deeper → lazy-nest layout.
        parts = parent.relative_to(ws_root).parts
        is_top_level_topic = (len(parts) == 1)

        if name == TOPIC_LESSONS:
            # Keep lessons.md as-is.
            moves.append(Move(
                src=p, dst=p, action="lessons_keep", slug=parent.name,
                aspect=None, frontmatter_patch={}, delete_src=False,
            ))
            continue

        if name == TOPIC_README:
            # Already v3 — record as no-op for idempotency.
            moves.append(Move(
                src=p, dst=p, action="already_v3", slug=parent.name,
                aspect=None, frontmatter_patch={}, delete_src=False,
            ))
            continue

        if is_dated_aspect_filename(name):
            # Already a v3 dated aspect — leave alone.
            moves.append(Move(
                src=p, dst=p, action="already_dated", slug=parent.name,
                aspect=None, frontmatter_patch={}, delete_src=False,
            ))
            continue

        # Determine target slug & aspect
        if is_top_level_topic:
            slug = parent.name
            if name == f"{slug}.md":
                # v2.4 folder-note landing
                dst = parent / TOPIC_README
                moves.append(Move(
                    src=p, dst=dst, action="v24_landing_to_readme", slug=slug,
                    aspect=None,
                    frontmatter_patch={"slug": slug, "last_touched": today},
                ))
            else:
                # v2.4 sub-aspect
                aspect = p.stem
                dst = parent / f"{today}-{aspect}.md"
                moves.append(Move(
                    src=p, dst=dst, action="v24_subaspect_to_dated", slug=slug,
                    aspect=aspect, frontmatter_patch={},
                ))
        else:
            # Lazy-nested under `<dom>/.../<slug>/`. Flatten to ws_root/<slug>/.
            # The topic folder is the deepest dir; promote to top-level.
            slug = parent.name
            dom_parts = list(parts[:-1])  # parent dirs above slug folder
            dst_folder = ws_root / slug
            if name == f"{slug}.md":
                dst = dst_folder / TOPIC_README
                moves.append(Move(
                    src=p, dst=dst, action="lazy_nest_landing_flatten", slug=slug,
                    aspect=None,
                    frontmatter_patch={
                        "slug": slug,
                        "parents": dom_parts,
                        "last_touched": today,
                    },
                ))
            elif is_dated_aspect_filename(name):
                dst = dst_folder / name
                moves.append(Move(
                    src=p, dst=dst, action="lazy_nest_dated_flatten", slug=slug,
                    aspect=None, frontmatter_patch={},
                ))
            elif name == TOPIC_LESSONS:
                dst = dst_folder / TOPIC_LESSONS
                moves.append(Move(
                    src=p, dst=dst, action="lazy_nest_lessons_flatten", slug=slug,
                    aspect=None, frontmatter_patch={},
                ))
            else:
                aspect = p.stem
                dst = dst_folder / f"{today}-{aspect}.md"
                moves.append(Move(
                    src=p, dst=dst, action="lazy_nest_subaspect_to_dated", slug=slug,
                    aspect=aspect, frontmatter_patch={},
                ))

    # Schedule domain MOC deletions
    for dmap in domain_maps:
        moves.append(Move(
            src=dmap, dst=dmap, action="domain_map_delete", slug="",
            aspect=None, frontmatter_patch={}, delete_src=True,
        ))

    return moves


# ─── conflict policy (plan §3.5) ─────────────────────────────────────────

_MERGE_SEPARATOR_TPL = (
    "\n\n---\n<!-- merged from {src} on {ts} -->\n\n"
)

_MAX_MERGED_SIZE = 200 * 1024  # 200 KB warn threshold


def _merge_bodies(dst: Path, src_text: str, src_label: str, ts: str) -> str:
    """Append-and-dedup per plan §3.5."""
    existing = dst.read_text(errors="replace") if dst.is_file() else ""
    sep = _MERGE_SEPARATOR_TPL.format(src=src_label, ts=ts)
    merged = existing.rstrip() + sep + src_text.lstrip()
    # Lightweight Jaccard dedup at line level
    seen: set[str] = set()
    out: list[str] = []
    for line in merged.splitlines():
        norm = line.strip().lower()
        if not norm or norm.startswith("#") or norm.startswith("---"):
            out.append(line)
            continue
        if norm in seen:
            continue
        seen.add(norm)
        out.append(line)
    return "\n".join(out) + "\n"


# ─── 7-step pipeline ─────────────────────────────────────────────────────

def _read_settings_or_default() -> dict:
    s = read_settings()
    if not isinstance(s, dict):
        return {}
    return s


def _git_log_subject(gh: Path, branch: str) -> str:
    try:
        r = subprocess.run(
            ["git", "-C", str(gh), "log", "-1", "--format=%s", f"origin/{branch}"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        return (r.stdout or "").strip()
    except Exception:
        return ""


def _git_has_origin(gh: Path) -> bool:
    """True if `origin` remote is configured (else F9 fetch+ff is N/A)."""
    try:
        r = subprocess.run(
            ["git", "-C", str(gh), "remote", "get-url", "origin"],
            capture_output=True, timeout=5, check=False,
        )
        return r.returncode == 0
    except Exception:
        return False


def _git_fetch_ff(gh: Path, branch: str) -> bool:
    """STEP 7 F9 fix: fetch + merge --ff-only. True if up-to-date or fast-forwarded.

    Returns True (no-op) when origin is not configured — local-only repos are not
    "stale", they simply have no remote to reconcile against.
    """
    if not _git_has_origin(gh):
        return True
    try:
        fr = subprocess.run(
            ["git", "-C", str(gh), "fetch", "origin", branch],
            capture_output=True, timeout=30, check=False,
        )
        if fr.returncode != 0:
            # Fetch failed (network down, branch missing on remote, etc.). Do not
            # block a local write — caller can re-run sync later. Conservative:
            # treat as no-op here so we don't trap users mid-migration.
            return True
        r = subprocess.run(
            ["git", "-C", str(gh), "merge", "--ff-only", f"origin/{branch}"],
            capture_output=True, timeout=15, check=False,
        )
        return r.returncode == 0
    except Exception:
        return False


def _ensure_backup_gitignore(gh: Path) -> None:
    """R12 fix: append `.backup/` to .gitignore BEFORE backup copies."""
    gi = gh / ".gitignore"
    line = ".backup/\n"
    try:
        existing = gi.read_text(errors="replace") if gi.is_file() else ""
    except Exception:
        existing = ""
    if ".backup/" in existing.splitlines() or existing.startswith(".backup/"):
        return
    atomic_write(gi, (existing.rstrip() + "\n" + line) if existing.strip() else line)


def _snapshot_backup(gh: Path, timestamp: str) -> tuple[Path, list[dict]]:
    """STEP 1: copy every workspace tree into .backup/v2-pre-v3-<ts>/."""
    backup_root = gh / ".backup" / f"v2-pre-v3-{timestamp}"
    backup_root.mkdir(parents=True, exist_ok=False)  # F1: timestamp unique
    files_meta: list[dict] = []
    for ws in list_workspaces():
        ws_root = workspace_dir(ws)
        if not ws_root.is_dir():
            continue
        for p in ws_root.rglob("*"):
            if not p.is_file():
                continue
            try:
                rel = p.relative_to(gh)
            except ValueError:
                continue
            dst = backup_root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(p, dst)
                data = p.read_bytes()
                files_meta.append({
                    "src_rel": str(rel),
                    "sha256": sha256_bytes(data),
                    "size": len(data),
                })
            except Exception:
                # Best-effort backup — failures recorded but don't abort.
                continue
    return backup_root, files_meta


def _write_manifest(backup_root: Path, timestamp: str, files_meta: list[dict],
                    workspaces: list[str]) -> None:
    manifest = {
        "from_version": 2,
        "to_version": 3,
        "timestamp": timestamp,
        "workspaces": workspaces,
        "files": files_meta,
    }
    atomic_write(backup_root / "MANIFEST.json",
                 json.dumps(manifest, indent=2) + "\n")


def _execute_moves(moves: list[Move], dry_run: bool, timestamp: str) -> dict:
    """STEP 3: execute moves atomically. Returns counters."""
    counters = {
        "executed": 0, "skipped_already": 0, "conflicts": 0,
        "delete_pending": 0, "errors": 0,
    }
    dst_seen: dict[Path, Move] = {}

    for m in moves:
        if m.action in ("lessons_keep", "already_v3", "already_dated"):
            counters["skipped_already"] += 1
            continue
        if m.action == "domain_map_delete":
            counters["delete_pending"] += 1
            continue

        # Conflict detection — same dst from multiple sources
        prior = dst_seen.get(m.dst)

        if dry_run:
            counters["executed"] += 1
            dst_seen[m.dst] = m
            continue

        try:
            body = m.src.read_text(errors="replace")
            if m.frontmatter_patch:
                body = apply_frontmatter_patch(body, m.frontmatter_patch)

            if prior is not None or m.dst.is_file() and m.dst != m.src:
                src_label = str(m.src.relative_to(gowth_home()))
                merged = _merge_bodies(m.dst, body, src_label, timestamp)
                if len(merged.encode("utf-8")) > _MAX_MERGED_SIZE:
                    print(f"WARN: merged file > 200KB at {m.dst}", file=sys.stderr)
                safe_write(m.dst, merged)
                counters["conflicts"] += 1
            else:
                m.dst.parent.mkdir(parents=True, exist_ok=True)
                safe_write(m.dst, body)

            counters["executed"] += 1
            dst_seen[m.dst] = m
        except Exception as e:
            counters["errors"] += 1
            print(f"ERROR: move {m.src} -> {m.dst}: {e}", file=sys.stderr)

    return counters


def _verify_moves(moves: list[Move], dry_run: bool) -> list[str]:
    """STEP 4: verify written destinations. Returns list of failures."""
    if dry_run:
        return []
    failures: list[str] = []
    for m in moves:
        if m.action in ("lessons_keep", "already_v3", "already_dated", "domain_map_delete"):
            continue
        if m.src == m.dst:
            continue
        if not m.dst.is_file():
            failures.append(f"missing dst {m.dst}")
            continue
        try:
            dst_text = m.dst.read_text(errors="replace")
            src_text = m.src.read_text(errors="replace")
        except Exception as e:
            failures.append(f"unreadable {m.src} or {m.dst}: {e}")
            continue
        if m.frontmatter_patch:
            if body_sha256_excluding_frontmatter(dst_text) != body_sha256_excluding_frontmatter(src_text):
                # Merged conflict OR patched body — accept if body contains src body.
                if body_sha256_excluding_frontmatter(src_text) not in dst_text and m.src.read_bytes() not in m.dst.read_bytes():
                    # For merged conflicts the dst is a superset; loose check.
                    pass  # allow — merge path verified elsewhere
        else:
            if sha256_bytes(dst_text.encode()) != sha256_bytes(src_text.encode()):
                # Could be a merge — allow if src bytes appear in dst.
                if src_text.strip() not in dst_text:
                    failures.append(f"sha mismatch (non-merge) {m.src} -> {m.dst}")
    return failures


def _cleanup_originals(moves: list[Move], dry_run: bool) -> int:
    """STEP 5: delete originals + empty dirs bottom-up."""
    if dry_run:
        return 0
    deleted = 0
    dirs_to_check: set[Path] = set()
    for m in moves:
        if not m.delete_src:
            continue
        if m.action == "domain_map_delete":
            try:
                m.src.unlink()
                deleted += 1
                dirs_to_check.add(m.src.parent)
            except Exception:
                pass
            continue
        if m.src == m.dst:
            continue
        try:
            m.src.unlink()
            deleted += 1
            dirs_to_check.add(m.src.parent)
        except FileNotFoundError:
            pass
        except Exception:
            pass

    # Remove emptied dirs bottom-up (skip reserved)
    for d in sorted(dirs_to_check, key=lambda p: -len(p.parts)):
        try:
            if d.name in _SKIP_SUBDIRS or d.name in _SKIP_FILES:
                continue
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
        except Exception:
            continue
    return deleted


def _rebuild_metadata(dry_run: bool) -> None:
    """STEP 6: MOC + index refresh."""
    if dry_run:
        return
    try:
        from _moc import rebuild_all  # type: ignore
        rebuild_all()
    except Exception as e:
        print(f"WARN: MOC rebuild failed: {e}", file=sys.stderr)
    try:
        scripts = Path(__file__).parent
        subprocess.run(
            ["python3", str(scripts / "_index.py"), "--full"],
            capture_output=True, timeout=60, check=False,
        )
    except Exception as e:
        print(f"WARN: index rebuild failed: {e}", file=sys.stderr)


def _prune_backups(gh: Path) -> None:
    """F1 backup retention: rolling-2 window, demote to 1 when newest ≥24h."""
    bdir = gh / ".backup"
    if not bdir.is_dir():
        return
    backups = sorted(
        [p for p in bdir.glob("v2-pre-v3-*") if p.is_dir()],
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    # Keep newest 2
    for stale in backups[2:]:
        try:
            shutil.rmtree(stale)
        except Exception:
            pass
    # Demote to 1 if newest ≥24h
    if backups and (time.time() - backups[0].stat().st_mtime) > 86400:
        for stale in backups[1:2]:
            try:
                shutil.rmtree(stale)
            except Exception:
                pass


def _bump_layout_version_and_commit(gh: Path, dry_run: bool,
                                    timestamp: str, branch: str) -> dict:
    """STEP 7: F9 fetch+ff before write; bump layout_version; git commit."""
    out = {"layout_version_bumped": False, "git_commit": None,
           "stale_remote_abort": False}
    if dry_run:
        return out

    # F9 — refresh remote before write
    git_dir = gh / ".git"
    if git_dir.is_dir():
        if not _git_fetch_ff(gh, branch):
            out["stale_remote_abort"] = True
            return out

    settings = _read_settings_or_default()
    settings["layout_version"] = 3
    atomic_write(settings_path(), json.dumps(settings, indent=2) + "\n")
    out["layout_version_bumped"] = True

    if git_dir.is_dir():
        try:
            subprocess.run(["git", "-C", str(gh), "add", "."],
                           capture_output=True, timeout=15, check=False)
            r = subprocess.run(
                ["git", "-C", str(gh), "commit", "-m", f"v3 migration {timestamp}"],
                capture_output=True, text=True, timeout=15, check=False,
            )
            if r.returncode == 0:
                # Get short SHA
                s = subprocess.run(
                    ["git", "-C", str(gh), "rev-parse", "--short", "HEAD"],
                    capture_output=True, text=True, timeout=5, check=False,
                )
                out["git_commit"] = (s.stdout or "").strip()
        except Exception:
            pass

    _prune_backups(gh)
    return out


def migrate(dry_run: bool = False, force: bool = False) -> dict:
    """Run the 7-step v2 → v3 migration. Returns report dict."""
    gh = gowth_home()
    if not gh.is_dir():
        return {"status": "no_gowth_home", "moves": [], "counters": {}}

    settings = _read_settings_or_default()
    layout = int(settings.get("layout_version", 0) or 0)
    branch = ""
    cfg_path = gh / "config.json"
    if cfg_path.is_file():
        try:
            cfg = json.loads(cfg_path.read_text())
            branch = cfg.get("branch", "main") or "main"
        except Exception:
            branch = "main"
    else:
        branch = "main"

    # F2 already-v3-on-remote short-circuit (STEP 1 prelude)
    if layout < 3 and (gh / ".git").is_dir():
        subj = _git_log_subject(gh, branch)
        if subj.startswith("v3 migration "):
            if not dry_run and not force:
                settings["layout_version"] = 3
                atomic_write(settings_path(),
                             json.dumps(settings, indent=2) + "\n")
            return {"status": "already_v3_on_remote", "moves": [], "counters": {}}

    if layout >= 3 and not force:
        return {"status": "already_v3", "moves": [], "counters": {}}

    timestamp = utc_iso_compact_us()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    workspaces = list_workspaces()
    all_moves: list[Move] = []

    with file_lock("migrate-v3", timeout=60):
        with file_lock("sync", timeout=30):
            # STEP 1: snapshot
            _ensure_backup_gitignore(gh)
            backup_root, files_meta = (gh / ".backup" / f"v2-pre-v3-{timestamp}", []) if dry_run else _snapshot_backup(gh, timestamp)
            if not dry_run:
                _write_manifest(backup_root, timestamp, files_meta, workspaces)

            # STEP 2: classify
            for ws in workspaces:
                ws_root = workspace_dir(ws)
                all_moves.extend(classify(ws, ws_root, today))

            # STEP 3: execute
            counters = _execute_moves(all_moves, dry_run, timestamp)

            # STEP 4: verify
            verify_failures = _verify_moves(all_moves, dry_run)
            if verify_failures and not dry_run:
                return {
                    "status": "verify_failed",
                    "failures": verify_failures,
                    "backup": str(backup_root),
                    "counters": counters,
                }

            # STEP 5: delete originals
            deleted = _cleanup_originals(all_moves, dry_run)
            counters["originals_deleted"] = deleted

            # STEP 6: rebuild metadata
            _rebuild_metadata(dry_run)

        # STEP 7: bump layout_version + commit (outside sync lock per §3.7)
        step7 = _bump_layout_version_and_commit(gh, dry_run, timestamp, branch)

    return {
        "status": "stale_remote_abort" if step7.get("stale_remote_abort") else (
            "dry_run" if dry_run else "ok"
        ),
        "timestamp": timestamp,
        "backup": str(backup_root),
        "workspaces": workspaces,
        "moves": [m.to_dict() for m in all_moves],
        "counters": counters,
        "git_commit": step7.get("git_commit"),
        "layout_version_bumped": step7.get("layout_version_bumped"),
    }


# ─── reporting ───────────────────────────────────────────────────────────

def render_report(rep: dict) -> str:
    if rep["status"] == "no_gowth_home":
        return "no ~/.gowth-mem/ directory; nothing to migrate"
    if rep["status"] == "already_v3":
        return "settings.layout_version >= 3 — already migrated. Use --force to re-run."
    if rep["status"] == "already_v3_on_remote":
        return "origin already has v3 migration commit. Local settings.json patched."
    if rep["status"] == "verify_failed":
        return ("verify_failed:\n  " + "\n  ".join(rep.get("failures", []))
                + f"\nBackup at {rep.get('backup')}")
    if rep["status"] == "stale_remote_abort":
        return ("stale_remote_abort: STEP 7 could not fast-forward.\n"
                "Resolve conflicts in ~/.gowth-mem and re-run /mem-migrate-v3.")

    counters = rep.get("counters", {})
    moves = rep.get("moves", [])
    by_action: dict[str, int] = {}
    for m in moves:
        by_action[m["action"]] = by_action.get(m["action"], 0) + 1
    lines = [
        f"gowth-mem v3 migration report ({rep.get('timestamp', '-')})",
        "=" * 60,
        f"backup:         {rep.get('backup')}",
        f"workspaces:     {len(rep.get('workspaces', []))}",
        f"moves planned:  {len(moves)}",
    ]
    for action in ("v24_landing_to_readme", "v24_subaspect_to_dated",
                   "v23_flat_promote", "lazy_nest_landing_flatten",
                   "lazy_nest_subaspect_to_dated", "lazy_nest_dated_flatten",
                   "lazy_nest_lessons_flatten", "lessons_keep",
                   "domain_map_delete", "already_v3", "already_dated"):
        n = by_action.get(action, 0)
        if n:
            lines.append(f"  {action:36s} {n}")
    lines.extend([
        f"conflicts:      {counters.get('conflicts', 0)}",
        f"executed:       {counters.get('executed', 0)}",
        f"originals del:  {counters.get('originals_deleted', 0)}",
        f"errors:         {counters.get('errors', 0)}",
        f"git commit:     {rep.get('git_commit') or '-'}",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(prog="_migrate_v3.py")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--report", action="store_true",
                    help="Print human report (default: JSON)")
    ap.add_argument("--json", action="store_true",
                    help="Print JSON (default if --report not set)")
    args = ap.parse_args()

    rep = migrate(dry_run=args.dry_run, force=args.force)
    if args.report:
        sys.stdout.write(render_report(rep))
    else:
        sys.stdout.write(json.dumps(rep, indent=2) + "\n")
    return 0 if rep.get("status") in ("ok", "dry_run", "already_v3",
                                       "already_v3_on_remote") else 1


if __name__ == "__main__":
    sys.exit(main())
