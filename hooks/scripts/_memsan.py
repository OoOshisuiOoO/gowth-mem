#!/usr/bin/env python3
"""v4.8 (review C3): privacy pass over Claude Code's auto-memory files.

`autoMemoryDirectory` points Claude Code at `<ws>/memory/`, so Claude writes
its own notes there directly — no gate, no `safe_write` — and the directory
syncs to the git remote. Every path that can publish the vault therefore runs
`sanitize_memory_files()` first: the Stop hook (before the detached autosync
is spawned) and every commit path (`auto-sync.py` commit_local for Stop /
PreCompact / PostCompact, `_sync.py` for /mem-sync), before `git add -A`.

Every workspace is scanned on every call (the v4.8 first cut scanned only the
Stop's own workspace under ONE global stamp, so a file written in another
workspace was skipped forever). Cost is bounded by a per-file content hash in
state.json (`memory_sanitized`: {relpath: sha1}) — an unchanged file is
neither re-scanned by the regexes nor rewritten.

v4.9 (review safety-sync#2): `sanitize_before_commit()` is what the commit
boundaries call — auto-sync `commit_local`, both `_sync.py` commit sites,
`_conflict`'s mid-rebase fold and `_conflict.py --continue`. It runs the
memory pass above, then `sanitize_changed_files()` over every OTHER changed
synced markdown file (`git status --porcelain -z --untracked-files=all --
workspaces shared`, .md/.markdown — what `safe_write` sanitizes).
docs/handoff.md and topic files are edited directly (the memory teammate,
/mem-save, the gap flow: the Edit tool, no `safe_write`); the v4.8 prune
rewrote nearly every such file through `safe_write` by accident, the v4.9
prune writes only what it changed, so a pasted token reached the remote raw.

A clean file (the normal case) is read once and neither locked nor written:
bytes and mtime unchanged. A file the sanitizer would change is re-read and
rewritten under `entries-<ws>` plus the locks of its other writers
(`lock_names_for`; `SHARED_LOCK` under shared/), and only if it still holds
exactly what was read. A file that holds a secret but cannot be rewritten
(a lock held, undecodable, a write failure, a sanitizer bypass) is reported in
`skipped`, and `keep_out_of_commit()` unstages it after `git add -A` — on an
unborn branch too — and reports any it could not unstage, so the caller
commits nothing (fail closed). No state, no new regex: the work is bounded by
the changed-file list.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import atomic_write  # type: ignore
from _debug import log_debug  # type: ignore
from _home import RESERVED_SUBDIRS, gowth_home, list_workspaces, state_path, workspace_dir  # type: ignore
from _lock import file_lock  # type: ignore

STATE_KEY = "memory_sanitized"
MEMFILE_NAME = "MEMORY.md"

# The changed-file pass (review safety-sync#2).
SYNCED_DIRS = ("workspaces", "shared")
MARKDOWN_SUFFIXES = (".md", ".markdown")       # what `_atomic.safe_write` sanitizes
# shared/ files: the ONE lock every read-modify-write under shared/ takes
# (`_topic.SHARED_ENTRIES_LOCK`, review safety-sync#3) — a name no workspace
# lock can produce. Kept as a literal so this module never imports _topic.
SHARED_LOCK = "shared-entries"
CHANGED_LOCK_TIMEOUT = 2.0
CAS_ATTEMPTS = 3                                # re-reads when a writer outside our locks races us
GIT_TIMEOUT = 60.0


def memory_files() -> list:
    """[(ws, path)] for every *.md under every workspace's memory/ dir."""
    out = []
    try:
        wss = list_workspaces()
    except Exception:
        wss = []
    for ws in sorted(wss):
        d = workspace_dir(ws) / "memory"
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*.md")):
            if p.is_file():
                out.append((ws, p))
    return out


def _load_state() -> dict:
    p = state_path()
    try:
        data = json.loads(p.read_text()) if p.is_file() else {}
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


def _known() -> dict:
    k = _load_state().get(STATE_KEY)
    return dict(k) if isinstance(k, dict) else {}


