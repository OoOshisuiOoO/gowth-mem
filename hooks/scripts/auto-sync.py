#!/usr/bin/env python3
"""Auto git sync for ~/.gowth-mem/. Hook-friendly, lock-protected.

Modes:
  --pull-only         SessionStart: rebase remote into local, no push
  --commit-only       PreCompact: stage + commit, no network
  --pull-rebase-push  PostCompact: full sync; on conflict invoke _conflict.py
  --quiet             suppress non-error output (for hooks)

If ~/.gowth-mem/config.json is missing 'remote', script exits 0 silently in
hook contexts (user hasn't run /mem-install yet — don't spam logs).

All git ops run under file_lock('sync') with a short 5s timeout. If a
parallel session holds the lock longer, this run skips with a warning rather
than blocking the hook.
"""
from __future__ import annotations

import argparse
import os
import re
import socket
import subprocess
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
from _commitmsg import build_message  # type: ignore
from _debug import log_debug  # type: ignore
from _git import auth_url, git_cmd, load_config, run_git  # type: ignore  # noqa: F401 (git_cmd re-exported for tests)
from _home import conflict_md, gowth_home  # type: ignore
from _lock import file_lock  # type: ignore



def log(msg: str, *, quiet: bool, err: bool = False) -> None:
    if quiet and not err:
        return
    print(msg, file=(sys.stderr if err else sys.stdout))


def ensure_repo(gh: Path, remote: str, branch: str, token: Optional[str], quiet: bool) -> bool:
    """Init repo if missing. Returns True on success."""
    if (gh / ".git").is_dir():
        # Update remote URL (token may have rotated)
        try:
            run_git(gh, "remote", "set-url", "origin", auth_url(remote, token))
        except subprocess.CalledProcessError:
            run_git(gh, "remote", "add", "origin", auth_url(remote, token), check=False)
        return True
    try:
        run_git(gh, "init", "-b", branch)
        run_git(gh, "remote", "add", "origin", auth_url(remote, token))
        log(f"sync: initialized .git on {branch}", quiet=quiet)
    except subprocess.CalledProcessError as e:
        log(f"sync: init failed: {e.stderr.strip()[:200]}", quiet=quiet, err=True)
        return False
    return True


# Only the real git sentinels. A bare `=======` line is legal markdown (setext h2
# underline), so matching it would refuse ordinary documents.
_MARKER_RE = re.compile(r"^(?:<{7}|>{7})(?: |$)", re.M)


def _conflict_marker_files(gh: Path) -> list[str]:
    """Return staged text files containing raw git conflict markers.

    This is the last gate before corruption becomes SHARED state, so it runs at the
    commit boundary and therefore catches every cause (stash-pop conflict, aborted
    rebase, hand-edited file), not just the one that was diagnosed. 16 commits in the
    live vault's history had introduced marker strings before this existed.
    """
    out: list[str] = []
    names = run_git(gh, "diff", "--cached", "--name-only", check=False).stdout.split("\n")
    for name in (n.strip() for n in names):
        if not name:
            continue
        f = gh / name
        try:
            if not f.is_file() or f.stat().st_size > 4_000_000:
                continue
            if _MARKER_RE.search(f.read_text(errors="ignore")):
                out.append(name)
        except Exception:
            continue
    return out


def _unmerged_paths(gh: Path) -> list[str]:
    """Paths git reports as unmerged — a merge/rebase is mid-flight."""
    r = run_git(gh, "ls-files", "-u", check=False)
    return sorted({ln.split("\t")[-1] for ln in r.stdout.splitlines() if "\t" in ln})


