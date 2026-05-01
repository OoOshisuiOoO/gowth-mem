#!/usr/bin/env python3
"""Git-sync helper for .gowth-mem/ directory.

Syncs `.gowth-mem/` (AGENTS.md + docs/* + settings.json + skills/*) across
machines via a user-owned git remote. Per-machine state (config.json,
state.json, index.db) stays local via .gitignore.

Token resolution (in priority order):
  1. env var GOWTH_MEM_GIT_TOKEN
  2. .gowth-mem/config.json → "token" field

Conflict strategy:
  - Auto-commit local changes before pull (with hostname as author).
  - `git pull --rebase` to integrate remote.
  - On rebase conflict: report files with conflict markers, exit 2,
    instruct user to resolve manually then re-run.

CLI:
  python3 _sync.py [--workspace PATH] [--init|--pull-only|--push-only]

Output:
  Multi-line status report per step.
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


def run_git(cwd: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True,
    )
    if check and r.returncode != 0:
        raise subprocess.CalledProcessError(r.returncode, r.args, r.stdout, r.stderr)
    return r.stdout


def auth_url(remote: str, token: Optional[str]) -> str:
    """Embed token into HTTPS GitHub-style URL. SSH URLs returned unchanged."""
    if not token or not remote.startswith("https://"):
        return remote
    # https://github.com/owner/repo.git → https://<token>@github.com/owner/repo.git
    return remote.replace("https://", f"https://{token}@", 1)


def load_config(gm: Path) -> dict:
    cfg_path = gm / "config.json"
    if not cfg_path.is_file():
        return {}
    try:
        return json.loads(cfg_path.read_text())
    except Exception:
        return {}


def write_default_gitignore(gm: Path) -> None:
    gi = gm / ".gitignore"
    if gi.is_file():
        return
    gi.write_text(
        "# .gowth-mem internal — gitignored (per-machine)\n"
        "config.json\n"
        "state.json\n"
        "index.db\n"
        "index.db-shm\n"
        "index.db-wal\n"
        "__pycache__/\n"
        "*.pyc\n"
        "SYNC-CONFLICT.md\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default=None)
    ap.add_argument("--init", action="store_true", help="Initialize repo, push current state")
    ap.add_argument("--pull-only", action="store_true")
    ap.add_argument("--push-only", action="store_true")
    args = ap.parse_args()

    workspace = Path(args.workspace or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    gm = workspace / ".gowth-mem"
    if not gm.is_dir():
        print("ERROR: .gowth-mem/ not initialized. Run /mem-init or /mem-migrate first.", file=sys.stderr)
        return 1

    config = load_config(gm)
    remote = config.get("remote")
    branch = config.get("branch", "main")
    token = os.environ.get("GOWTH_MEM_GIT_TOKEN") or config.get("token")

    if not remote:
        print(
            "ERROR: .gowth-mem/config.json missing 'remote'. Run /mem-config to set it up.\n"
            "Example: {\"remote\": \"https://github.com/USER/REPO.git\", \"branch\": \"main\"}\n"
            "Token (optional, prefer env var GOWTH_MEM_GIT_TOKEN).",
            file=sys.stderr,
        )
        return 1

    auth = auth_url(remote, token)
    host = socket.gethostname()

    write_default_gitignore(gm)

    git_dir = gm / ".git"
    initialized = git_dir.is_dir()

    if args.init or not initialized:
        if not initialized:
            run_git(gm, "init", "-b", branch)
            print(f"init: created .gowth-mem/.git on branch {branch}")
        # Set or update remote
        try:
            run_git(gm, "remote", "set-url", "origin", auth)
        except subprocess.CalledProcessError:
            run_git(gm, "remote", "add", "origin", auth)
        # Initial commit if nothing committed yet
        try:
            run_git(gm, "rev-parse", "HEAD", check=True)
            has_head = True
        except subprocess.CalledProcessError:
            has_head = False
        if not has_head:
            run_git(gm, "add", "-A")
            try:
                run_git(gm, "-c", f"user.name=gowth-mem", "-c", f"user.email=gowth-mem@{host}",
                        "commit", "-m", f"initial sync from {host}")
            except subprocess.CalledProcessError as e:
                print(f"WARN: initial commit failed: {e.stderr}", file=sys.stderr)
        # Try to pull (allow unrelated histories on init)
        try:
            run_git(gm, "pull", "origin", branch, "--allow-unrelated-histories", "--rebase")
            print(f"init: pulled origin/{branch} (allowed unrelated histories)")
        except subprocess.CalledProcessError as e:
            err = (e.stderr or "")
            if "couldn't find remote ref" in err or "Couldn't find remote ref" in err:
                print(f"init: remote {branch} branch doesn't exist yet — will create on push")
            elif "CONFLICT" in err:
                print("init: conflict during initial pull — resolve manually, then re-run.", file=sys.stderr)
                return 2
            else:
                print(f"init: pull warning: {err.strip()[:200]}")
        # Push (creates branch if needed)
        try:
            run_git(gm, "push", "-u", "origin", branch)
            print(f"init: pushed to origin/{branch}")
        except subprocess.CalledProcessError as e:
            print(f"ERROR: push failed: {e.stderr}", file=sys.stderr)
            return 1
        if args.init:
            return 0

    # Always update remote URL (in case token rotated)
    try:
        run_git(gm, "remote", "set-url", "origin", auth)
    except subprocess.CalledProcessError:
        pass

    # Auto-commit local changes before sync
    if not args.pull_only:
        run_git(gm, "add", "-A")
        status = run_git(gm, "status", "--porcelain")
        if status.strip():
            try:
                run_git(gm, "-c", f"user.name=gowth-mem", "-c", f"user.email=gowth-mem@{host}",
                        "commit", "-m", f"auto-sync from {host}")
                print(f"sync: committed local changes from {host}")
            except subprocess.CalledProcessError as e:
                print(f"WARN: auto-commit failed: {e.stderr.strip()[:200]}")

    # Pull (with rebase)
    if not args.push_only:
        try:
            out = run_git(gm, "pull", "--rebase", "origin", branch)
            print(f"sync: pulled origin/{branch}")
            if "Successfully rebased" in out or "up to date" in out.lower():
                pass
        except subprocess.CalledProcessError as e:
            err = (e.stderr or "") + (e.stdout or "")
            print(f"sync: pull failed: {err.strip()[:300]}", file=sys.stderr)
            if "CONFLICT" in err:
                conflict_files = run_git(gm, "diff", "--name-only", "--diff-filter=U", check=False).strip()
                conflict_msg = (
                    "# SYNC CONFLICT\n\n"
                    f"Pull from origin/{branch} hit conflicts on:\n\n"
                )
                for f in conflict_files.splitlines():
                    conflict_msg += f"- {f}\n"
                conflict_msg += (
                    "\n## Resolve\n\n"
                    "1. Open each file, find `<<<<<<<` markers, pick the right version.\n"
                    "2. `git -C .gowth-mem add <file>`\n"
                    "3. `git -C .gowth-mem rebase --continue`\n"
                    "4. Re-run `/mem-sync` (or `memY` shortcut).\n"
                    "\nTo abort: `git -C .gowth-mem rebase --abort`\n"
                )
                (gm / "SYNC-CONFLICT.md").write_text(conflict_msg)
                print(f"sync: wrote .gowth-mem/SYNC-CONFLICT.md — resolve and re-run.")
                return 2
            return 1

    # Push
    if not args.pull_only:
        try:
            run_git(gm, "push", "origin", branch)
            print(f"sync: pushed to origin/{branch}")
        except subprocess.CalledProcessError as e:
            print(f"ERROR: push failed: {e.stderr}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
