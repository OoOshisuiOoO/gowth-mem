#!/usr/bin/env python3
"""UserPromptSubmit hook (v2.0): keyword shortcuts + intent-driven auto-skill.

Shortcuts (must appear at start of prompt):

  mems   →  mem-save             (save current memory entry to topic+docs)
  memd   →  mem-distill          (distill journal → topics)
  memr   →  mem-reflect          (recap / reflections)
  memk   →  mem-skillify         (extract reusable workflow → skills/)
  memb   →  mem-bootstrap        (3-line: doing what / next / blocker)
  memh   →  mem-hyde-recall      (HyDE for conceptual queries)
  memj   →  mem-journal          (open today's journal)
  memx   →  mem-reindex          (rebuild SQLite index)
  memc   →  mem-cost             (estimate bootstrap token footprint)
  memp   →  mem-prune             (active delete outdated)
  memy   →  mem-sync             (git pull/push)
  memg   →  mem-config           (set up remote+token)
  memm   →  mem-migrate-global   (v1.0 → v2.0 migration)
  memT   →  mem-topic            (list / inspect topics)
  memI   →  mem-install          (first-time setup wizard)
  memC   →  mem-sync-resolve     (AI conflict resolution)
  memL   →  mem-lesson           (capture experience: symptom -- tried -- root -- fix [-- source])

@-shortcuts: @today @yesterday @user
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# NOTE: AGENTS.md is loaded ONCE at SessionStart by bootstrap-load.py (which
# already reads shared/AGENTS.md + workspaces/<ws>/AGENTS.md). We deliberately
# do NOT re-inject Rules every turn here — that was wasteful (~6k tokens/turn
# duplicate). See user decision 2026-05-06: "Option B" (rules at SessionStart only).


INLINE_MEM_SAVE = """[auto-skill: mem-save] Intent = save. Execute inline (no /mem-save needed):

Routing table (1-2 lines / entry, Source required for [ref]):
| Episodic experience (debug, fix, lesson, surprise)  | <slug>.md (at workspace root)  - [exp] |
| Verified semantic fact (with Source URL)            | <slug>.md (at workspace root)  - [ref] |
| Topic-specific tool quirk                           | <slug>.md (at workspace root)  - [tool] |
| Architectural decision                              | <slug>.md (at workspace root)  - [decision] |
| Lesson / takeaway / pattern                         | <slug>.md (at workspace root)  - [reflection] |
| Cross-topic tool quirk                              | docs/tools.md |
| Resource pointer (env-var name; NEVER value)        | docs/secrets.md |
| Session state (host: prefix; current/next/blocker)  | docs/handoff.md |
| Workflow done 2+ times                              | skills/<name>.md (use memk) |