def commit_local(gh: Path, host: str, quiet: bool, context: str = "auto-sync") -> bool:
    """Stage and commit. Returns True if a commit was made.

    v3.6: the commit message is generated deterministically from the staged
    diff (`_commitmsg.build_message`) so `git log` is a readable audit trail
    instead of "auto-sync from <host>". `context` names the hook that fired
    (e.g. "pre-compact", "auto-sync") and lands in a `Context:` trailer.
    """
    unmerged = _unmerged_paths(gh)
    if unmerged:
        log(f"sync: REFUSING to commit — {len(unmerged)} unmerged path(s), a merge or "
            f"rebase is mid-flight (e.g. {unmerged[0]}). Resolve first: cd {gh} && "
            f"git status", quiet=quiet, err=True)
        return False

    run_git(gh, "add", "-A", check=False)
    status = run_git(gh, "status", "--porcelain", check=False).stdout
    if not status.strip():
        return False

    marked = _conflict_marker_files(gh)
    if marked:
        # Unstage them so the NEXT run cannot sweep them in, and make the problem
        # visible instead of pushing corruption to the other machine.
        for name in marked:
            run_git(gh, "restore", "--staged", "--", name, check=False)
        log(f"sync: REFUSING to commit — raw conflict markers in {len(marked)} file(s): "
            f"{', '.join(marked[:3])}. Fix the markers, then sync again.",
            quiet=quiet, err=True)
        log_debug("auto-sync", f"conflict markers blocked commit: {marked}")
        return False
    try:
        msg = build_message(gh, host=host, context=context)
    except Exception as e:
        log_debug("auto-sync", f"build_message failed: {e}")
        msg = f"{context} from {host}"  # fallback to the old one-liner
    try:
        run_git(
            gh,
            "-c", "user.name=gowth-mem",
            "-c", f"user.email=gowth-mem@{host}",
            "commit", "-m", msg,
        )
        log(f"sync: committed local changes from {host}", quiet=quiet)
        return True
    except subprocess.CalledProcessError as e:
        log(f"sync: commit failed: {e.stderr.strip()[:200]}", quiet=quiet, err=True)
        return False


_STASH_MSG = "auto-sync pre-pull stash"


def _clear_stale_rebase(gh: Path, quiet: bool) -> bool:
    """Abort a leftover rebase if `.git/rebase-merge/` or `.git/rebase-apply/`
    is present from a previously interrupted sync.

    Refuses to abort when SYNC-CONFLICT.md is present — that signals an
    *active* conflict awaiting `/mem-sync-resolve`, not stale state.

    Returns True if state is clean (or successfully cleaned), False if abort
    failed and the repo is still mid-rebase.
    """
    git_dir = gh / ".git"
    stale = (git_dir / "rebase-merge").is_dir() or (git_dir / "rebase-apply").is_dir()
    if not stale:
        return True
    if conflict_md().is_file():
        log(
            "sync: rebase in progress with SYNC-CONFLICT.md present — "
            "leaving intact, run /mem-sync-resolve.",
            quiet=quiet, err=True,
        )
        return False
    r = run_git(gh, "rebase", "--abort", check=False)
    if r.returncode == 0:
        log("sync: aborted stale rebase from prior interrupted sync", quiet=quiet)
        return True
    err = (r.stderr or r.stdout or "").strip()[:300]
    log(
        f"sync: stale rebase detected but abort failed ({err}). "
        f"Resolve manually: cd {gh} && git rebase --abort.",
        quiet=quiet, err=True,
    )
    return False


def _stash_if_dirty(gh: Path, quiet: bool):
    """Stash uncommitted changes if dirty.

    Returns: msg (str) if stashed, None if clean, False if stash failed.
    """
    status = run_git(gh, "status", "--porcelain", check=False).stdout
    if not status.strip():
        return None
    r = run_git(gh, "stash", "push", "-u", "-m", _STASH_MSG, check=False)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()[:300]
        log(
            f"sync: dirty tree, stash failed ({err}). "
            f"Resolve manually: cd {gh} && git status, then commit/restore.",
            quiet=quiet, err=True,
        )
        return False
    if "No local changes to save" in (r.stdout or ""):
        return None
    log("sync: stashed dirty tree before pull", quiet=quiet)
    return _STASH_MSG


def _restore_stash(gh: Path, pull_ok: bool, quiet: bool) -> bool:
    """Pop the auto-stash. Returns False when the tree is left needing attention.

    A failed pop used to be logged and forgotten — with a message that wrongly said
    "changes safe in stash" — while the working tree was left holding raw `<<<<<<<`
    markers, and the caller still reported success. The next commit then published the
    corruption. Now the failure is propagated so `pull_rebase` can return 2 and the
    marker guard in `commit_local` has a chance to refuse.
    """
    if not pull_ok:
        log(
            f"sync: dirty changes preserved in stash '{_STASH_MSG}'. "
            f"After resolving: cd {gh} && git stash list && git stash pop",
            quiet=quiet, err=True,
        )
        return False
    r = run_git(gh, "stash", "pop", check=False)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()[:300]
        log(
            f"sync: stash pop CONFLICTED — the working tree now holds conflict "
            f"markers and the stash entry was kept. Nothing will be committed until "
            f"you resolve it: cd {gh} && git status. Detail: {err}",
            quiet=quiet, err=True,
        )
        log_debug("auto-sync", f"stash pop conflict: {err}")
        return False
    log("sync: restored stashed changes", quiet=quiet)
    return True


