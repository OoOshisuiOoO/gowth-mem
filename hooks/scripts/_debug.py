from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path


def _log_to(rel_path: str, component: str, message: str) -> None:
    home = Path(os.environ.get("GOWTH_MEM_HOME") or Path.home() / ".gowth-mem")
    log = home / rel_path
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().isoformat(timespec="seconds")
        with log.open("a", encoding="utf-8") as f:
            f.write(f"{stamp} {component}: {message}\n")
    except Exception:
        pass


def log_debug(component: str, message: str) -> None:
    """Log a hook diagnostic.

    Default: silent unless `GOWTH_MEM_DEBUG=1`, in which case a verbose line
    is appended to `~/.gowth-mem/logs/hooks.log`. Errors/warnings ALWAYS get
    a short line in `~/.gowth-mem/logs/hook-errors.log` so users can debug
    silent hook failures without setting an env var first.
    """
    # Always-on lightweight error log so we never lose a signal.
    _log_to("logs/hook-errors.log", component, message)
    # Verbose duplicate when user opts in.
    if os.environ.get("GOWTH_MEM_DEBUG") == "1":
        _log_to("logs/hooks.log", component, message)