def _remember(known: dict) -> None:
    try:
        with file_lock("state", timeout=2.0):
            state = _load_state()
            state[STATE_KEY] = known
            atomic_write(state_path(), json.dumps(state, indent=1))
    except Exception as exc:
        log_debug("memsan", f"state write skipped: {exc}")


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "surrogateescape")).hexdigest()


def _sanitize_one(p: Path, rel: str, known: dict, fresh: dict, report: dict, sanitize) -> "str | None":
    """Sanitize one file in place. Returns the hash to record, or None when the
    file must be looked at again next pass."""
    text = p.read_text(errors="ignore")
    h = _sha1(text)
    if known.get(rel) == h:
        return h
    cleaned, n = sanitize(text)
    if n > 0 and isinstance(cleaned, str) and cleaned != text:
        atomic_write(p, cleaned)
        report["sanitized"].append(rel)
        report["secrets"] += int(n)
        log_debug("memsan", f"sanitized {n} secret(s) in {rel}")
        return _sha1(cleaned)
    return h


def _flag_skipped(p: Path, rel: str, report: dict, sanitize, why: str = "memfile lock held") -> None:
    """The file could not be sanitized in place: report it as skipped WHEN it
    holds a secret, so the commit paths can keep it out of the commit (fail
    closed). A sanitizer bypass (n < 0) or an unreadable file counts as one."""
    try:
        _cleaned, n = sanitize(p.read_text(errors="ignore"))
    except Exception:
        n = 1
    if n != 0:
        report["skipped"].append(rel)
        log_debug("memsan", f"skipped {rel}: {why}; kept out of the next commit")


def sanitize_memory_files(lock_timeout: float = 2.0) -> dict:
    """Sanitize every changed memory file in every workspace.

    Returns {"scanned": n, "sanitized": [relpaths], "secrets": n, "skipped":
    [relpaths]}. A file whose content hash matches the recorded one is not
    re-scanned. MEMORY.md is read and written under the workspace's memfile
    lock (`_memfile.write` holds it); on timeout a file that holds a secret is
    reported in `skipped` — the commit paths keep it out of the commit — and
    is retried next pass (its hash is not recorded). Never raises."""
    report = {"scanned": 0, "sanitized": [], "secrets": 0, "skipped": []}
    try:
        from _privacy import sanitize  # type: ignore
    except Exception as exc:
        log_debug("memsan", f"privacy import failed: {exc}")
        return report
    gh = gowth_home()
    known = _known()
    fresh: dict = {}
    for ws, p in memory_files():
        try:
            rel = p.relative_to(gh).as_posix()
        except ValueError:
            rel = str(p)
        report["scanned"] += 1
        try:
            if p.name == MEMFILE_NAME:
                # read AND write under the memfile lock (review m2): a concurrent
                # _memfile.write must never be overwritten with stale text
                try:
                    with file_lock(f"memfile-{ws}", timeout=lock_timeout):
                        h = _sanitize_one(p, rel, known, fresh, report, sanitize)
                except TimeoutError:
                    h = None
                    _flag_skipped(p, rel, report, sanitize)
            else:
                h = _sanitize_one(p, rel, known, fresh, report, sanitize)
        except Exception as exc:
            log_debug("memsan", f"sanitize failed for {rel}: {exc}")
            h = None
            _flag_skipped(p, rel, report, sanitize)   # review R6: any failure fails closed
        if h is not None:
            fresh[rel] = h
    if fresh != known:
        _remember(fresh)
    return report


# ── the changed-file pass (review safety-sync#2) ─────────────────────────


def _git_bytes(gh: Path, *args: str) -> "subprocess.CompletedProcess | None":
    """`git -C <vault> <args>` with BYTES output (no quoting, no lossy
    decode). None when git cannot run at all."""
    try:
        return subprocess.run(["git", "-C", str(gh), *args], capture_output=True, timeout=GIT_TIMEOUT)
    except Exception as exc:
        log_debug("memsan", f"git {' '.join(args[:2])} failed: {exc}")
        return None


