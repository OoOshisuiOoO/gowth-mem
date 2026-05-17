#!/usr/bin/env python3
"""Stop hook (v2.2): auto-distill + auto-prune every N user turns, scoped to the
active workspace.

State lives in global ~/.gowth-mem/state.json (per-machine, gitignored).
Updates are protected by file_lock("state") for multi-session safety.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import atomic_write  # type: ignore
from _debug import log_debug  # type: ignore
from _home import active_workspace, gowth_home, list_workspaces, state_path  # type: ignore
from _lock import file_lock  # type: ignore

AUTO_DISTILL_EVERY = 10


def _load_state() -> dict:
    p = state_path()
    if not p.is_file():
        return {"version": 2, "files": {}, "session": {}}
    try:
        d = json.loads(p.read_text())
        d.setdefault("files", {})
        d.setdefault("session", {})
        return d
    except Exception as e:
        log_debug("auto-journal", f"load_state failed: {e}")
        return {"version": 2, "files": {}, "session": {}}


def _save_state(state: dict) -> None:
    try:
        gowth_home().mkdir(parents=True, exist_ok=True)
        atomic_write(state_path(), json.dumps(state, indent=2))
    except Exception as e:
        log_debug("auto-journal", f"save_state failed: {e}")


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    session_id = data.get("session_id") or "default"

    try:
        with file_lock("state", timeout=5.0):
            state = _load_state()
            sess = state["session"].setdefault(session_id, {"turn_count": 0})
            sess["turn_count"] = sess.get("turn_count", 0) + 1
            turn = sess["turn_count"]
            _save_state(state)
    except TimeoutError as e:
        log_debug("auto-journal", f"state lock timeout (increment): {e}")
        print(json.dumps({"continue": True, "suppressOutput": True}))
        return 0

    if turn < AUTO_DISTILL_EVERY:
        print(json.dumps({"continue": True, "suppressOutput": True}))
        return 0

    try:
        with file_lock("state", timeout=5.0):
            state = _load_state()
            state["session"].setdefault(session_id, {"turn_count": 0})["turn_count"] = 0
            _save_state(state)
    except TimeoutError as e:
        log_debug("auto-journal", f"state lock timeout (reset): {e}")

    prune_summary = ""
    prune_script = Path(__file__).parent / "_prune.py"
    if prune_script.is_file():
        try:
            r = subprocess.run(
                ["python3", str(prune_script), "--all-workspaces"],
                capture_output=True, text=True, timeout=8,
            )
            if r.stdout:
                prune_summary = r.stdout.strip().splitlines()[0][:500]
        except subprocess.TimeoutExpired as e:
            log_debug("auto-journal", f"prune subprocess timeout after 8s: {e}")
        except Exception as e:
            log_debug("auto-journal", f"prune subprocess failed: {e}")

    consolidation_summary = ""
    consolidate_script = Path(__file__).parent / "_consolidate.py"
    if consolidate_script.is_file():
        try:
            r = subprocess.run(
                ["python3", str(consolidate_script)],
                capture_output=True, text=True, timeout=8,
            )
            if r.stdout:
                consolidation_summary = r.stdout.strip()[:500]
        except subprocess.TimeoutExpired as e:
            log_debug("auto-journal", f"consolidate subprocess timeout after 8s: {e}")
        except Exception as e:
            log_debug("auto-journal", f"consolidate subprocess failed: {e}")

    ws = active_workspace()
    all_ws = list_workspaces()
    other_ws = [w for w in all_ws if w != ws]
    ws_list_str = ", ".join(other_ws) if other_ws else "(none)"
    reason = f"""[gowth-mem:auto-journal ws={ws}] {AUTO_DISTILL_EVERY} turns elapsed.

Pre-block prune ran (all workspaces): {prune_summary or '(no prune output)'}
Pre-block consolidation: {consolidation_summary or '(no consolidation data)'}

Active workspace: {ws}
Other workspaces: {ws_list_str}

Now do this WITHOUT user prompting before yielding control:

1. Scan the last {AUTO_DISTILL_EVERY} user turns and your replies.
2. For each high-signal item, classify into ONE of these types and prepend the prefix:
   [decision]    choice + rationale          → workspaces/<target_ws>/<slug>.md  (## [decision])
   [exp]         debug / fix / lesson         → workspaces/<target_ws>/<slug>.md  (## [exp])
   [reflection]  pattern / takeaway           → workspaces/<target_ws>/<slug>.md  (## [exp])
   [ref]         verified external fact       → workspaces/<target_ws>/<slug>.md  (## [ref], Source REQUIRED)
   [tool]        topic-specific gotcha        → workspaces/<target_ws>/<slug>.md  OR  workspaces/<target_ws>/docs/tools.md
   [secret-ref]  env-var POINTER              → shared/secrets.md  (NEVER value)
3. **Workspace routing**: Route each entry to the workspace that best matches its topic:
   - {ws} (active) = default target for entries about the current session's work
   - {ws_list_str} = route here if entry clearly belongs to that workspace's domain
   - shared/ = cross-workspace resources (secrets, tools)
   Topic routing within a workspace: pick existing workspaces/<target_ws>/**/<slug>.md
   (excluding docs/journal/skills) if keywords overlap (≥3 common words);
   otherwise create new workspaces/<target_ws>/<new-slug>.md
   (file-per-topic at workspace root, with v2.3 frontmatter:
   slug/title/status:draft/created/last_touched/parents:[]/links:[]/aliases:[]).
   Reserved names cannot be used as slug or domain folder: docs, journal, skills, _MAP.md, AGENTS.md, workspace.json.
   Lazy-nest into domain folders only when ≥5 topics share a theme: workspaces/<ws>/<domain>/<sub>/<slug>.md (≤3 cấp).
4. Apply quality gates — DROP if:
   - Entry < 20 chars
   - Code-only (no prose)
   - [ref] without Source
   - Vague / hedged ("maybe", "I think") without backing
5. Apply mem0 ADD / UPDATE / DELETE / NOOP against existing target file content.
   Update frontmatter.last_touched on every write.
6. Update workspaces/{ws}/docs/handoff.md (prefix host:<machine>) with new task / next / blocker.
7. After writes, run `_moc.py --ws <target_ws>` for each workspace that received writes.
8. Confirm in 1 line: "auto-journal: ws={ws}(+others), kept N, dropped M, promoted K, conflicts resolved J".

Don't write the user a long message about this — just do the work silently and continue.
This is automation, not a conversation step."""

    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
