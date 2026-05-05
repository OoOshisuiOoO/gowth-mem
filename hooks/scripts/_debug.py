from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path


def log_debug(component: str, message: str) -> None:
    if os.environ.get("GOWTH_MEM_DEBUG") != "1":
        return
    home = Path(os.environ.get("GOWTH_MEM_HOME") or Path.home() / ".gowth-mem")
    log = home / "logs" / "hooks.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().isoformat(timespec="seconds")
        with log.open("a", encoding="utf-8") as f:
            f.write(f"{stamp} {component}: {message}\n")
    except Exception:
        pass
