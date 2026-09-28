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
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import atomic_write  # type: ignore
from _debug import log_debug  # type: ignore
from _home import gowth_home, list_workspaces, state_path, workspace_dir  # type: ignore
from _lock import file_lock  # type: ignore

STATE_KEY = "memory_sanitized"
MEMFILE_NAME = "MEMORY.md"


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


def sanitize_memory_files(lock_timeout: float = 2.0) -> dict:
    """Sanitize every changed memory file in every workspace.

    Returns {"scanned": n, "sanitized": [relpaths], "secrets": n}. A file
    whose content hash matches the recorded one is skipped. MEMORY.md is
    written under the workspace's memfile lock (`_memfile.write` holds it);
    on timeout the file is left for the next pass (its hash is not recorded).
    Never raises."""
    report = {"scanned": 0, "sanitized": [], "secrets": 0}
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
            text = p.read_text(errors="ignore")
        except OSError:
            continue
        h = _sha1(text)
        if known.get(rel) == h:
            fresh[rel] = h
            continue
        try:
            cleaned, n = sanitize(text)
        except Exception as exc:
            log_debug("memsan", f"sanitize failed for {rel}: {exc}")
            continue
        if n > 0 and isinstance(cleaned, str) and cleaned != text:
            try:
                if p.name == MEMFILE_NAME:
                    with file_lock(f"memfile-{ws}", timeout=lock_timeout):
                        atomic_write(p, cleaned)
                else:
                    atomic_write(p, cleaned)
            except Exception as exc:
                log_debug("memsan", f"write skipped for {rel}: {exc}")
                continue                     # not recorded → retried next pass
            report["sanitized"].append(rel)
            report["secrets"] += int(n)
            log_debug("memsan", f"sanitized {n} secret(s) in {rel}")
            h = _sha1(cleaned)
        fresh[rel] = h
    if fresh != known:
        _remember(fresh)
    return report


if __name__ == "__main__":
    r = sanitize_memory_files()
    print(f"memsan: scanned {r['scanned']} file(s), sanitized {len(r['sanitized'])}, "
          f"{r['secrets']} secret(s)")
    for rel in r["sanitized"]:
        print(f"  {rel}")
