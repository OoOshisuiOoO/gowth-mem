#!/usr/bin/env python3
"""UserPromptSubmit hook: if ~/.gowth-mem/SYNC-CONFLICT.md exists, inject a
reminder so Claude prompts the user to run /mem-sync resolve before doing
other work.

Reads stdin (Claude Code's prompt JSON) and writes the standard hook output
JSON with `additionalContext`.

v4.9: GOWTH_MEM_OFF=1|true|yes|on → drain stdin, exit 0, nothing else. The
prompt is parsed (it used to be discarded): a bot/pipeline-marked prompt
(`_capture.is_pipeline_prompt`) is never nagged.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _home import clamp_context, conflict_md, drain_stdin, hooks_off, read_settings  # type: ignore


def _v3_nudge(settings: "dict | None" = None) -> str:
    """Return v3 upgrade nudge if settings.layout_version < 3, else empty."""
    try:
        s = read_settings() if settings is None else settings
        layout = int(s.get("layout_version", 0) or 0)
    except Exception:
        layout = 0
    if layout >= 3:
        return ""
    return (
        "[gowth-mem v3.0 upgrade] settings.layout_version=" + str(layout) + " (< 3).\n"
        "Run `/mem-ops migrate-v3` to convert this tree to v3 topic-folder + dated-aspect layout.\n\n"
    )


def _prompt_of(raw: str) -> str:
    try:
        ev = json.loads(raw) if raw.strip() else {}
    except Exception:
        return ""
    prompt = ev.get("prompt") if isinstance(ev, dict) else None
    return prompt if isinstance(prompt, str) else ""


def _pipeline_prompt(prompt: str, settings: dict) -> bool:
    try:
        from _capture import is_pipeline_prompt  # type: ignore
        return is_pipeline_prompt(prompt, settings)
    except Exception:
        return False


def main() -> int:
    if hooks_off():
        drain_stdin()
        return 0
    try:
        raw = sys.stdin.read()
    except Exception:
        raw = ""

    cm = conflict_md()
    if not cm.is_file():
        return 0

    try:
        settings = read_settings()
    except Exception:
        settings = {}
    prompt = _prompt_of(raw)
    if prompt and _pipeline_prompt(prompt, settings):
        return 0

    try:
        head = cm.read_text(errors="ignore").splitlines()[:30]
    except Exception:
        head = []

    summary = "\n".join(head)
    msg = (
        _v3_nudge(settings)
        + "[gowth-mem SYNC-CONFLICT pending]\n\n"
        f"~/.gowth-mem/SYNC-CONFLICT.md exists. Resolve before continuing other work.\n"
        f"Run /mem-sync resolve to walk each conflicted file and apply the user's choice.\n\n"
        "Preview (first 30 lines):\n"
        "```\n"
        f"{summary}\n"
        "```\n"
    )

    # v4.8: 30 long lines could cross the host's 10,000-char persist rule
    # (the model would then see a 2,000-char preview of this notice).
    out = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": clamp_context(msg),
        }
    }
    sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    except Exception:
        rc = 0  # a hook entrypoint never exits non-zero
    sys.exit(rc)
