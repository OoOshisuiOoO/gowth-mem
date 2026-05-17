"""Shared git helpers for _sync.py and auto-sync.py.

Provides token-secure git command construction and subprocess execution.
Token is injected via HTTP header (never embedded in the remote URL).
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
from _debug import log_debug  # type: ignore
from _home import config_path  # type: ignore


def git_cmd(remote: str, token: Optional[str], *args: str) -> list[str]:
    """Build a git command list with optional token injected as HTTP header."""
    cmd = ["git"]
    if token and remote.startswith("https://"):
        header = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        cmd.extend(["-c", f"http.{remote}.extraHeader=AUTHORIZATION: basic {header}"])
    cmd.extend(args)
    return cmd


def run_git(cwd: Path, *args: str, check: bool = True,
            remote: str = "", token: Optional[str] = None) -> subprocess.CompletedProcess:
    """Run a git subcommand in *cwd*.

    Always uses capture_output=True. Raises CalledProcessError when
    check=True and returncode != 0.
    """
    r = subprocess.run(
        git_cmd(remote, token, "-C", str(cwd), *args),
        capture_output=True, text=True,
    )
    if check and r.returncode != 0:
        raise subprocess.CalledProcessError(r.returncode, r.args, r.stdout, r.stderr)
    return r


def auth_url(remote: str, token: Optional[str]) -> str:
    """Return the remote URL (token is never embedded in URL — use HTTP header)."""
    return remote


def load_config() -> dict:
    """Load ~/.gowth-mem/config.json; return {} on missing or invalid JSON."""
    p = config_path()
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception as e:
        log_debug("git", f"load_config failed: {e}")
        return {}
