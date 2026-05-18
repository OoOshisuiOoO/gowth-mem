#!/usr/bin/env python3
"""gowth-mem dreaming orchestrator — wraps _consolidate.py 3-phase pipeline.

Adapted from OpenClaw's dreaming architecture (Light → REM → Deep).
Provides a user-facing entry point for the consolidation pipeline that
_consolidate.py defines but never wired to a command.

Pure stdlib Python 3.9+. No pip deps.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _home import gowth_home, list_workspaces  # type: ignore
from _lock import file_lock  # type: ignore
from _consolidate import (  # type: ignore
    light_phase,
    rem_phase,
    deep_phase,
    _load_state,
)


def _print_progress(msg: str) -> None:
    """Emit progress to stderr so stdout stays parseable JSON."""
    print(msg, file=sys.stderr, flush=True)


def _filter_state_to_ws(state: dict, ws: str | None) -> dict:
    """Return a shallow copy of `state` with `files` restricted to workspaces/<ws>/.

    `state["files"]` is keyed by paths relative to ~/.gowth-mem/. Anything outside
    `workspaces/<ws>/` (shared/, other workspaces, top-level state) is dropped.
    `ws is None` returns the state unchanged.
    """
    if ws is None or not isinstance(state, dict):
        return state
    files = state.get("files") or {}
    if not isinstance(files, dict):
        return state
    prefix = f"workspaces/{ws}/"
    filtered = {k: v for k, v in files.items() if isinstance(k, str) and k.startswith(prefix)}
    new_state = dict(state)
    new_state["files"] = filtered
    return new_state


def _run_phases(
    ws: str | None,
    *,
    light: bool,
    rem: bool,
    deep: bool,
    dry_run: bool,
) -> dict:
    """Run enabled phases for a single workspace. Returns per-phase result dict."""
    phase_results: dict[str, dict] = {}

    # ── Load state under lock ────────────────────────────────────────────
    try:
        with file_lock("state", timeout=5.0):
            state = _load_state()
    except TimeoutError:
        _print_progress(f"  [dream] warn: state lock timeout for ws={ws!r}; reading unguarded")
        state = _load_state()

    # v3.4: filter state["files"] to this workspace only (P1 fix from critic).
    state = _filter_state_to_ws(state, ws)

    candidates: list = []

    # ── Light phase ──────────────────────────────────────────────────────
    if not light:
        phase_results["light"] = {"skipped": True, "files_processed": 0, "duration_s": 0.0}
        _print_progress(f"  [dream/light] skipped (--no-light)")
    else:
        _print_progress(f"  [dream/light] starting")
        t0 = time.perf_counter()
        try:
            candidates = light_phase(state)
            duration = round(time.perf_counter() - t0, 4)
            phase_results["light"] = {
                "skipped": False,
                "files_processed": len(candidates),
                "duplicates_collapsed": 0,
                "duration_s": duration,
            }
            _print_progress(f"  [dream/light] done — {len(candidates)} candidates ({duration}s)")
        except Exception as exc:
            duration = round(time.perf_counter() - t0, 4)
            phase_results["light"] = {
                "skipped": False,
                "error": str(exc),
                "files_processed": 0,
                "duplicates_collapsed": 0,
                "duration_s": duration,
            }
            _print_progress(f"  [dream/light] ERROR: {exc}")

    # ── REM phase ────────────────────────────────────────────────────────
    if not rem:
        phase_results["rem"] = {"skipped": True, "themes_found": 0, "files_processed": 0, "duration_s": 0.0}
        _print_progress(f"  [dream/rem] skipped (--no-rem)")
    else:
        _print_progress(f"  [dream/rem] starting")
        t0 = time.perf_counter()
        try:
            themes = rem_phase(candidates)
            duration = round(time.perf_counter() - t0, 4)
            total_files = sum(len(v) for v in themes.values())
            phase_results["rem"] = {
                "skipped": False,
                "themes_found": len(themes),
                "files_processed": total_files,
                "duration_s": duration,
            }
            _print_progress(f"  [dream/rem] done — {len(themes)} themes, {total_files} files ({duration}s)")
        except Exception as exc:
            duration = round(time.perf_counter() - t0, 4)
            phase_results["rem"] = {
                "skipped": False,
                "error": str(exc),
                "themes_found": 0,
                "files_processed": 0,
                "duration_s": duration,
            }
            _print_progress(f"  [dream/rem] ERROR: {exc}")

    # ── Deep phase ───────────────────────────────────────────────────────
    if not deep:
        phase_results["deep"] = {"skipped": True, "promoted": 0, "maintained": 0, "prune_candidates": 0, "duration_s": 0.0}
        _print_progress(f"  [dream/deep] skipped (--no-deep)")
    else:
        _print_progress(f"  [dream/deep] starting")
        t0 = time.perf_counter()
        try:
            rankings = deep_phase(candidates)
            duration = round(time.perf_counter() - t0, 4)
            promoted = len(rankings.get("promote", []))
            maintained = len(rankings.get("maintain", []))
            prune_count = len(rankings.get("prune_candidates", []))
            phase_results["deep"] = {
                "skipped": False,
                "promoted": promoted,
                "maintained": maintained,
                "prune_candidates": prune_count,
                "duration_s": duration,
            }
            _print_progress(
                f"  [dream/deep] done — promote={promoted} maintain={maintained} "
                f"prune_candidates={prune_count} ({duration}s)"
            )
        except Exception as exc:
            duration = round(time.perf_counter() - t0, 4)
            phase_results["deep"] = {
                "skipped": False,
                "error": str(exc),
                "promoted": 0,
                "maintained": 0,
                "prune_candidates": 0,
                "duration_s": duration,
            }
            _print_progress(f"  [dream/deep] ERROR: {exc}")

    return phase_results


def _build_summary(ws: str | None, phases: dict, dry_run: bool) -> str:
    light = phases.get("light", {})
    rem = phases.get("rem", {})
    deep = phases.get("deep", {})

    ws_label = ws or "active"
    parts = [f"Dream run on workspace '{ws_label}'."]

    if light.get("skipped"):
        parts.append("Light phase: skipped.")
    elif "error" in light:
        parts.append(f"Light phase: failed ({light['error']}).")
    else:
        parts.append(f"Light phase: {light.get('files_processed', 0)} candidate files gathered.")

    if rem.get("skipped"):
        parts.append("REM phase: skipped.")
    elif "error" in rem:
        parts.append(f"REM phase: failed ({rem['error']}).")
    else:
        parts.append(
            f"REM phase: {rem.get('themes_found', 0)} keyword themes across "
            f"{rem.get('files_processed', 0)} files."
        )

    if deep.get("skipped"):
        parts.append("Deep phase: skipped.")
    elif "error" in deep:
        parts.append(f"Deep phase: failed ({deep['error']}).")
    else:
        parts.append(
            f"Deep phase: {deep.get('promoted', 0)} files promoted, "
            f"{deep.get('maintained', 0)} maintained, "
            f"{deep.get('prune_candidates', 0)} flagged for pruning."
        )

    if dry_run:
        parts.append("Dry-run: no files were written.")

    return " ".join(parts)


def run(
    ws: str | None = None,
    *,
    light: bool = True,
    rem: bool = True,
    deep: bool = True,
    dry_run: bool = False,
) -> dict:
    """Run dreaming consolidation on a workspace (or all workspaces if ws is None).

    Returns:
        {
            "workspace": ws,        # None = all workspaces
            "phases": {
                "light": {"skipped": bool, "files_processed": N, "duplicates_collapsed": M, "duration_s": X},
                "rem":   {"skipped": bool, "themes_found": N, "files_processed": M, "duration_s": X},
                "deep":  {"skipped": bool, "promoted": N, "maintained": M, "prune_candidates": K, "duration_s": X},
            },
            "summary": "human-readable 1-paragraph summary",
            "dry_run": bool,
        }
    """
    gh = gowth_home()
    if not gh.is_dir():
        _print_progress("[dream] ~/.gowth-mem not found — nothing to do")
        return {
            "workspace": ws,
            "phases": {
                "light": {"skipped": True, "files_processed": 0, "duration_s": 0.0},
                "rem": {"skipped": True, "themes_found": 0, "files_processed": 0, "duration_s": 0.0},
                "deep": {"skipped": True, "promoted": 0, "maintained": 0, "prune_candidates": 0, "duration_s": 0.0},
            },
            "summary": "Dream skipped: ~/.gowth-mem directory not found.",
            "dry_run": dry_run,
        }

    # ── Single workspace path ────────────────────────────────────────────
    if ws is not None:
        _print_progress(f"[dream] workspace={ws!r} dry_run={dry_run}")
        lock_name = f"dream-{ws}"
        try:
            with file_lock(lock_name, timeout=3.0):
                phases = _run_phases(ws, light=light, rem=rem, deep=deep, dry_run=dry_run)
        except TimeoutError:
            _print_progress(f"[dream] workspace {ws!r} already dreaming — skipped")
            phases = {
                "light": {"skipped": True, "files_processed": 0, "duration_s": 0.0},
                "rem": {"skipped": True, "themes_found": 0, "files_processed": 0, "duration_s": 0.0},
                "deep": {"skipped": True, "promoted": 0, "maintained": 0, "prune_candidates": 0, "duration_s": 0.0},
            }

        return {
            "workspace": ws,
            "phases": phases,
            "summary": _build_summary(ws, phases, dry_run),
            "dry_run": dry_run,
        }

    # ── All workspaces path ──────────────────────────────────────────────
    workspace_list = list_workspaces()
    _print_progress(f"[dream] all workspaces: {workspace_list or ['(none found)']}")

    if not workspace_list:
        empty_phases = {
            "light": {"skipped": True, "files_processed": 0, "duration_s": 0.0},
            "rem": {"skipped": True, "themes_found": 0, "files_processed": 0, "duration_s": 0.0},
            "deep": {"skipped": True, "promoted": 0, "maintained": 0, "prune_candidates": 0, "duration_s": 0.0},
        }
        return {
            "workspace": None,
            "phases": empty_phases,
            "summary": "Dream skipped: no workspaces found.",
            "dry_run": dry_run,
        }

    # Aggregate phases across all workspaces
    agg: dict[str, dict] = {
        "light": {"skipped": False, "files_processed": 0, "duplicates_collapsed": 0, "duration_s": 0.0},
        "rem":   {"skipped": False, "themes_found": 0, "files_processed": 0, "duration_s": 0.0},
        "deep":  {"skipped": False, "promoted": 0, "maintained": 0, "prune_candidates": 0, "duration_s": 0.0},
    }

    for w in workspace_list:
        _print_progress(f"[dream] workspace={w!r}")
        lock_name = f"dream-{w}"
        try:
            with file_lock(lock_name, timeout=3.0):
                phases = _run_phases(w, light=light, rem=rem, deep=deep, dry_run=dry_run)
        except TimeoutError:
            _print_progress(f"[dream] workspace {w!r} already dreaming — skipped")
            continue

        for phase_key, acc in agg.items():
            p = phases.get(phase_key, {})
            if p.get("skipped"):
                continue
            if "error" in p:
                continue
            if phase_key == "light":
                acc["files_processed"] += p.get("files_processed", 0)
                acc["duplicates_collapsed"] += p.get("duplicates_collapsed", 0)
                acc["duration_s"] += p.get("duration_s", 0.0)
            elif phase_key == "rem":
                acc["themes_found"] += p.get("themes_found", 0)
                acc["files_processed"] += p.get("files_processed", 0)
                acc["duration_s"] += p.get("duration_s", 0.0)
            elif phase_key == "deep":
                acc["promoted"] += p.get("promoted", 0)
                acc["maintained"] += p.get("maintained", 0)
                acc["prune_candidates"] += p.get("prune_candidates", 0)
                acc["duration_s"] += p.get("duration_s", 0.0)

    # Round accumulated durations
    for acc in agg.values():
        acc["duration_s"] = round(acc["duration_s"], 4)

    if not light:
        agg["light"]["skipped"] = True
    if not rem:
        agg["rem"]["skipped"] = True
    if not deep:
        agg["deep"]["skipped"] = True

    return {
        "workspace": None,
        "phases": agg,
        "summary": _build_summary(None, agg, dry_run),
        "dry_run": dry_run,
    }


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="gowth-mem dreaming orchestrator — Light/REM/Deep consolidation pipeline"
    )
    ap.add_argument("--ws", default=None, help="Workspace name (omit = all workspaces)")
    ap.add_argument("--no-light", action="store_true", help="Skip Light phase")
    ap.add_argument("--no-rem", action="store_true", help="Skip REM phase")
    ap.add_argument("--no-deep", action="store_true", help="Skip Deep phase")
    ap.add_argument("--dry-run", action="store_true", help="Report without writing")
    ap.add_argument("--json", action="store_true", default=True,
                    help="Output JSON (default: true)")
    args = ap.parse_args()

    result = run(
        args.ws,
        light=not args.no_light,
        rem=not args.no_rem,
        deep=not args.no_deep,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
