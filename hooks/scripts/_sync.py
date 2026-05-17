#!/usr/bin/env python3
"""Manual sync helper for ~/.gowth-mem/. Lock-protected.

This is the user-facing CLI counterpart to the auto-sync.py hook. They share
the same core logic; auto-sync runs in hook contexts (quiet-by-default, never
fails the hook), this one is verbose for /mem-sync.

CLI:
  python3 _sync.py [--init|--pull-only|--push-only]

Conflict path: writes ~/.gowth-mem/SYNC-CONFLICT.md via _conflict.py and exits 2.
User then runs /mem-sync-resolve.
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import atomic_write  # type: ignore
from _git import auth_url, git_cmd, load_config, run_git  # type: ignore  # noqa: F401 (git_cmd re-exported for tests)
from _home import conflict_md, gowth_home  # type: ignore
from _lock import file_lock  # type: ignore


_DEFAULT_GITIGNORE = (
    "# ~/.gowth-mem internal — gitignored (per-machine)\n"
    "config.json\n"
    "state.json\n"
    "index.db\n"
    "index.db-shm\n"
    "index.db-wal\n"
    ".locks/\n"
    ".audit/\n"
    ".dedup-window.json\n"
    "__pycache__/\n"
    "*.pyc\n"
    "SYNC-CONFLICT.md\n"
)

_REQUIRED_IGNORES = (".audit/", ".dedup-window.json")


def write_default_gitignore(gh: Path) -> None:
    """Write template on first install; on subsequent runs backfill missing
    privacy/audit entries idempotently (preserves user edits to other lines)."""
    gi = gh / ".gitignore"
    if not gi.is_file():
        atomic_write(gi, _DEFAULT_GITIGNORE)
        return
    try:
        existing = gi.read_text(errors="ignore")
    except Exception:
        return
    missing = [e for e in _REQUIRED_IGNORES if e not in existing]
    if not missing:
        return
    additions = "".join(f"{e}\n" for e in missing)
    sep = "" if existing.endswith("\n") else "\n"
    atomic_write(gi, f"{existing}{sep}{additions}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--pull-only", action="store_true")
    ap.add_argument("--push-only", action="store_true")
    args = ap.parse_args()

    gh = gowth_home()
    if not gh.is_dir():
        print("ERROR: ~/.gowth-mem not initialized. Run /mem-install first.", file=sys.stderr)
        return 1

    if conflict_md().is_file():
        print("ERROR: SYNC-CONFLICT.md present. Run /mem-sync-resolve first.", file=sys.stderr)
        return 2

    config = load_config()
    remote = config.get("remote")
    branch = config.get("branch", "main")
    token = os.environ.get("GOWTH_MEM_GIT_TOKEN") or config.get("token")
    host = config.get("host_id") or socket.gethostname()

    if not remote:
        print(
            "ERROR: ~/.gowth-mem/config.json missing 'remote'.\n"
            "Run /mem-config to set it up. Token via env GOWTH_MEM_GIT_TOKEN preferred.",
            file=sys.stderr,
        )
        return 1

    auth = auth_url(remote, token)
    write_default_gitignore(gh)

    git_dir = gh / ".git"
    initialized = git_dir.is_dir()

    try:
        with file_lock("sync", timeout=30.0):
            if args.init or not initialized:
                if not initialized:
                    run_git(gh, "init", "-b", branch)
                    print(f"init: created .git on branch {branch}")
                try:
                    run_git(gh, "remote", "set-url", "origin", auth)
                except subprocess.CalledProcessError:
                    run_git(gh, "remote", "add", "origin", auth)
                try:
                    run_git(gh, "rev-parse", "HEAD", check=True)
                    has_head = True
                except subprocess.CalledProcessError:
                    has_head = False
                if not has_head:
                    run_git(gh, "add", "-A")
                    try:
                        run_git(
                            gh, "-c", "user.name=gowth-mem",
                            "-c", f"user.email=gowth-mem@{host}",
                            "commit", "-m", f"initial sync from {host}",
                        )
                    except subprocess.CalledProcessError as e:
                        print(f"WARN: initial commit failed: {e.stderr}", file=sys.stderr)
                try:
                    run_git(gh, "pull", "origin", branch,
                            "--allow-unrelated-histories", "--rebase",
                            remote=remote, token=token)
                    print(f"init: pulled origin/{branch}")
                except subprocess.CalledProcessError as e:
                    err = (e.stderr or "")
                    if "couldn't find remote ref" in err.lower():
                        print(f"init: remote {branch} doesn't exist yet — will create on push")
                    elif "CONFLICT" in err:
                        from _conflict import package_conflict  # type: ignore
                        package_conflict()
                        print("init: conflict — wrote SYNC-CONFLICT.md, run /mem-sync-resolve",
                              file=sys.stderr)
                        return 2
                    else:
                        print(f"init: pull warning: {err.strip()[:200]}")
                try:
                    run_git(gh, "push", "-u", "origin", branch, remote=remote, token=token)
                    print(f"init: pushed to origin/{branch}")
                except subprocess.CalledProcessError as e:
                    print(f"ERROR: push failed: {e.stderr}", file=sys.stderr)
                    return 1
                if args.init:
                    return 0

            try:
                run_git(gh, "remote", "set-url", "origin", auth)
            except subprocess.CalledProcessError:
                pass

            if not args.pull_only:
                run_git(gh, "add", "-A", check=False)
                status = run_git(gh, "status", "--porcelain", check=False).stdout
                if status.strip():
                    try:
                        run_git(
                            gh, "-c", "user.name=gowth-mem",
                            "-c", f"user.email=gowth-mem@{host}",
                            "commit", "-m", f"sync from {host}",
                        )
                        print(f"sync: committed local changes from {host}")
                    except subprocess.CalledProcessError as e:
                        print(f"WARN: commit failed: {e.stderr.strip()[:200]}")

            if not args.push_only:
                r = run_git(gh, "pull", "--rebase", "origin", branch, check=False,
                            remote=remote, token=token)
                if r.returncode != 0:
                    err = (r.stderr or "") + (r.stdout or "")
                    if "CONFLICT" in err:
                        from _conflict import package_conflict  # type: ignore
                        package_conflict()
                        print("sync: conflict — wrote SYNC-CONFLICT.md, run /mem-sync-resolve",
                              file=sys.stderr)
                        return 2
                    print(f"sync: pull failed: {err.strip()[:300]}", file=sys.stderr)
                    return 1
                print(f"sync: pulled origin/{branch}")

            if not args.pull_only:
                r = run_git(gh, "push", "origin", branch, check=False,
                            remote=remote, token=token)
                if r.returncode != 0:
                    print(f"ERROR: push failed: {(r.stderr or '').strip()[:200]}", file=sys.stderr)
                    return 1
                print(f"sync: pushed to origin/{branch}")
    except TimeoutError:
        print("sync: another session holds the sync lock; try again shortly", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
