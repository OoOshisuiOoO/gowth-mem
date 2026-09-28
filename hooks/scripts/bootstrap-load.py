#!/usr/bin/env python3
"""SessionStart hook (v4.8): native-first bootstrap.

Measured on Claude Code 2.1.283 (2026-09-28):
  * hook additionalContext / SessionStart stdout of >= 10,000 chars is persisted
    to a tool-results file; the model gets a 2,000-char preview. The previous
    15,589-char bootstrap hit this in 193 sessions — handoff.md never arrived.
  * auto-memory MEMORY.md (200 lines / 25 KB) is attached with the CLAUDE.md
    bundle at session start, on resume and after every compaction, exempt from
    that rule. `_memfile.py` keeps the working set there.

Modes (per workspace + project):
  native   — the project's autoMemoryDirectory points at <ws>/memory and
             MEMORY.md exists: print a <= 600-char header only.
             `compact`: header + the 15 newest handoff lines (<= 1,500 chars).
  fallback — not wired (or auto memory disabled on the host): print the same
             sections as the block, handoff first, <= 8,500 chars, plus a
             one-line nudge to run /mem-setup native.

Sources: startup / clear / empty → prepare (workspace.json, MEMORY.md refresh,
detached incremental reindex) + emit; compact → emit; resume → nothing (the
resumed transcript keeps its context).

Every emission passes `_home.clamp_context` (HOOK_CONTEXT_MAX = 9000).
`--report` prints what would be emitted, for /mem-cost.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _debug import log_debug  # type: ignore
from _home import (  # type: ignore
    HOOK_CONTEXT_MAX,
    active_workspace,
    clamp_context,
    read_settings,
    setting,
)
from _version import drift_nudge, version_tag  # type: ignore

HEADER_MAX = 600
COMPACT_MAX = 1_500
COMPACT_HANDOFF_LINES = 15
NUDGE = ("run /mem-setup native (one-time) so memory loads through MEMORY.md "
         "(25 KB, re-attached after every compaction) instead of this capped bootstrap")
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _stdin_event() -> dict:
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    try:
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _resolve_ws(cwd: str) -> str:
    try:
        return active_workspace(Path(cwd)) if cwd else active_workspace()
    except Exception:
        return active_workspace()


def mode(ws: str, cwd: str, settings: dict) -> str:
    """'native' when this project loads <ws>/memory/MEMORY.md through Claude
    Code's auto memory; else 'fallback'."""
    if not setting("native.enabled", bool, True, settings=settings):
        return "fallback"
    if not cwd:
        return "fallback"
    try:
        from _memfile import memfile_path  # type: ignore
        from _native import is_wired  # type: ignore
        if is_wired(Path(cwd), ws) and memfile_path(ws).is_file():
            return "native"
    except Exception as exc:
        log_debug("bootstrap-load", f"mode check failed: {exc}")
    return "fallback"


def _handoff_date(ws: str) -> str:
    try:
        from _handoff import digest  # type: ignore
        for ln in digest(ws, max_lines=5):
            m = _DATE_RE.search(ln)
            if m:
                return m.group(1)
    except Exception:
        pass
    return ""


def _header(ws: str, mode_: str) -> str:
    lines = [f"[gowth-mem:bootstrap workspace={ws}{version_tag()}]"]
    try:
        nudge = drift_nudge()
    except Exception:
        nudge = ""
    if nudge.strip():
        lines.append(nudge.strip())
    if mode_ == "native":
        try:
            from _memfile import topic_index  # type: ignore
            n = len(topic_index(ws, max_lines=10_000))
        except Exception:
            n = 0
        date = _handoff_date(ws) or "n/a"
        lines.append(f"memory: MEMORY.md is attached by Claude Code ({n} topics, handoff {date}); "
                     f"related entries are recalled automatically per prompt — /mem-recall <query> for more")
    return "\n".join(lines)


def emit(ws: str, source: str, mode_: str, settings: dict) -> str:
    """The additionalContext for one SessionStart event ('' = print nothing)."""
    if source == "resume":
        return ""
    if mode_ == "native":
        head = _header(ws, mode_)
        if source == "compact":
            try:
                from _handoff import digest  # type: ignore
                lines = [head, "## Handoff (newest)"] + digest(ws, max_lines=COMPACT_HANDOFF_LINES)
            except Exception as exc:
                log_debug("bootstrap-load", f"compact digest failed: {exc}")
                lines = [head]
            return clamp_context("\n".join(lines), COMPACT_MAX)
        return clamp_context(head, HEADER_MAX)

    from _memfile import render_hook_bootstrap  # type: ignore
    body = render_hook_bootstrap(ws)
    parts = [body.rstrip("\n")]
    try:
        layout = int(settings.get("layout_version", 0) or 0)
    except Exception:
        layout = 0
    if layout < 3:
        parts.append("layout_version < 3: run /mem-ops migrate-v3 to move this vault to the v3 topic layout")
    parts.append(NUDGE)
    return clamp_context("\n".join(parts), HOOK_CONTEXT_MAX)


