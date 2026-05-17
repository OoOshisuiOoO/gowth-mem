#!/usr/bin/env python3
"""SessionStart hook (v2.10.2): aggressive-cap bootstrap — 15k char hard limit.

Stable prefix (always loaded, helps Anthropic prompt cache):
  1. shared/AGENTS.md                    — global rules
  2. shared/secrets.md                   — env-var pointers (small, stable)
  3. shared/tools.md                     — system-wide tools (small, stable)
  4. workspaces/<ws>/AGENTS.md           — workspace-specific rules (delta)
  5. workspaces/<ws>/docs/handoff.md     — current session state

Conditional (today only):
  6. workspaces/<ws>/journal/<today>.md  — loaded ONLY if it already exists

Deferred to recall-active.py at query time (NOT loaded here):
  - workspaces/<ws>/docs/{exp,ref,tools,files}.md
  - topic files (workspace root subdirs)
  - skills/ content
  - shared/files.md, _MAP.md
  - yesterday's journal and older

Caps: 15k total. Per-file truncation with [truncated: N chars omitted] marker.
Final line: [bootstrap: loaded N/M files, X chars / Y cap]
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _debug import log_debug  # type: ignore
from _home import (  # type: ignore
    active_workspace,
    agents_md,
    docs_dir,
    gowth_home,
    journal_dir,
    secrets_md,
    shared_tools_md,
    workspace_agents_md,
)

MAX_TOTAL = 15_000
DEFERRED_NOTICE = (
    "(docs/exp, docs/ref, docs/tools, docs/files, topic files, and skills "
    "are loaded on-demand via recall)"
)


def _load_file(f: Path, gh: Path, budget: int) -> tuple[str, int]:
    """Read *f* and return (formatted_block, chars_used).

    If the file's content exceeds *budget*, truncate and append a marker.
    Returns ("", 0) for missing / empty / unreadable files.
    """
    if not f.is_file():
        return "", 0
    try:
        raw = f.read_text(errors="ignore")
    except Exception as exc:
        log_debug("bootstrap-load", f"read error {f}: {exc}")
        return "", 0
    if not raw.strip():
        return "", 0

    try:
        rel = f.relative_to(gh)
        label = f"~/.gowth-mem/{rel}"
    except ValueError:
        label = str(f)

    if len(raw) <= budget:
        block = f"\n=== {label} ===\n{raw}"
        return block, len(raw)

    omitted = len(raw) - budget
    chunk = raw[:budget]
    block = f"\n=== {label} ===\n{chunk}\n[truncated: {omitted} chars omitted]"
    return block, budget


def main() -> int:
    try:
        gh = gowth_home()
        ws = active_workspace()
        today = date.today()

        # Priority-ordered stable files (always attempt to load)
        stable: list[Path] = [
            agents_md(),
            secrets_md(),
            shared_tools_md(),
            workspace_agents_md(ws),
            docs_dir(ws) / "handoff.md",
        ]

        # Conditional: today's journal only if it already exists
        today_journal = journal_dir(ws) / f"{today.isoformat()}.md"
        if today_journal.is_file():
            stable.append(today_journal)

        parts: list[str] = []
        total = 0
        loaded = 0
        attempted = len(stable)

        for f in stable:
            room = MAX_TOTAL - total
            if room <= 200:
                log_debug("bootstrap-load", f"budget exhausted at {total}/{MAX_TOTAL}, stopping")
                break
            block, used = _load_file(f, gh, room)
            if not block:
                continue
            parts.append(block)
            total += used
            loaded += 1

        if not parts:
            return 0

        summary = f"\n[bootstrap: loaded {loaded}/{attempted} files, {total} chars / {MAX_TOTAL} cap — {DEFERRED_NOTICE}]"
        context = f"[gowth-mem:bootstrap workspace={ws}]" + "".join(parts) + summary

        out = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": context,
            }
        }
        print(json.dumps(out))
        log_debug("bootstrap-load", f"done: {loaded}/{attempted} files, {total} chars")
        return 0

    except Exception as exc:
        log_debug("bootstrap-load", f"unhandled error: {exc}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