def pull_rebase(gh: Path, branch: str, quiet: bool,
                remote: str, token: Optional[str]) -> int:
    """Pull --rebase; auto-stash dirty tree, restore after. Returns 0/2/1."""
    if not _clear_stale_rebase(gh, quiet):
        return 1
    stash_ref = _stash_if_dirty(gh, quiet)
    if stash_ref is False:
        return 1

    r = run_git(gh, "pull", "--rebase", "origin", branch, check=False,
                remote=remote, token=token)
    if r.returncode == 0:
        log(f"sync: pulled origin/{branch}", quiet=quiet)
        rc = 0
    else:
        err = (r.stderr or "") + (r.stdout or "")
        if "couldn't find remote ref" in err.lower():
            # Remote branch doesn't exist yet — first push will create it.
            log(f"sync: remote {branch} doesn't exist yet (will create on push)", quiet=quiet)
            rc = 0
        elif "CONFLICT" in err:
            from _conflict import package_conflict  # type: ignore
            cm = package_conflict()
            log(f"sync: conflict — wrote {cm}. Run /mem-sync-resolve.", quiet=quiet, err=True)
            rc = 2
        else:
            log(f"sync: pull failed: {err.strip()[:300]}", quiet=quiet, err=True)
            rc = 1

    if stash_ref:
        # A conflicted pop leaves markers in the tree; surface it as a conflict so the
        # caller stops (and commit_local's marker guard refuses) instead of publishing.
        if not _restore_stash(gh, pull_ok=(rc == 0), quiet=quiet) and rc == 0:
            rc = 2
    return rc


def push(gh: Path, branch: str, quiet: bool,
         remote: str, token: Optional[str]) -> int:
    r = run_git(gh, "push", "-u", "origin", branch, check=False,
                remote=remote, token=token)
    if r.returncode == 0:
        log(f"sync: pushed origin/{branch}", quiet=quiet)
        return 0
    log(f"sync: push failed: {(r.stderr or '').strip()[:300]}", quiet=quiet, err=True)
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pull-only", action="store_true")
    ap.add_argument("--commit-only", action="store_true")
    ap.add_argument("--pull-rebase-push", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    quiet = args.quiet
    gh = gowth_home()
    if not gh.is_dir():
        # Not initialized — silent in hook context.
        log(f"sync: {gh} not initialized — run /mem-install", quiet=quiet)
        return 0

    # If conflict pending, refuse new sync until resolved.
    if conflict_md().is_file() and not args.commit_only:
        log("sync: SYNC-CONFLICT.md present — run /mem-sync-resolve first", quiet=quiet, err=True)
        return 2

    config = load_config()
    remote = config.get("remote")
    branch = config.get("branch", "main")
    token = os.environ.get("GOWTH_MEM_GIT_TOKEN") or config.get("token")
    host = config.get("host_id") or socket.gethostname()

    # Network ops require remote; commit-only doesn't.
    if args.commit_only:
        if not (gh / ".git").is_dir():
            return 0
        try:
            with file_lock("sync", timeout=5.0):
                commit_local(gh, host, quiet, context="pre-compact")
        except TimeoutError as e:
            log_debug("auto-sync", f"commit-only lock timeout: {e}")
            log("sync: commit skipped — sync lock held", quiet=quiet, err=True)
            return 0
        return 0

    if not remote:
        log("sync: no remote configured — run /mem-config or /mem-install", quiet=quiet)
        return 0

    # SessionStart pull-only path must never block > ~8s. Full sync is more
    # tolerant but still capped well below the old 30s.
    lock_timeout = 5.0 if args.pull_only else 5.0
    try:
        with file_lock("sync", timeout=lock_timeout):
            if not ensure_repo(gh, remote, branch, token, quiet):
                return 1

            if args.pull_only:
                return pull_rebase(gh, branch, quiet, remote, token)

            # Default & --pull-rebase-push: commit local, pull, push.
            commit_local(gh, host, quiet)
            rc = pull_rebase(gh, branch, quiet, remote, token)
            if rc != 0:
                return rc
            return push(gh, branch, quiet, remote, token)
    except TimeoutError as e:
        log_debug("auto-sync", f"sync lock timeout (pull_only={args.pull_only}): {e}")
        log("sync: skipped — another session holds the sync lock", quiet=quiet, err=True)
        return 0


if __name__ == "__main__":
    sys.exit(main())
