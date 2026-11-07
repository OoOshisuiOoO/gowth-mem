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
    lock (`--pull-only` and `--pull-rebase-push`), and in its no-remote branch
    (under the sync lock too) — `/mem-install` on an installed vault runs
    `auto-sync.py --pull-only` for exactly this (pull first, then deliver);
  * `bootstrap-load.prepare()` only when the vault has no `.git` or no remote;
  * this CLI (waits for the sync lock): `/mem-install` Step 4b, after the
    first sync — never before a pull.
A pull that brings a peer's older canon therefore lands first and is then
updated in place — a tracked modification, never an add/add conflict.

COMMITTED, never left untracked (review safety-sync#1 / gaps-docs#1): auto-sync
and this CLI commit exactly what `ensure()` installed or updated
(`commit_delivered`, gowth-mem identity, `git commit --only -- <those paths>`),
and auto-sync's pull commits any canon path still untracked right before its
`stash push -u` (`commit_untracked`). An untracked copy used to ride that stash;
when a peer had pushed the same path (same plugin version is enough) the pop
failed with "already exists, no checkout" — rc 2, a stranded stash and, on git
2.33, the user's uncommitted edits to tracked files left only in it. A committed
copy rebases onto the peer's identical one as an empty commit, which git drops;
a different version surfaces as SYNC-CONFLICT.md (spec §11), never silently.

Writes go through `_atomic.safe_write` (the canon is synced markdown) under
`file_lock("canon")`; `.gitattributes` through `atomic_write` after an explicit
`_privacy.sanitize`. A vault that is not installed (no settings.json, no
shared/) gets nothing.

CLI:
  _canon.py                    # ensure the canon in the vault; prints "<dst>\\t<status>"
                               # and commits what it installed/updated (git vaults)
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


# ── commit what was delivered (review safety-sync#1 / gaps-docs#1) ────────
#
# A delivered file left UNTRACKED rides the next pull's `stash push -u`. When a
# peer pushed the same path meanwhile, the pull brings its tracked copy and
# `stash pop` fails with "already exists, no checkout" even for byte-identical
# content: rc 2, the pre-pull stash stranded, and git 2.33 (first on PATH on the
# author's Mac) restores untracked files BEFORE re-applying tracked changes, so
# the user's uncommitted edits stay only in that stash. A COMMITTED delivery
# rebases onto the peer's identical one as an empty commit, which git drops.

DELIVERED = ("installed", "updated")
DELIVER_SUBJECT = "update(shared): deliver gowth-mem canon"
TRACK_SUBJECT = "update(shared): track gowth-mem canon before the pull"
GIT_TIMEOUT = 60.0
MARKER_SCAN_MAX = 4_000_000          # the same bound auto-sync's commit guard uses
_IN_PROGRESS = ("rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD")


def canon_destinations() -> tuple:
    """Every vault path canon delivery writes: the CANON destinations, then `.gitattributes`."""
    return tuple(dst for _src, dst in CANON) + (GITATTRIBUTES,)


def _run(gh: Path, *args: str):
    from _git import run_git  # type: ignore
    return run_git(gh, *args, check=False, timeout=GIT_TIMEOUT)


def _commit_blocked(gh: Path) -> str:
    """'' when a canon commit may run now, else why not: no `.git` directory; a
    rebase / merge / cherry-pick / revert in progress; no commit yet (an unborn
    branch — the first commit is `_sync.py --init`'s, with the whole vault); or
    unmerged paths."""
    git_dir = gh / ".git"
    if not git_dir.is_dir():
        return "no .git directory"
    for name in _IN_PROGRESS:
        if (git_dir / name).exists():
            return f"{name} in progress"
    if _run(gh, "rev-parse", "--verify", "-q", "HEAD").returncode != 0:
        return "no commit yet"
    unmerged = _run(gh, "ls-files", "-u")
    if unmerged.returncode != 0 or unmerged.stdout.strip():
        return "unmerged paths"
    return ""


def _has_conflict_markers(path: Path) -> bool:
    """A raw git conflict sentinel line — exactly 7 `<` or `>`, then a space or
    the end of the line: the rule auto-sync's commit boundary refuses, checked
    without a regex. Files above MARKER_SCAN_MAX are not read (same bound)."""
    try:
        if path.stat().st_size > MARKER_SCAN_MAX:
            return False
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    for line in text.splitlines():
        if line[:7] in ("<<<<<<<", ">>>>>>>") and (len(line) == 7 or line[7] == " "):
            return True
    return False


def _through_link(gh: Path, rel: str) -> bool:
    """True when any component of `rel` under `gh` (the file itself or a parent
    directory) is a symlink — never commit through one."""
    cur = gh
    for part in Path(rel).parts:
        cur = cur / part
        if cur.is_symlink():
            return True
    return False


def _committable(gh: Path, paths: list) -> list:
    """Of `paths`, those a commit would carry — untracked and not ignored, or
    different from HEAD — that are regular files reached without a symlink and
    free of raw conflict markers. Order kept."""
    found: set = set()
    r = _run(gh, "ls-files", "--others", "--exclude-standard", "-z", "--", *paths)
    if r.returncode == 0:
        found.update(p for p in r.stdout.split("\0") if p)
    r = _run(gh, "diff", "--name-only", "-z", "HEAD", "--", *paths)
    if r.returncode == 0:
        found.update(p for p in r.stdout.split("\0") if p)
    out: list = []
    for rel in paths:
        if rel not in found:
            continue
        f = gh / rel
        if _through_link(gh, rel) or not f.is_file():
            log_debug("canon", f"not committed: {rel} is not a regular file")
            continue
        if _has_conflict_markers(f):
            log_debug("canon", f"not committed: {rel} holds raw conflict markers")
            continue
        out.append(rel)
    return out


def _message(subject: str, paths: list, statuses: dict, host: str) -> str:
    n = len(paths)
    lines = [f"{subject} ({n} file{'' if n == 1 else 's'})", ""]
    lines += [f"- {p}: {statuses.get(p, 'changed')}" for p in paths]
    lines += ["", f"Files: {n}", f"Machine: {host}", "Context: canon"]
    return "\n".join(lines) + "\n"


def commit_paths(gh: "Path | str", paths, host: "str | None" = None, *,
                 subject: str = DELIVER_SUBJECT, statuses: "dict | None" = None) -> list:
    """Stage and commit EXACTLY `paths` — canon destinations only, any other path
    is dropped — as one commit with the gowth-mem identity: `git add -- <paths>`,
    then `git commit --only -- <paths>`, so whatever else is staged or dirty
    stays out of it. Runs only when `_commit_blocked()` allows; skips a path that
    is unchanged, ignored, not a regular file or holds raw conflict markers.
    Returns the committed paths ([] when nothing was committed). Never raises;
    a failure is logged via log_debug."""
    try:
        gh = Path(gh)
        allowed = set(canon_destinations())
        want = [p for p in dict.fromkeys(paths or ()) if isinstance(p, str) and p in allowed]
        if not want:
            return []
        why = _commit_blocked(gh)
        if why:
            log_debug("canon", f"canon commit skipped ({why}): {', '.join(want)}")
            return []
        todo = _committable(gh, want)
        if not todo:
            return []
        added = _run(gh, "add", "--", *todo)
        if added.returncode != 0:
            log_debug("canon", f"git add failed: {(added.stderr or added.stdout or '').strip()[:300]}")
            return []
        from _git import identity_args, identity_host  # type: ignore
        host = host or identity_host()
        msg = _message(subject, todo, statuses or {}, host)
        done = _run(gh, *identity_args(host), "commit", "-q", "--only", "-m", msg, "--", *todo)
        if done.returncode != 0:
            log_debug("canon", f"git commit failed: {(done.stderr or done.stdout or '').strip()[:300]}")
            return []
        log_debug("canon", f"committed {', '.join(todo)}")
        return todo
    except Exception as exc:
        log_debug("canon", f"canon commit failed: {exc}")
        return []


def commit_delivered(gh: "Path | str", results, host: "str | None" = None) -> list:
    """Commit exactly the destinations `ensure()` reported `installed` or
    `updated` (incl. `.gitattributes` when it changed) — a `current` or
    `user-modified` file is never committed by this helper. The caller holds the
    sync lock. See commit_paths()."""
    statuses: dict = {}
    for item in results or ():
        try:
            dst, status = item
        except (TypeError, ValueError):
            continue
        if status in DELIVERED and isinstance(dst, str):
            statuses.setdefault(dst, status)
    if not statuses:
        return []
    return commit_paths(gh, list(statuses), host, statuses=statuses)


def untracked(gh: "Path | str") -> list:
    """The canon destinations git reports as untracked and not ignored (CANON
    order); [] when the vault is not a git repository. Never raises."""
    try:
        gh = Path(gh)
        if not (gh / ".git").is_dir():
            return []
        dsts = canon_destinations()
        r = _run(gh, "ls-files", "--others", "--exclude-standard", "-z", "--", *dsts)
        if r.returncode != 0:
            return []
        found = {p for p in r.stdout.split("\0") if p}
        return [p for p in dsts if p in found]
    except Exception as exc:
        log_debug("canon", f"untracked canon check failed: {exc}")
        return []


def commit_untracked(gh: "Path | str", host: "str | None" = None) -> list:
    """Defence in depth for a pull (auto-sync, right before `stash push -u`):
    commit every canon destination that is still untracked — left by an earlier
    build that did not commit, by bootstrap-load before a remote existed, or
    copied by hand — so the stash never carries one. See commit_paths()."""
    paths = untracked(gh)
    if not paths:
        return []
    return commit_paths(gh, paths, host, subject=TRACK_SUBJECT,
                        statuses={p: "untracked" for p in paths})


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
    # sync → canon, as in auto-sync). What it delivers is committed under the
    # same lock (review safety-sync#1), never left untracked for the next pull.
    try:
        with file_lock("sync", timeout=CLI_SYNC_TIMEOUT):
            results = ensure(gh)
            committed = commit_delivered(gh, results)
    except TimeoutError:
        print("a sync is in progress (sync lock held) — run _canon.py again in a moment", file=sys.stderr)
        return 1
    for dst, status in results:
        print(f"{dst}\t{status}")
    if committed:
        print(f"committed {len(committed)} delivered file(s) to the vault's local git history "
              f"({DELIVER_SUBJECT}); a configured remote gets it with the next full sync",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
