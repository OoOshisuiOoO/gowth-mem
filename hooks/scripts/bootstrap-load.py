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

NOT loaded here (Claude reads on-demand via grep / `[[wikilink]]` / explicit Read):
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
    read_settings,
    secrets_md,
    shared_tools_md,
    workspace_agents_md,
)

MAX_TOTAL = 15_000

# No single file may consume more than this. Before v4.3 the first file was handed
# the ENTIRE remaining budget, so two oversized shared files exhausted the cap and
# the loop broke at `room <= 200` — the live vault loaded 2 of 5 files and silently
# dropped docs/handoff.md, the one file carrying current session state.
MAX_PER_FILE = 4_000

DEFERRED_NOTICE = (
    "(docs/exp, docs/ref, docs/tools, docs/files, topic files, and skills "
    "are loaded on-demand via recall)"
)


def _read_text(f: Path) -> str:
    """Return *f*'s text, or "" for missing / empty / unreadable files."""
    if not f.is_file():
        return ""
    try:
        raw = f.read_text(errors="ignore")
    except Exception as exc:
        log_debug("bootstrap-load", f"read error {f}: {exc}")
        return ""
    return raw if raw.strip() else ""


def _format_block(f: Path, gh: Path, raw: str, allowance: int) -> tuple[str, int]:
    """Return (formatted_block, chars_used), truncating to *allowance*."""
    if not raw or allowance <= 0:
        return "", 0
    try:
        label = f"~/.gowth-mem/{f.relative_to(gh)}"
    except ValueError:
        label = str(f)
    if len(raw) <= allowance:
        return f"\n=== {label} ===\n{raw}", len(raw)
    omitted = len(raw) - allowance
    return (f"\n=== {label} ===\n{raw[:allowance]}"
            f"\n[truncated: {omitted} chars omitted]"), allowance


def _allocate(
    texts: dict,
    statics: list,
    deltas: list,
    total: int = MAX_TOTAL,
    per_file: int = MAX_PER_FILE,
) -> dict:
    """Split *total* chars across files, reserving the per-session deltas FIRST.

    `statics` are the large, slow-changing shared files (AGENTS/secrets/tools);
    `deltas` are the small per-session files (workspace AGENTS.md, docs/handoff.md,
    today's journal) that actually carry current state.

    Reserving the deltas' share before allocating statics is what stops a bloated
    shared file from starving them. Emission ORDER is unchanged (statics first) —
    CLAUDE.md requires a stable prompt-cache prefix, and handoff.md changes every
    session, so moving it to the front would invalidate the cached prefix behind it.
    """
    want = {f: min(len(texts.get(f, "")), per_file) for f in statics + deltas}
    reserved = sum(want[f] for f in deltas)

    allow: dict = {}
    room = max(0, total - reserved)
    for f in statics:
        take = min(want[f], room)
        allow[f] = take
        room -= take

    room = total - sum(allow.values())
    for f in deltas:
        take = min(want[f], room)
        allow[f] = take
        room -= take
    return allow


def _budget_planner_enabled(settings: dict) -> bool:
    if not isinstance(settings, dict):
        return False
    r = settings.get("retrieval", {})
    if isinstance(r, dict) and bool(r.get("use_budget_planner", False)):
        return True
    cb = settings.get("context_budget", {})
    return isinstance(cb, dict) and bool(cb.get("enabled", False))


def _load_via_budget_planner(ws: str, gh: Path, settings: dict) -> tuple[list[str], int, int, int]:
    """Use _budget.plan_context to fill the 15k cap. Falls back gracefully on import error."""
    try:
        from _budget import plan_context  # type: ignore
    except Exception as exc:  # pragma: no cover
        log_debug("bootstrap-load", f"budget planner import failed: {exc}")
        return [], 0, 0, 0
    try:
        plan = plan_context(ws=ws, query="", budget_chars=MAX_TOTAL, settings=settings)
    except Exception as exc:  # pragma: no cover
        log_debug("bootstrap-load", f"budget planner failed: {exc}")
        return [], 0, 0, 0
    parts: list[str] = []
    total = 0
    loaded = 0
    for p, snippet, _score in plan:
        try:
            rel = p.relative_to(gh)
            label = f"~/.gowth-mem/{rel}"
        except ValueError:
            label = str(p)
        block = f"\n=== {label} ===\n{snippet}"
        parts.append(block)
        total += len(snippet)
        loaded += 1
    return parts, total, loaded, len(plan)


def main() -> int:
    try:
        gh = gowth_home()
        ws = active_workspace()
        today = date.today()
        settings = read_settings()

        if _budget_planner_enabled(settings):
            parts, total, loaded, attempted = _load_via_budget_planner(ws, gh, settings)
            if loaded > 0:
                summary = f"\n[bootstrap: loaded {loaded}/{attempted} files via budget-planner, {total} chars / {MAX_TOTAL} cap — {DEFERRED_NOTICE}]"
                context = f"[gowth-mem:bootstrap workspace={ws} mode=budget-planner]" + "".join(parts) + summary
                out = {
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": context,
                    }
                }
                print(json.dumps(out))
                log_debug("bootstrap-load", f"budget-planner: {loaded}/{attempted} files, {total} chars")
                return 0
            log_debug("bootstrap-load", "budget planner returned 0 files; falling back to stable prefix")

        # Large, slow-changing shared files — the stable prompt-cache prefix.
        statics: list[Path] = [agents_md(), secrets_md(), shared_tools_md()]

        # Small per-session deltas carrying CURRENT state. Budget is reserved for
        # these before the statics are allocated, so they can never be starved.
        deltas: list[Path] = [
            workspace_agents_md(ws),
            docs_dir(ws) / "handoff.md",
        ]
        today_journal = journal_dir(ws) / f"{today.isoformat()}.md"
        if today_journal.is_file():
            deltas.append(today_journal)

        stable = statics + deltas
        texts = {f: _read_text(f) for f in stable}
        allow = _allocate(texts, statics, deltas)

        parts: list[str] = []
        total = 0
        loaded = 0
        attempted = sum(1 for f in stable if texts.get(f))

        for f in stable:
            block, used = _format_block(f, gh, texts.get(f, ""), allow.get(f, 0))
            if not block:
                continue
            parts.append(block)
            total += used
            loaded += 1

        if not parts:
            return 0

        summary = f"\n[bootstrap: loaded {loaded}/{attempted} files, {total} chars / {MAX_TOTAL} cap — {DEFERRED_NOTICE}]"

        # v3.0 mismatch nudge: settings.layout_version < 3 → prepend upgrade hint.
        nudge = ""
        try:
            layout = int(settings.get("layout_version", 0) or 0)
        except Exception:
            layout = 0
        if layout < 3:
            nudge = (
                "\n=== gowth-mem v3.0 upgrade available ===\n"
                "Your settings.json reports layout_version=" + str(layout) + " (< 3).\n"
                "v3.0 uses topic-FOLDER + dated-aspect layout (<slug>/00-README.md + YYYY-MM-DD-<aspect>.md).\n"
                "Run `/mem-migrate-v3` to migrate the local tree, then commit & sync.\n"
                "Read-path stays permissive across v3/v2.4/v2.3 — but writes are strict v3.\n"
            )

        context = f"[gowth-mem:bootstrap workspace={ws}]" + nudge + "".join(parts) + summary

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