def _spawn_incremental_index() -> None:
    script = Path(__file__).parent / "_index.py"
    if not script.is_file():
        return
    try:
        subprocess.Popen([sys.executable, str(script), "--incremental"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, start_new_session=True)
    except Exception as exc:
        log_debug("bootstrap-load", f"incremental index spawn failed: {exc}")


def prepare(ws: str, settings: dict) -> None:
    """Startup side effects: workspace.json, MEMORY.md refresh, detached reindex.
    Each best-effort; the emission never depends on them."""
    try:
        from _workspace import ensure_workspace_json  # type: ignore
        ensure_workspace_json(ws)
    except Exception as exc:
        log_debug("bootstrap-load", f"ensure_workspace_json failed: {exc}")
    if setting("native.enabled", bool, True, settings=settings):
        try:
            from _memfile import memfile_path, sources_changed, write  # type: ignore
            if not memfile_path(ws).is_file() or sources_changed(ws):
                write(ws)
        except Exception as exc:
            log_debug("bootstrap-load", f"memfile refresh failed: {exc}")
    _spawn_incremental_index()


def _report(ws: str, cwd: str, settings: dict) -> int:
    from _memfile import (  # type: ignore
        MAX_CHARS, MAX_LINES, memfile_path, render, render_hook_bootstrap, split)
    mode_ = mode(ws, cwd, settings)
    block = render(ws)
    print(f"bootstrap (v4.8) workspace={ws} mode={mode_} cwd={cwd or '(none)'}")
    p = memfile_path(ws)
    free_lines = 0
    if p.is_file():
        free_lines = split(p.read_text(errors="ignore"))[1].count("\n")
    print(f"memfile: {p} — block {block.count(chr(10))} lines / {len(block)} chars "
          f"(budget {MAX_LINES} lines / {MAX_CHARS} chars), free zone {free_lines} lines, "
          f"{'present' if p.is_file() else 'MISSING'}")
    section = None
    counts: dict = {}
    for ln in block.splitlines():
        if ln.startswith("## "):
            section = ln[3:]
            counts[section] = 0
        elif section:
            counts[section] += 1
    for name, n in counts.items():
        print(f"  {name:<24} {n:>4} lines")
    start = emit(ws, "startup", mode_, settings)
    comp = emit(ws, "compact", mode_, settings)
    print(f"emission: startup {len(start)} chars (≈{len(start) // 4} tok), compact {len(comp)} chars; "
          f"hook cap {HOOK_CONTEXT_MAX}, host persists at 10,000")
    print(f"fallback bootstrap: {len(render_hook_bootstrap(ws))} chars")
    # v4.8 per-prompt recall telemetry (state.json, written only when something was injected)
    try:
        from _home import state_path  # type: ignore
        st = json.loads(state_path().read_text()) if state_path().is_file() else {}
        prompts = entries = chars = 0
        for sess in (st.get("session") or {}).values():
            r = sess.get("recall") if isinstance(sess, dict) else None
            if isinstance(r, dict):
                prompts += int(r.get("injected", 0) or 0)
                entries += int(r.get("entries", 0) or 0)
                chars += int(r.get("chars", 0) or 0)
        print(f"recall: {prompts} prompts got memory, {entries} entries, {chars} chars "
              f"(≈{chars // 4} tok) across sessions in state.json")
        # review M13: every profiled prompt is counted per day, injecting or not
        daily = st.get("recall_daily") if isinstance(st.get("recall_daily"), dict) else {}
        seen = sum(int((d or {}).get("prompts", 0) or 0) for d in daily.values() if isinstance(d, dict))
        got = sum(int((d or {}).get("injected", 0) or 0) for d in daily.values() if isinstance(d, dict))
        if seen:
            print(f"recall rate: {got}/{seen} prompts injected ({100 * got // seen}%) over "
                  f"{len(daily)} day(s) of recall_daily")
    except Exception as exc:
        print(f"recall: (no telemetry: {exc})")
    if mode_ != "native":
        print("native memory not wired for this project: run /mem-setup native")
    return 0


def main() -> int:
    try:
        ev = _stdin_event()
        source = str(ev.get("source") or "")
        cwd = str(ev.get("cwd") or "")
        settings = read_settings()
        ws = _resolve_ws(cwd)

        if "--report" in sys.argv[1:]:
            return _report(ws, cwd or str(Path.cwd()), settings)

        if source in ("startup", "clear", ""):
            prepare(ws, settings)
        mode_ = mode(ws, cwd, settings)
        context = emit(ws, source, mode_, settings)
        if not context.strip():
            return 0
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                                 "additionalContext": context}}))
        log_debug("bootstrap-load", f"source={source or 'empty'} mode={mode_} chars={len(context)}")
        return 0
    except Exception as exc:
        log_debug("bootstrap-load", f"unhandled error: {exc}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
