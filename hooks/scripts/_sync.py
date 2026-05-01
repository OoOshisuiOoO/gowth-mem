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


def load_config(gh: Path) -> dict:
    p = config_path()
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def write_default_gitignore(gh: Path) -> None:
    gi = gh / ".gitignore"
    if gi.is_file():
        return
    gi.write_text(
        "# ~/.gowth-mem internal — gitignored (per-machine)\n"
        "config.json\n"
        "state.json\n"
        "index.db\n"
        "index.db-shm\n"
        "index.db-wal\n"
        ".locks/\n"
        "__pycache__/\n"
        "*.pyc\n"
        "SYNC-CONFLICT.md\n"
    )


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

    config = load_config(gh)
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
                            "--allow-unrelated-histories", "--rebase")
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
                    run_git(gh, "push", "-u", "origin", branch)
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
                r = run_git(gh, "pull", "--rebase", "origin", branch, check=False)
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
                r = run_git(gh, "push", "origin", branch, check=False)
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
