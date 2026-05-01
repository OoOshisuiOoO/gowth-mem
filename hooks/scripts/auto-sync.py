#!/usr/bin/env python3
"""Auto git sync for ~/.gowth-mem/. Hook-friendly, lock-protected.

Modes:
  --pull-only         SessionStart: rebase remote into local, no push
  --commit-only       PreCompact: stage + commit, no network
  --pull-rebase-push  PostCompact: full sync; on conflict invoke _conflict.py
  --quiet             suppress non-error output (for hooks)

If ~/.gowth-mem/config.json is missing 'remote', script exits 0 silently in
hook contexts (user hasn't run /mem-install yet — don't spam logs).

All git ops run under file_lock('sync') with default 30s timeout. If a
parallel session holds the lock longer, this run skips with a warning rather
than blocking the hook.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
from _home import config_path, conflict_md, gowth_home  # type: ignore
from _lock import file_lock  # type: ignore


def run_git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True,
    )
    if check and r.returncode != 0:
        raise subprocess.CalledProcessError(r.returncode, r.args, r.stdout, r.stderr)
    return r


def auth_url(remote: str, token: Optional[str]) -> str:
    if not token or not remote.startswith("https://"):
        return remote
    return remote.replace("https://", f"https://{token}@", 1)


def load_config() -> dict:
    p = config_path()
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


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


def commit_local(gh: Path, host: str, quiet: bool, message: str = "auto-sync") -> bool:
    """Stage and commit. Returns True if a commit was made."""
    run_git(gh, "add", "-A", check=False)
    status = run_git(gh, "status", "--porcelain", check=False).stdout
    if not status.strip():
        return False
    try:
        run_git(
            gh,
            "-c", f"user.name=gowth-mem",
            "-c", f"user.email=gowth-mem@{host}",
            "commit", "-m", f"{message} from {host}",
        )
        log(f"sync: committed local changes from {host}", quiet=quiet)
        return True
    except subprocess.CalledProcessError as e:
        log(f"sync: commit failed: {e.stderr.strip()[:200]}", quiet=quiet, err=True)
        return False


def pull_rebase(gh: Path, branch: str, quiet: bool) -> int:
    """Pull --rebase; on conflict invoke _conflict.py. Returns 0/2/1."""
    r = run_git(gh, "pull", "--rebase", "origin", branch, check=False)
    if r.returncode == 0:
        log(f"sync: pulled origin/{branch}", quiet=quiet)
        return 0
    err = (r.stderr or "") + (r.stdout or "")
    if "couldn't find remote ref" in err.lower():
        # Remote branch doesn't exist yet — first push will create it.
        log(f"sync: remote {branch} doesn't exist yet (will create on push)", quiet=quiet)
        return 0
    if "CONFLICT" in err:
        from _conflict import package_conflict  # type: ignore
        cm = package_conflict()
        log(f"sync: conflict — wrote {cm}. Run /mem-sync-resolve.", quiet=quiet, err=True)
        return 2
    log(f"sync: pull failed: {err.strip()[:300]}", quiet=quiet, err=True)
    return 1


def push(gh: Path, branch: str, quiet: bool) -> int:
    r = run_git(gh, "push", "-u", "origin", branch, check=False)
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
            with file_lock("sync", timeout=10.0):
                commit_local(gh, host, quiet, message="pre-compact snapshot")
        except TimeoutError:
            log("sync: commit skipped — sync lock held", quiet=quiet, err=True)
            return 0
        return 0

    if not remote:
        log("sync: no remote configured — run /mem-config or /mem-install", quiet=quiet)
        return 0

    try:
        with file_lock("sync", timeout=30.0):
            if not ensure_repo(gh, remote, branch, token, quiet):
                return 1

            if args.pull_only:
                return pull_rebase(gh, branch, quiet)

            # Default & --pull-rebase-push: commit local, pull, push.
            commit_local(gh, host, quiet)
            rc = pull_rebase(gh, branch, quiet)
            if rc != 0:
                return rc
            return push(gh, branch, quiet)
    except TimeoutError:
        log("sync: skipped — another session holds the sync lock", quiet=quiet, err=True)
        return 0


if __name__ == "__main__":
    sys.exit(main())
