#!/usr/bin/env python3
"""PreCompact hook (v2.3): block until critical info is flushed to topics.

Routes destinations for topic-organized memory at ~/.gowth-mem/, scoped to the
active workspace. Topic files live at workspace root (no `topics/` wrapper);
reserved subdirs (docs, journal, skills) hold cross-cutting registries.

Idempotency (v2.9.2): once the LLM has flushed (writes a markdown file under the
active workspace), a follow-up /compact within FLUSH_GRACE seconds passes silently
so the user isn't trapped in a perpetual block.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _home import active_workspace, workspace_dir  # type: ignore

FLUSH_GRACE = 300  # seconds — recent flush window


REASON = """[gowth-mem:precompact-flush] HARD-BLOCK: compact incoming. Save EVERYTHING critical first.

Active workspace = the one resolved at session start. All writes below land under
~/.gowth-mem/workspaces/<active>/ unless explicitly cross-workspace ([[ws:slug]] / shared/).

Before context is summarized, do this WITHOUT user prompting:

1. Scan this entire conversation for high-signal info that hasn't been saved yet.
2. Route each item (apply mem0 ADD/UPDATE/DELETE/NOOP):
   - Cross-cutting per workspace (flat under <ws>/docs/):
     - Session state (current task / next / blocker) → <ws>/docs/handoff.md (prefix host:<name>)
     - Cross-topic tool quirks (ws-specific) → <ws>/docs/tools.md
     - Cross-topic episodic / fact overflow → <ws>/docs/exp.md / <ws>/docs/ref.md
   - Cross-workspace (flat under shared/):
     - Resource pointers (env-var name only, NEVER value) → shared/secrets.md
     - System-wide tools (kubectl, frida, …) → shared/tools.md
   - Topic content — pick or create the right topic file at workspace root:
     - Find existing <ws>/**/<slug>.md (excluding docs/journal/skills) whose keywords overlap
       (≥3 common words). Append there.
     - Else create <ws>/<new-slug>.md (top-2 distinctive keywords as slug, kebab-case ≤60).
     - Reserved names blocked: docs, journal, skills, _MAP.md, AGENTS.md, workspace.json.
     - Episodic experience → `## [exp]` section, line `- ...`
     - Verified fact (Source REQUIRED) → `## [ref]` section
     - Tool quirk specific to this topic → `## [ref]` section
     - Architectural decision → `## [decision]` section
     - Lesson learned → `## [exp]` section (reflection group)
3. Append raw observations to <ws>/journal/<today>.md if useful.
4. Update <ws>/docs/handoff.md so the next session can resume.
5. After writes: refresh MOC + index:
   `python3 ${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_moc.py --ws <ws>`
   `python3 ${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_index.py`
6. Confirm in one line: "precompact-flush: ws=<ws>, saved N items into <topics+docs>".

Quy tắc: 1-2 dòng / entry, có Source cho [ref]. Conflict cũ → DELETE cũ. KHÔNG commit secret value.
Frontmatter.last_touched phải update theo today. Slug đã publish thì KHÔNG đổi (vỡ wikilinks).

After this turn, the PostCompact hook will pull-rebase-push automatically.
Once saved (any *.md modified under the active workspace), re-run /compact within
5 minutes — this hook detects the recent flush via mtime and lets it through."""


def recently_flushed(grace: int = FLUSH_GRACE) -> bool:
    """True if any markdown under the active workspace was modified within
    `grace` seconds — heuristic that the LLM just completed a flush."""
    try:
        wsd = workspace_dir(active_workspace())
    except Exception:
        return False
    if not wsd.is_dir():
        return False
    cutoff = time.time() - grace
    for p in wsd.rglob("*.md"):
        try:
            if p.stat().st_mtime > cutoff:
                return True
        except OSError:
            continue
    return False


def main() -> int:
    if recently_flushed():
        return 0  # silent approve — recent flush detected, don't trap the user
    print(json.dumps({"decision": "block", "reason": REASON}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