def _changed_paths(gh: Path) -> "list | None":
    """Vault-relative paths of every changed file under workspaces/ and
    shared/ — staged, unstaged or untracked (`--untracked-files=all` lists the
    files inside a new directory; ignored files are never listed). Under -z a
    rename/copy entry is `XY <to>NUL<from>NUL`: the source is skipped. None
    when the vault has no `.git` of its own (never parse an enclosing repo's
    status) or git cannot list the changes."""
    if not (gh / ".git").exists():
        return None
    r = _git_bytes(gh, "status", "--porcelain", "-z", "--untracked-files=all", "--", *SYNCED_DIRS)
    if r is None or r.returncode != 0:
        if r is not None:
            log_debug("memsan", f"git status failed: {r.stderr[:200]!r}")
        return None
    out: list = []
    tokens = r.stdout.split(b"\0")
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        i += 1
        if len(tok) < 4:
            continue                         # the empty token after the last NUL
        if b"R" in tok[:2] or b"C" in tok[:2]:
            i += 1                           # skip the rename/copy source path
        out.append(os.fsdecode(tok[3:]))
    return out


def _walk_synced(gh: Path) -> list:
    """Fallback when git cannot list the changes: every file under
    workspaces/ and shared/ (fail closed — scan everything that could be
    committed; bounded by the vault). Never follows a symlinked directory."""
    out: list = []
    for top in SYNCED_DIRS:
        root = gh / top
        if root.is_symlink() or not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for name in sorted(filenames):
                try:
                    out.append((Path(dirpath) / name).relative_to(gh).as_posix())
                except ValueError:
                    continue
    return out


def _is_candidate(rel: str) -> bool:
    """A synced markdown file — exactly what `_atomic.safe_write` sanitizes."""
    parts = rel.split("/")
    return (len(parts) >= 2 and parts[0] in SYNCED_DIRS
            and Path(rel).suffix.lower() in MARKDOWN_SUFFIXES)


def lock_names_for(rel: str) -> tuple:
    """The locks a changed file is rewritten under, outermost first.

    `workspaces/<ws>/…` → `entries-<ws>` (spec §2.6: the lock every
    read-modify-write of a workspace topic/docs file holds), plus the locks
    of the file's other read-modify-write writers: the gap ledger
    (`gaps-<ws>`), handoff rotation (`handoff-<ws>`), session capture
    (`capture-<ws>`), the journal raw-dump and journal forget
    (`journal-<ws>`), aspect archiving and its lessons salvage
    (`topic-forget-<ws>`, any file inside a topic folder). Anything else
    (shared/, the workspace registry) → `SHARED_LOCK`. A MOC file
    (`00-README.md`, `_MAP.md`) also takes `moc`, last: MOC rebuilds and
    cards hold it. None of those writers takes `entries-<ws>` (or an earlier
    lock in this order) while holding its own, so the order never inverts.
    A workspace's MEMORY.md takes only `memfile-<ws>` (in a listed
    workspace the memory pass owns it)."""
    parts = rel.split("/")
    names: list = []
    if len(parts) >= 3 and parts[0] == "workspaces" and parts[1]:
        ws, sub = parts[1], parts[2:]
        if sub == ["memory", MEMFILE_NAME]:
            return (f"memfile-{ws}",)
        names.append(f"entries-{ws}")
        if sub == ["docs", "questions.md"]:
            names.append(f"gaps-{ws}")
        elif sub == ["docs", "handoff.md"]:
            names.append(f"handoff-{ws}")
        elif sub[0] == "journal":
            if sub[1:2] == ["sessions"]:
                names.append(f"capture-{ws}")
            names.append(f"journal-{ws}")
        elif len(sub) >= 2 and sub[0] not in RESERVED_SUBDIRS:
            names.append(f"topic-forget-{ws}")
    else:
        names.append(SHARED_LOCK)
    if parts[-1] in ("00-README.md", "_MAP.md"):
        names.append("moc")
    return tuple(names)


