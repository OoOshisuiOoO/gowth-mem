"""fcntl.flock helper — multi-session safe advisory locking.

Used to serialize:
  - git operations in _sync.py / auto-sync.py     (lock name: "sync")
  - state.json updates in recall-active.py        (lock name: "state")

Behavior:
  - Lock files live under ~/.gowth-mem/.locks/<name>.lock (gitignored).
  - Exclusive blocking lock with timeout (default 30s).
  - On timeout: TimeoutError raised (caller decides to abort/retry).
  - Windows: fcntl unavailable → no-op contextmanager (single-session assumed).
"""
from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _home import locks_dir  # type: ignore

try:
    import fcntl  # type: ignore
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False


@contextmanager
def file_lock(name: str, timeout: float = 30.0):
    """Acquire ~/.gowth-mem/.locks/<name>.lock or raise TimeoutError."""
    if not HAS_FCNTL:
        # Windows or unsupported — no concurrent-session protection.
        yield
        return

    ld = locks_dir()
    ld.mkdir(parents=True, exist_ok=True)
    path = ld / f"{name}.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.time() + timeout
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.time() >= deadline:
                os.close(fd)
                raise TimeoutError(f"lock '{name}' held >{timeout}s")
            time.sleep(0.1)
    try:
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
