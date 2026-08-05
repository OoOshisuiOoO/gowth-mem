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
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import atomic_write  # type: ignore
from _git import auth_url, git_cmd, load_config, run_git  # type: ignore  # noqa: F401 (git_cmd re-exported for tests)
from _home import conflict_md, gowth_home, read_settings  # type: ignore
from _debug import log_debug  # type: ignore
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
    "review-ledger.json\n"
    ".archive/\n"
    ".backup/\n"
    ".session-workspace\n"
    "hook-errors.log\n"
    "*.log\n"
    "__pycache__/\n"
    "*.pyc\n"
    "SYNC-CONFLICT.md\n"
)

# review-ledger.json is machine-local: it references transcript paths under
# this machine's ~/.claude/projects, which other machines don't have.
#
# `.session-workspace` MUST be ignored: _home.py resolves it at priority 2, ABOVE
# config.json's workspace_map, so syncing it would pin every other machine to whatever
# workspace this machine last selected.
#
# `.archive/`, `.backup/` and `*.log` were missing from the default, so a fresh
# (non-clone) install would start committing gzip archive blobs and per-machine hook
# logs. Listed in _REQUIRED_IGNORES too so existing vaults backfill them.
_REQUIRED_IGNORES = (".audit/", ".dedup-window.json", "review-ledger.json",
                     ".archive/", ".backup/", ".session-workspace", "*.log")


def _gitignore_has_entry(existing: str, entry: str) -> bool:
    """Line-by-line membership: skip comments / negations / blank lines.

    The previous `entry in existing` substring check skipped backfill when a
    user comment contained the literal string (e.g. `# Maybe ignore .audit/
    later`), which would leak audit logs to the remote. This walks lines
    explicitly and matches the *normalized* entry only.
    """
    target = entry.strip()
    if not target:
        return False
    for raw in existing.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        if line == target:
            return True
    return False


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
    missing = [e for e in _REQUIRED_IGNORES if not _gitignore_has_entry(existing, e)]
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
                        from _commitmsg import build_message as _bm  # type: ignore
                        _msg = _bm(gh, host=host, context="initial")
                    except Exception:
                        _msg = f"initial sync from {host}"
                    try:
                        run_git(
                            gh, "-c", "user.name=gowth-mem",
                            "-c", f"user.email=gowth-mem@{host}",
                            "commit", "-m", _msg,
                        )
                    except subprocess.CalledProcessError as e:
                        print(f"WARN: initial commit failed: {e.stderr}", file=sys.stderr)
                try:
                    run_git(gh, "pull", "origin", branch,
                            "--allow-unrelated-histories", "--rebase",
                            remote=remote, token=token)
                    print(f"init: pulled origin/{branch}")
                except subprocess.CalledProcessError as e:
                    # git prints "CONFLICT (add/add)" on STDOUT, not stderr. Dropping
                    # stdout here meant `--init` on a SECOND machine never detected the
                    # conflict: it pushed anyway (rejected), and left raw `<<<<<<<`
                    # markers in shared/AGENTS.md — the file injected into every
                    # session — with no SYNC-CONFLICT.md to nudge the user.
                    err = (e.stderr or "") + (e.stdout or "")
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
                        from _commitmsg import build_message as _bm  # type: ignore
                        _msg = _bm(gh, host=host, context="mem-sync")
                    except Exception:
                        _msg = f"sync from {host}"
                    try:
                        run_git(
                            gh, "-c", "user.name=gowth-mem",
                            "-c", f"user.email=gowth-mem@{host}",
                            "commit", "-m", _msg,
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


# ── debounced auto-push (v4.4) ───────────────────────────────────────────
#
# Coverage gap this closes: hooks/hooks.json wires auto-sync to PostCompact ONLY, so a
# session that never compacts never pushes. Observed on a live vault: 116 uncommitted
# changes at ahead=0/behind=0 — a full session of hook writes (journal, forget
# deletions, _MAP, handoff) that a second machine could not see. The longer the drift,
# the worse the eventual conflict.
#
# The Stop hook runs every turn, so it is the right trigger — but it must never turn a
# turn into a network round-trip. Hence: debounce on a machine-local timestamp, and
# spawn the sync DETACHED so the hook returns immediately.

DEFAULT_AUTOSYNC_MINUTES = 30
MIN_AUTOSYNC_MINUTES = 5


def _sync_settings() -> dict:
    try:
        s = read_settings()
        v = s.get("sync") if isinstance(s, dict) else None
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def _record_autosync() -> None:
    """Stamp `last_autosync` in state.json without clobbering other keys."""
    try:
        p = gowth_home() / "state.json"
        try:
            d = json.loads(p.read_text()) if p.is_file() else {}
            if not isinstance(d, dict):
                d = {}
        except Exception:
            d = {}
        d["last_autosync"] = time.time()
        atomic_write(p, json.dumps(d, indent=2) + "\n")
    except Exception as exc:
        log_debug("sync", f"could not record last_autosync: {exc}")


def maybe_autosync(dry_run: bool = False) -> dict:
    """Push the vault if the debounce window has elapsed. Never raises.

    Returns {"due": bool, "interval_minutes": int, "spawned": bool}.
    Settings: `sync.auto_sync_on_stop` (default true),
              `sync.min_interval_minutes` (default 30, floor 5).
    """
    out = {"due": False, "interval_minutes": DEFAULT_AUTOSYNC_MINUTES, "spawned": False}
    try:
        cfg = _sync_settings()
        if not cfg.get("auto_sync_on_stop", True):
            return out
        try:
            minutes = int(cfg.get("min_interval_minutes", DEFAULT_AUTOSYNC_MINUTES))
        except Exception:
            minutes = DEFAULT_AUTOSYNC_MINUTES
        minutes = max(MIN_AUTOSYNC_MINUTES, minutes)
        out["interval_minutes"] = minutes

        gh = gowth_home()
        if not (gh / ".git").exists():
            return out          # not a synced vault; nothing to push

        last = 0.0
        try:
            p = gh / "state.json"
            if p.is_file():
                last = float(json.loads(p.read_text()).get("last_autosync") or 0.0)
        except Exception:
            last = 0.0

        if time.time() - last < minutes * 60:
            return out
        out["due"] = True
        if dry_run:
            return out

        script = Path(__file__).parent / "auto-sync.py"
        if not script.is_file():
            return out
        # Detached: the Stop hook must not wait on the network.
        subprocess.Popen(
            ["python3", str(script), "--pull-rebase-push", "--quiet"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, start_new_session=True,
        )
        _record_autosync()
        out["spawned"] = True
        return out
    except Exception as exc:
        log_debug("sync", f"maybe_autosync failed: {exc}")
        return out