Topic routing:
- Find existing <slug>.md (at workspace root) whose keywords overlap (≥3 common words). Append there.
- Else create <new-slug>.md (at workspace root) from top-2 distinctive keywords (kebab-case ≤40 chars).
- Use python3 ${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_topic.py --route '<text>' to compute slug.

Apply mem0 ADD / UPDATE / DELETE / NOOP. Conflict with existing → drop old. Confirm path written."""

INLINE_MEM_DISTILL = """[auto-skill: mem-distill] Intent = distill. Execute inline (no /mem-distill needed):

1. Read journal/<today>.md and <yesterday>.md (under ~/.gowth-mem/).
2. For each high-signal entry: route to a topic (use _topic.py) or to docs/{handoff,secrets,tools}.md.
3. Apply mem0 ADD / UPDATE / DELETE / NOOP against the target file.
4. Mark distilled journal entries with `(distilled)` suffix.
5. Report: ADD N, UPDATE M, DELETE K, NOOP J, DROP D, LEFT L."""

INLINE_MEM_REFLECT = """[auto-skill: mem-reflect] Intent = reflect/recap. Execute inline (no /mem-reflect needed):

1. Read journal/*.md from last 7 days + recent workspace topic files.
2. Score entries by importance × recency × novelty.
3. Pick top 3 patterns (clusters of related entries).
4. Append to the matching <slug>.md (at workspace root) as `- [reflection] ...` with:
   ### YYYY-MM-DD: <title>
   **Claim**: <evergreen 1-line>
   **Evidence**: <file:line refs (≥2)>
   **Implication**: <1-line action>
NEVER invent — every reflection cites ≥2 source entries."""

INLINE_MEM_SKILLIFY = """[auto-skill: mem-skillify] Intent = extract reusable skill. Execute inline:

1. Identify the recurring workflow's core steps (parameterize variables).
2. Pick a kebab-case <name> ≤30 chars.
3. Write ~/.gowth-mem/skills/<name>.md with frontmatter (name, description, created, inputs)
   and sections: Description / Steps (parameterized) / Variations / Token cost / Source.
4. Confirm path. Suggest invocation: `do <name> for <input>`."""

INLINE_MEM_BOOTSTRAP = """[auto-skill: mem-bootstrap] Intent = where-am-I / status. Execute inline:

Read ~/.gowth-mem/docs/handoff.md (filter by host:<this-machine> if multiple hosts).
Emit EXACTLY 3 lines for the user:
1. **doing**: <Current task — 1 line>
2. **next**: <Next step — 1 line>
3. **blocker**: <Blocker — 1 line, or `none`>

If handoff.md is missing/empty, say so and suggest /mem-install."""

INLINE_MEM_HYDE = """[auto-skill: mem-hyde-recall] Intent = HyDE recall (conceptual query). Execute inline:

1. Draft a 1-2 paragraph hypothetical answer (just for retrieval).
2. If ~/.gowth-mem/index.db + sqlite-vec + embedding key all available:
   embed the hypothetical, vector top-K, RRF-merge with FTS5 BM25 over the original prompt.
3. Else: extract ≥5-char keywords from the hypothetical and grep workspaces/<active>/**/*.md (excl reserved) + <ws>/docs/*.md + shared/*.md.
4. Filter temporal-invalid lines (`(superseded)`, expired `valid_until:`).
5. Synthesize against the original question, citing each chunk like `workspaces/personal/python-venv.md § Lessons`.
6. If no useful match: suggest /mem-reindex."""

INLINE_MEM_JOURNAL = """[auto-skill: mem-journal] Intent = open today's journal. Execute inline:

1. mkdir -p ~/.gowth-mem/journal.
2. If journal/<today>.md missing: copy from ${CLAUDE_PLUGIN_ROOT}/templates/journal-day.md, replace YYYY-MM-DD.
3. Show current contents.
4. Ask user what to log (Logs / Wins / Pains / Questions). Append with HH:MM prefix for Logs."""

INLINE_MEM_REINDEX = """[auto-skill: mem-reindex] Intent = rebuild search index. Execute inline:

Run: python3 ${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_index.py
- Default: incremental (only re-indexes changed mtimes).
- Pass --full to drop and rebuild.
Indexes ~/.gowth-mem/shared/*.md + workspaces/<ws>/{docs,journal,skills}/**/*.md + workspaces/<ws>/**/*.md (topic root, excl reserved) into ~/.gowth-mem/index.db.
Report files / chunks indexed and which fallback (FTS5-only vs vector hybrid)."""

INLINE_MEM_COST = """[auto-skill: mem-cost] Intent = estimate bootstrap token footprint. Execute inline:

Sum char count of: AGENTS.md + shared/{files,secrets,tools}.md + workspaces/<active>/_MAP.md +
<ws>/docs/{handoff,exp,ref,tools,files}.md + top-3 most-recent <ws>/**/*.md (topics) +
<ws>/journal/<today>.md + <yesterday>.md.
Estimate tokens = chars / 4. Print per-file breakdown + total.
Cap = 60,000 chars (~15,000 tokens). Warn if >40k or >60k."""

INLINE_MEM_PRUNE = """[auto-skill: mem-prune] Intent = actively DELETE outdated entries. Execute inline:

Run: python3 ${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_prune.py
Pass --dry-run first if user wants preview.

Deletion rules (in order):
1. Entry with `valid_until: YYYY-MM-DD` past today → DELETE
2. Entry with `(superseded)` / `(deprecated)` / `(obsolete)` → DELETE
3. Within-file Jaccard ≥ 0.85 duplicate → DELETE the SHORTER

Walks ~/.gowth-mem/workspaces/<ws>/**/*.md (excl reserved) + <ws>/docs/*.md + shared/*.md. Skips journal/. Report: deleted N, kept K."""

INLINE_MEM_SYNC = """[auto-skill: mem-sync] Intent = sync ~/.gowth-mem/ via git remote. Execute inline:

Pre-req: ~/.gowth-mem/config.json with `remote`+`branch` (use /mem-config). Token via env GOWTH_MEM_GIT_TOKEN preferred.

Run: python3 ${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_sync.py
Flags: --init (first-time) | --pull-only | --push-only

What syncs: AGENTS.md, settings.json, shared/*, workspaces/<ws>/{_MAP, workspace.json, docs, journal, skills, <slug>.md, <domain>/}.
Gitignored (per-machine): config.json, state.json, index.db, .locks/.

On conflict: writes ~/.gowth-mem/SYNC-CONFLICT.md and exits 2.
Run /mem-sync-resolve to walk through it."""

INLINE_MEM_CONFIG = """[auto-skill: mem-config] Intent = set up ~/.gowth-mem/config.json for git sync. Execute inline:

1. Ensure ~/.gowth-mem/ exists (if not, suggest /mem-install).
2. Ask user for git remote URL (HTTPS or SSH).
3. Ask for branch (default: main).
4. Recommend token via env: `export GOWTH_MEM_GIT_TOKEN=ghp_...`
   (Optional: ask if user wants token in config.json. Warn it's plaintext.)
5. Write ~/.gowth-mem/config.json:
   {"remote": "<URL>", "branch": "<branch>", "host_id": "<machine>"}
   Plus "token": "<value>" only if user explicitly chose that path.
6. Verify ~/.gowth-mem/.gitignore excludes config.json (auto-created on first sync).
7. Suggest next: /mem-sync --init to push initial state."""

INLINE_MEM_MIGRATE_GLOBAL = """[auto-skill: mem-migrate-global] Intent = v1.0 per-workspace → v2.0 global. Execute inline:

1. Scan ~/Git/** (or user-provided list) for <ws>/.gowth-mem/ dirs (v1.0 layout).
2. For each found:
   - Walk docs/exp.md, docs/ref.md, docs/tools.md lines.
   - Use _topic.py route() to pick or create <slug>.md (at workspace root) under ~/.gowth-mem/.
   - Append migrated lines with provenance suffix `Source: <ws>/<file>`.
   - Copy docs/handoff.md lines into ~/.gowth-mem/docs/handoff.md, prefixed `host:<ws>`.
   - Copy docs/secrets.md lines (dedup by env-var name).
   - Copy journal/*.md into ~/.gowth-mem/journal/ (rename collisions).
   - Copy skills/*.md (dedup by slug).
3. Print summary: N topics created, M lines migrated, K skipped (dups).
4. Leave per-workspace .gowth-mem/ intact. User removes manually after verifying."""

INLINE_MEM_TOPIC = """[auto-skill: mem-topic] Intent = list / inspect topics. Execute inline:

Run: python3 ${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_topic.py --list
Show table: slug | title | last touched.

If user names a slug: open ~/.gowth-mem/<slug>.md (at workspace root) and show first 80 lines.
If user gives content text: show what slug --route would pick.
To regenerate _index.md: python3 _topic.py --regen-index."""

INLINE_MEM_INSTALL = """[auto-skill: mem-install] Intent = first-time setup wizard. Execute inline:

If ~/.gowth-mem/ already exists and has AGENTS.md, refuse and suggest /mem-config or /mem-sync.

1. mkdir -p ~/.gowth-mem/shared/skills (v2.3 layout: shared + workspaces)
2. Copy templates from ${CLAUDE_PLUGIN_ROOT}/templates/:
   - AGENTS.md → ~/.gowth-mem/AGENTS.md
   - dot-gowth-mem/settings.example.v2.json → ~/.gowth-mem/settings.json (rewrite version=2.3)
   - docs/secrets.md → ~/.gowth-mem/shared/secrets.md
   - docs/tools.md → ~/.gowth-mem/shared/tools.md
   Then scaffold default workspace: python3 _workspace.py create default --title "Default Fallback"
3. Ask user 3 questions:
   a. Git remote URL (HTTPS preferred for token-based auth)
   b. Branch (default: main)
   c. Token preference: env var GOWTH_MEM_GIT_TOKEN, or stored in config.json (warn plaintext)
4. Write ~/.gowth-mem/config.json with {remote, branch, host_id, token?}.
5. Run /mem-sync --init to push initial state to remote.
6. Suggest next: memx (build search index)."""

INLINE_MEM_LESSON = """[auto-skill: mem-lesson] Intent = capture experience entry. Execute inline (no /mem-lesson needed):

5-field schema (cited canonical sources — NASA LLIS / Army AAR / AWS EKS / Stripe / 5 Whys):
  Symptom    — observable error / behavior (becomes H2 heading for FTS5 prefix match)
  Tried      — what was attempted, in order
  Root cause — 1 line (optional 5-Whys chain)
  Fix        — working command/patch/config
  Source     — commit | file:line | URL (optional)

Two modes:

A. ONE-LINER (fast path) — split on ' -- ' (space-dash-dash-space):
   memL <symptom> -- <tried> -- <root cause> -- <fix>
   memL <symptom> -- <tried> -- <root cause> -- <fix> -- <source>

B. INTERACTIVE — prompt user for 5 fields sequentially.

Steps:
1. Detect mode by checking for ' -- ' in the prompt after `memL `.
2. If one-liner: parse via _lesson.parse_oneliner; reject if not 4-5 fields.
3. If interactive: ask user for Symptom?/Tried?/Root cause?/Fix?/Source? sequentially.
4. Call _lesson.append_lesson(symptom, tried, root_cause, fix, source, ws=active).
5. Auto-route via _topic.route(symptom + tried) — picks the matching topic file in active workspace.
6. Lesson written to <ws>/<topic-folder>/lessons.md (folder-level ledger; one per topic, NOT per sub-aspect).
7. Post-hook: refresh MOC + index (`_moc.py --ws <ws>` + `_index.py`).
8. Confirm: "lesson saved: <path>".

Equivalent CLI:
  python3 ${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_lesson.py \\
      --symptom "..." --tried "..." --root "..." --fix "..." [--source "..."] [--topic <slug>]

Hard rules:
- 4 fields min (symptom/tried/root/fix), source optional.
- No secrets in any field.
- Lessons.md is per topic FOLDER (not per sub-aspect file).
- Atomic write; newest entry appended at TOP under '## Entries'.
- After ≥7 days stable → distill to <ws>/docs/ref.md (manual via /mem-distill)."""


INLINE_MEM_SYNC_RESOLVE = """[auto-skill: mem-sync-resolve] Intent = AI-mediated conflict resolution. Execute inline:

1. Read ~/.gowth-mem/SYNC-CONFLICT.md. If missing, say "no conflict pending" and exit.
2. For each conflicted file in the report:
   a. Show the user the local vs remote diff (concise).
   b. Ask: keep-local | keep-remote | merge | skip | abort.
   c. If merge: propose a merged version, ask user to confirm or edit.
   d. Apply chosen version via atomic write to ~/.gowth-mem/<path>.
3. After all files resolved:
   - cd ~/.gowth-mem && git add -A
   - git rebase --continue (commit if needed)
   - git push origin <branch>  (under file_lock("sync"))
4. Delete ~/.gowth-mem/SYNC-CONFLICT.md.
5. Confirm to user: "resolved N files, pushed to origin"."""


SHORTCUT_KEYWORDS: dict[str, str] = {
    "mems": INLINE_MEM_SAVE,
    "memd": INLINE_MEM_DISTILL,
    "memr": INLINE_MEM_REFLECT,
    "memk": INLINE_MEM_SKILLIFY,
    "memb": INLINE_MEM_BOOTSTRAP,
    "memh": INLINE_MEM_HYDE,
    "memj": INLINE_MEM_JOURNAL,
    "memx": INLINE_MEM_REINDEX,
    "memc": INLINE_MEM_COST,
    "memp": INLINE_MEM_PRUNE,
    "memy": INLINE_MEM_SYNC,
    "memg": INLINE_MEM_CONFIG,
    "memm": INLINE_MEM_MIGRATE_GLOBAL,
    "memT": INLINE_MEM_TOPIC,
    "memI": INLINE_MEM_INSTALL,
    "memC": INLINE_MEM_SYNC_RESOLVE,
    "memL": INLINE_MEM_LESSON,
}

# Capital-suffix shortcuts (memT/memI/memC/memL) are case-sensitive to avoid false matches with
# their lowercase variants (memt/memi/memc/meml).
SHORTCUT_RE = re.compile(
    r"^\s*(mems|memd|memr|memk|memb|memh|memj|memx|memc|memp|memy|memg|memm|memT|memI|memC|memL)\b"
)


NL_PATTERNS: list[tuple[re.Pattern[str], str, bool]] = [
    (re.compile(r"\b(save\s+(this|it|that)|remember\s+(this|it|that)|note\s+(this|it|that))\b", re.I),
     INLINE_MEM_SAVE, True),
    (re.compile(r"\b(recap|summari[sz]e|sum\s+(this|it)\s+up|reflect\s+on)\b", re.I),
     INLINE_MEM_REFLECT, True),
    (re.compile(r"\b(make\s+this\s+(a\s+)?skill|extract\s+(a\s+)?skill|reusable\s+workflow)\b", re.I),
     INLINE_MEM_SKILLIFY, True),
    (re.compile(r"^\s*(where\s+am\s+i|what's?\s+the\s+status|current\s+state)\b", re.I),
     INLINE_MEM_BOOTSTRAP, True),
    (re.compile(r"^\s*(review|critique)\b", re.I),
     "intent=review: examine and point out flaws, do not implement unless asked.", False),
    (re.compile(r"^\s*(fix|debug|repair)\b", re.I),
     "intent=fix: root-cause first, minimal diff, verify before claiming done.", False),
    (re.compile(r"^\s*(research|find|investigate|explain)\b", re.I),
     "intent=research: read first, no edits, cite sources, save findings under workspaces/<ws>/<slug>.md.", False),
    (re.compile(r"^\s*(plan|design|architect)\b", re.I),
     "intent=plan: produce structure, list steps, do not implement yet.", False),
]


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    prompt = (data.get("prompt") or "").strip()
    # NOTE: do NOT early-return on empty prompt — Rules must inject every turn
    # (cache-friendly stable prefix). Downstream gate at the bottom returns 0
    # if literally nothing to emit (no rules + no shortcuts + no nudge).

    today = date.today()
    yesterday = today - timedelta(days=1)
    user = os.environ.get("USER") or os.environ.get("USERNAME") or "user"

    expansions: dict[str, str] = {}
    if re.search(r"@today\b", prompt):
        expansions["@today"] = today.isoformat()
    if re.search(r"@yesterday\b", prompt):
        expansions["@yesterday"] = yesterday.isoformat()
    if re.search(r"@user\b", prompt):
        expansions["@user"] = user

    triggered_block: str | None = None
    nudge: str | None = None

    m = SHORTCUT_RE.match(prompt)
    if m:
        triggered_block = SHORTCUT_KEYWORDS[m.group(1)]
    else:
        for pattern, payload, is_inline in NL_PATTERNS:
            if pattern.search(prompt):
                if is_inline:
                    triggered_block = payload
                else:
                    nudge = payload
                break

    # Rules block removed (2026-05-06): bootstrap-load.py already loads AGENTS.md
    # at SessionStart. Re-injecting here every turn was duplicate work.
    if not expansions and triggered_block is None and nudge is None:
        return 0

    parts: list[str] = []
    parts.append("[gowth-mem:user-augment]")
    if expansions:
        parts.append("Shortcuts:")
        for k, v in expansions.items():
            parts.append(f"- {k} -> {v}")
    if triggered_block:
        parts.append("")
        parts.append(triggered_block)
    elif nudge:
        parts.append("")
        parts.append(nudge)

    out = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "\n".join(parts),
        }
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