class LockBusy(TimeoutError):
    """One of a file's locks could not be taken in time (`.name` says which)."""

    def __init__(self, name: str, detail: str = ""):
        super().__init__(detail or f"lock '{name}' held")
        self.name = name


@contextmanager
def _held(names: tuple, timeout_for):
    """Take every lock in `names` in order (`timeout_for(name)` seconds
    each); release them in reverse. Raises LockBusy naming the one that
    could not be taken."""
    taken: list = []
    try:
        for name in names:
            cm = file_lock(name, timeout=timeout_for(name))
            try:
                cm.__enter__()
            except TimeoutError as exc:
                raise LockBusy(name, str(exc)) from exc
            taken.append(cm)
        yield
    finally:
        for cm in reversed(taken):
            cm.__exit__(None, None, None)


def _rewrite_locked(p: Path, rel: str, report: dict, sanitize) -> None:
    """Under the file's locks: re-read, redact, and write ONLY if the file
    still holds exactly what was read (a writer outside these locks — the
    Edit tool, a writer holding another lock — means a re-read, up to
    CAS_ATTEMPTS times). Raises when a file that holds a secret cannot be
    rewritten (gone, undecodable, sanitizer bypassed, still changing); the
    caller then keeps it out of the commit."""
    for _attempt in range(CAS_ATTEMPTS):
        raw = p.read_bytes()
        text = raw.decode("utf-8")           # strict: a lossy rewrite would drop bytes
        cleaned, n = sanitize(text)
        if n == 0:
            return                           # cleaned meanwhile: nothing to write
        if n < 0 or not isinstance(cleaned, str):
            raise RuntimeError("the sanitizer was bypassed")
        if cleaned == text:
            return
        if p.read_bytes() != raw:
            continue                         # changed under us: read it again
        atomic_write(p, cleaned)
        report["sanitized"].append(rel)
        report["secrets"] += int(n)
        log_debug("memsan", f"sanitized {n} secret(s) in {rel}")
        return
    raise RuntimeError(f"{rel} kept changing while it was sanitized")


def sanitize_changed_files(lock_timeout: float = CHANGED_LOCK_TIMEOUT, exclude=None) -> dict:
    """Sanitize every changed .md/.markdown file under workspaces/ and shared/
    except the paths in `exclude` (the memory pass owns those).

    Same report shape as `sanitize_memory_files`. Each file is read and
    checked WITHOUT a lock; a clean file (the normal case) is never locked
    nor written — bytes and mtime unchanged. A file the sanitizer would
    change is rewritten under `lock_names_for(rel)`; when it holds a secret
    but cannot be rewritten (a lock held past `lock_timeout`, undecodable,
    a write failure, a sanitizer bypass) it is reported in `skipped` — the
    commit paths keep it out of the commit (fail closed). A lock that timed
    out is tried once, without waiting, for the rest of the pass. Never
    raises."""
    report = {"scanned": 0, "sanitized": [], "secrets": 0, "skipped": []}
    try:
        gh = gowth_home()
        if not gh.is_dir():
            return report
        paths = _changed_paths(gh)
        if paths is None:
            paths = _walk_synced(gh)
        owned = set(exclude or ())
        candidates = []
        for rel in paths:
            if rel in owned or not _is_candidate(rel):
                continue
            p = gh / rel
            if p.is_symlink() or not p.is_file():
                continue                     # git commits a link's target path, never its content
            candidates.append((rel, p))
        try:
            from _privacy import sanitize  # type: ignore
        except Exception as exc:
            log_debug("memsan", f"privacy import failed: {exc}")
            report["skipped"] = [rel for rel, _p in candidates]      # cannot verify: fail closed
            return report
        busy: set = set()
        for rel, p in candidates:
            report["scanned"] += 1
            try:
                _cleaned, n = sanitize(p.read_bytes().decode("utf-8"))
            except Exception as exc:          # unreadable / not UTF-8: cannot be rewritten
                _flag_skipped(p, rel, report, sanitize, why=f"unreadable ({exc.__class__.__name__})")
                continue
            if n == 0:
                continue                     # clean: no lock, no write
            if n < 0:
                _flag_skipped(p, rel, report, sanitize, why="sanitizer bypassed")
                continue
            try:
                with _held(lock_names_for(rel), lambda name: 0.0 if name in busy else lock_timeout):
                    _rewrite_locked(p, rel, report, sanitize)
            except LockBusy as exc:
                busy.add(exc.name)
                _flag_skipped(p, rel, report, sanitize, why=f"lock {exc.name} held")
            except Exception as exc:
                log_debug("memsan", f"sanitize failed for {rel}: {exc}")
                _flag_skipped(p, rel, report, sanitize, why=f"rewrite failed ({exc})")
    except Exception as exc:
        log_debug("memsan", f"changed-file pass failed: {exc}")
    return report


