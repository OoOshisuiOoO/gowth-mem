#!/usr/bin/env python3
"""Canon delivery (v4.9): keep the plugin-shipped rules + research canon current
in the vault, and the gap ledger's `.gitattributes` union line in place.

CANON maps a plugin template to its vault path. `ensure()` per file:

  missing                                   → copy            `installed`
  identical to the shipped template         → leave           `current`
  sha1 ∈ a PREVIOUSLY shipped version       → overwrite       `updated`
  anything else (the user edited it)        → leave           `user-modified`

It never deletes. Shipped versions live in `templates/canon-hashes.json`
({vault path: [sha1, …]} of every historical version of the template in this
repo's git history plus the current one; `--rebuild-hashes` regenerates it).
Because an OLDER plugin does not know a NEWER version's hash, a machine still on
an old release leaves a newer canon alone — no ping-pong between peers.

A template that is not shipped in this build is skipped silently.

Also ensured (append-only, idempotent): `.gitattributes` carries
`workspaces/*/docs/questions.md merge=union`, so two machines appending to a
gap ledger at once merge line-wise instead of writing SYNC-CONFLICT.md.

WHERE it runs (sync-safe — never concurrently with a pull):
  * `auto-sync.py` right after `pull_rebase()` returns 0, still under the sync
    lock (`--pull-only` and `--pull-rebase-push`), and in its no-remote branch;
  * `bootstrap-load.prepare()` only when the vault has no `.git` or no remote;
  * `/mem-install` (this CLI, which waits for the sync lock) after the first sync.
A pull that brings a peer's older canon therefore lands first and is then
updated in place — a tracked modification, never an add/add conflict.

Writes go through `_atomic.safe_write` (the canon is synced markdown) under
`file_lock("canon")`; `.gitattributes` through `atomic_write` after an explicit
`_privacy.sanitize`. A vault that is not installed (no settings.json, no
shared/) gets nothing.

CLI:
  _canon.py                    # ensure the canon in the vault; prints "<dst>\\t<status>"
  _canon.py --rebuild-hashes   # regenerate templates/canon-hashes.json (git log --follow)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import atomic_write, safe_write  # type: ignore
from _debug import log_debug  # type: ignore
from _home import gowth_home  # type: ignore
from _lock import file_lock  # type: ignore

# (template path relative to the plugin root, vault path relative to the vault)
CANON = (
    ("templates/dot-gowth-mem/shared/memory-rules.md", "shared/memory-rules.md"),
    ("templates/dot-gowth-mem/shared/research/data-quality-2026.md",
     "shared/research/data-quality-2026.md"),
    ("templates/dot-gowth-mem/shared/research/extraction-reuse-2026.md",
     "shared/research/extraction-reuse-2026.md"),
    ("templates/dot-gowth-mem/shared/research/provenance-2026.md",
     "shared/research/provenance-2026.md"),
)
HASHES_REL = "templates/canon-hashes.json"
GITATTRIBUTES = ".gitattributes"
UNION_LINE = "workspaces/*/docs/questions.md merge=union"
_UNION_COMMENT = "# gowth-mem v4.9: two machines appending to a gap ledger merge line-wise"
LOCK_TIMEOUT = 5.0
CLI_SYNC_TIMEOUT = 30.0      # the CLI waits this long for a running sync


def plugin_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _sanitized(text: str) -> str:
    """What `safe_write` would store for `text` (the canon is synced markdown)."""
    try:
        from _privacy import sanitize  # type: ignore
        out, n = sanitize(text)
        return out if n >= 0 and isinstance(out, str) else text
    except Exception:
        return text


def load_hashes(root: "Path | None" = None) -> dict:
    """{vault path: set(sha1)} of every shipped version; {} when unreadable."""
    p = (root or plugin_root()) / HASHES_REL
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict = {}
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(k, str) and isinstance(v, list):
                out[k] = {h for h in v if isinstance(h, str)}
    return out


def _installed(vault: Path) -> bool:
    return vault.is_dir() and ((vault / "settings.json").is_file() or (vault / "shared").is_dir())


def _inside(path: Path, vault: Path) -> bool:
    try:
        path.resolve().relative_to(vault.resolve())
        return True
    except (ValueError, OSError):
        return False


def _ensure_file(vault: Path, root: Path, src_rel: str, dst_rel: str, known: set) -> "str | None":
    src = root / src_rel
    if not src.is_file():
        return None                                   # not shipped in this build
    try:
        content = _sanitized(src.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        log_debug("canon", f"unreadable template {src}: {exc}")
        return None
    want = content.encode("utf-8")
    dst = vault / dst_rel
    if dst.is_symlink() or (dst.exists() and not dst.is_file()):
        return "user-modified"                        # never write through a link
    anc = dst.parent                                  # nearest existing ancestor
    while not anc.exists() and anc != vault and anc != anc.parent:
        anc = anc.parent
    if not _inside(anc, vault):
        return "user-modified"                        # a symlinked shared/ points elsewhere
    if not dst.exists():
        safe_write(dst, content)
        return "installed"
    try:
        have = dst.read_bytes()
    except OSError:
        return "user-modified"
    if have == want:
        return "current"
    if _sha1(have) in known:
        safe_write(dst, content)
        return "updated"
    return "user-modified"


def _union_state(text: str) -> str:
    """'present' (the pattern carries merge=union), 'overridden' (the user set
    another merge attribute for it — respected), or 'absent'."""
    pattern, attr = UNION_LINE.split()
    for raw in text.splitlines():
        toks = raw.split()
        if not toks or toks[0].startswith("#") or toks[0] != pattern:
            continue
        attrs = toks[1:]
        if attr in attrs:
            return "present"
        if any(a in ("-merge", "!merge") or a.startswith("merge=") for a in attrs):
            return "overridden"
    return "absent"


def _ensure_gitattributes(vault: Path) -> str:
    ga = vault / GITATTRIBUTES
    if ga.is_symlink() or (ga.exists() and not ga.is_file()):
        return "user-modified"
    if not ga.exists():
        body = f"{_UNION_COMMENT}\n{UNION_LINE}\n"
        status = "installed"
    else:
        try:
            existing = ga.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "user-modified"
        state = _union_state(existing)
        if state == "present":
            return "current"
        if state == "overridden":
            return "user-modified"
        sep = "" if not existing or existing.endswith("\n") else "\n"
        body = f"{existing}{sep}{_UNION_COMMENT}\n{UNION_LINE}\n"
        status = "updated"
    try:
        from _privacy import sanitize  # type: ignore
        clean, n = sanitize(body)
        if n >= 0 and isinstance(clean, str):
            body = clean
    except Exception:
        pass
    atomic_write(ga, body)
    return status


def _ensure_unlocked(vault: Path, root: Path) -> list:
    hashes = load_hashes(root)
    out: list = []
    for src_rel, dst_rel in CANON:
        try:
            status = _ensure_file(vault, root, src_rel, dst_rel, hashes.get(dst_rel, set()))
        except Exception as exc:
            log_debug("canon", f"{dst_rel}: {exc}")
            continue
        if status:
            out.append((dst_rel, status))
    try:
        out.append((GITATTRIBUTES, _ensure_gitattributes(vault)))
    except Exception as exc:
        log_debug("canon", f".gitattributes: {exc}")
    return out


def ensure(vault: "Path | str | None" = None, *, root: "Path | str | None" = None,
           lock_timeout: float = LOCK_TIMEOUT) -> list:
    """Install / update the shipped canon in `vault` (default: the gowth-mem
    home). Returns [(vault path, status)]; [] when the vault is not installed
    or the canon lock is held elsewhere. Never deletes; never raises for a
    single file (that file is skipped and logged)."""
    vault = Path(vault) if vault is not None else gowth_home()
    root = Path(root) if root is not None else plugin_root()
    if not _installed(vault):
        return []
    try:
        same_home = vault.resolve() == gowth_home().resolve()
    except OSError:
        same_home = False
    if not same_home:
        # the lock lives in the HOME's .locks/ — never create one for a vault
        # this process does not own
        return _ensure_unlocked(vault, root)
    try:
        with file_lock("canon", timeout=lock_timeout):
            return _ensure_unlocked(vault, root)
    except TimeoutError:
        log_debug("canon", "canon lock held elsewhere — delivery skipped this time")
        return []


# ── shipped-version hashes ────────────────────────────────────────────────

def _history_blobs(root: Path, rel: str) -> list:
    """Every committed version of `rel` (rename-aware), oldest first."""
    try:
        r = subprocess.run(["git", "-C", str(root), "log", "--follow", "--format=%H",
                            "--name-only", "--", rel],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if r.returncode != 0:
        return []
    pairs: list = []
    sha = ""
    for line in r.stdout.splitlines():
        s = line.strip()
        if not s:
            continue
        if len(s) == 40 and all(c in "0123456789abcdef" for c in s):
            sha = s
        elif sha:
            pairs.append((sha, s))
            sha = ""
    blobs: list = []
    for sha, path in reversed(pairs):
        try:
            b = subprocess.run(["git", "-C", str(root), "show", f"{sha}:{path}"],
                               capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if b.returncode == 0:
            blobs.append(b.stdout)
    return blobs


def rebuild_hashes(root: "Path | None" = None, *, write: bool = True) -> dict:
    """{vault path: [sha1, …]} over every committed version of each template
    plus the working-tree one (and the form safe_write stores, when the
    sanitizer would change it). Written to templates/canon-hashes.json."""
    root = root or plugin_root()
    data: dict = {}
    for src_rel, dst_rel in CANON:
        versions = _history_blobs(root, src_rel)
        cur = root / src_rel
        if cur.is_file():
            versions.append(cur.read_bytes())
        hashes: list = []
        for blob in versions:
            forms = [blob]
            try:
                forms.append(_sanitized(blob.decode("utf-8")).encode("utf-8"))
            except UnicodeDecodeError:
                pass
            for form in forms:
                h = _sha1(form)
                if h not in hashes:
                    hashes.append(h)
        if hashes:
            data[dst_rel] = hashes
    if write:
        atomic_write(root / HASHES_REL, json.dumps(data, indent=2, sort_keys=True) + "\n")
    return data


def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="_canon.py", description=__doc__.splitlines()[0])
    ap.add_argument("--rebuild-hashes", action="store_true",
                    help="regenerate templates/canon-hashes.json from this repo's git history")
    args = ap.parse_args(argv)
    if args.rebuild_hashes:
        data = rebuild_hashes()
        for dst in sorted(data):
            print(f"{dst}\t{len(data[dst])} version hash(es)")
        if not data:
            print("no canon templates found", file=sys.stderr)
            return 1
        return 0
    gh = gowth_home()
    if not _installed(gh):
        print(f"{gh} is not an installed vault — run /mem-install first", file=sys.stderr)
        return 1
    # the sync lock: a hook-spawned pull may be running beside an interactive
    # /mem-install — wait for it rather than write next to it (lock order
    # sync → canon, as in auto-sync)
    try:
        with file_lock("sync", timeout=CLI_SYNC_TIMEOUT):
            results = ensure(gh)
    except TimeoutError:
        print("a sync is in progress (sync lock held) — run _canon.py again in a moment", file=sys.stderr)
        return 1
    for dst, status in results:
        print(f"{dst}\t{status}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