def memory_relpaths() -> set:
    """The vault-relative paths `sanitize_memory_files` owns."""
    gh = gowth_home()
    out = set()
    for _ws, p in memory_files():
        try:
            out.add(p.relative_to(gh).as_posix())
        except ValueError:
            continue
    return out


def sanitize_before_commit(lock_timeout: float = 2.0) -> dict:
    """The commit-boundary pass: every changed memory file (memfile lock for
    MEMORY.md), then every other changed synced markdown file. Called before
    `git add` by auto-sync `commit_local`, both `_sync.py` commit sites and
    `_conflict` (the mid-rebase fold and `--continue`). Returns the merged
    report; callers keep `skipped` out of the commit. Never raises."""
    merged = {"scanned": 0, "sanitized": [], "secrets": 0, "skipped": []}
    try:
        owned = memory_relpaths()
    except Exception:
        owned = set()
    for part in (sanitize_memory_files(lock_timeout), sanitize_changed_files(lock_timeout, exclude=owned)):
        merged["scanned"] += part["scanned"]
        merged["sanitized"] += part["sanitized"]
        merged["secrets"] += part["secrets"]
        merged["skipped"] += part["skipped"]
    return merged


def staged_paths(gh: Path) -> "set | None":
    """Vault-relative paths staged for the next commit (`git diff --cached
    --name-only -z`, bytes). None when git cannot list them."""
    r = _git_bytes(gh, "diff", "--cached", "--name-only", "-z")
    if r is None or r.returncode != 0:
        return None
    return {os.fsdecode(t) for t in r.stdout.split(b"\0") if t}


def keep_out_of_commit(gh: Path, rels: list) -> list:
    """Unstage `rels` after `git add -A` so they stay out of the next commit.

    Returns the ones STILL staged afterwards (all of them when that cannot be
    verified): the caller must then commit nothing. `restore --staged` needs a
    HEAD — on an unborn branch (a vault's first commit) it fails, so `rm
    --cached` unstages instead (there, every staged file is new). Pathspecs
    are literal: a file name is never read as a glob or pathspec magic."""
    if not rels:
        return []
    head = _git_bytes(gh, "rev-parse", "--verify", "-q", "HEAD")
    has_head = head is not None and head.returncode == 0
    for rel in rels:
        if has_head:
            _git_bytes(gh, "--literal-pathspecs", "restore", "--staged", "--", rel)
        else:
            _git_bytes(gh, "--literal-pathspecs", "rm", "-q", "--cached", "--ignore-unmatch", "--", rel)
    staged = staged_paths(gh)
    if staged is None:
        return list(rels)
    return [rel for rel in rels if rel in staged]


if __name__ == "__main__":
    r = sanitize_before_commit()
    print(f"memsan: scanned {r['scanned']} file(s), sanitized {len(r['sanitized'])}, "
          f"{r['secrets']} secret(s)")
    for rel in r["sanitized"]:
        print(f"  {rel}")
    for rel in r["skipped"]:
        print(f"  skipped (holds a secret, not rewritten): {rel}")
    # non-zero when a file still holds a secret, so `_memsan.py && git add -A` stops
    sys.exit(1 if r["skipped"] else 0)
