#!/usr/bin/env python3
"""UserPromptSubmit hook: short keyword shortcuts (OMC-style) + intent-driven auto-skill.

v0.8: drop Vietnamese intent matching. Add short English keyword shortcuts
(like OMC's `ulw`) at start of prompt — explicit, low false-positive.

Shortcut keywords (must appear at start of prompt, optionally followed by content):

  mems   →  mem-save          (save current decision/fact/lesson)
  memd   →  mem-distill       (chắt lọc journal → docs/exp/ref/tools)
  memr   →  mem-reflect       (recap / generate reflections from journal)
  memk   →  mem-skillify      (extract recurring workflow → docs/skills/)
  memb   →  mem-bootstrap     (3-line: doing what / next / blocker)
  memh   →  mem-hyde-recall   (HyDE for conceptual queries)
  memj   →  mem-journal       (open today's journal)
  memx   →  mem-reindex       (rebuild SQLite FTS5/vector index)
  memc   →  mem-cost          (estimate bootstrap token footprint)

Natural English phrases (loose match, lower priority than shortcuts):

  save this / remember this / note this   →  mem-save
  recap / summarize / reflect             →  mem-reflect
  where am I / status / current state     →  mem-bootstrap
  make this a skill / extract skill       →  mem-skillify

Plain nudges (no inline skill, just intent hint):

  review / critique                       →  examine, don't implement
  fix / debug / repair                    →  root-cause first
  research / find / investigate           →  read first, save findings
  plan / design / architect               →  produce structure first

@-shortcuts (unchanged):

  @today @yesterday @ws @user @hot

Examples:
  "mems decided to use EMA cross strategy"   →  inline mem-save body + content
  "memb"                                     →  inline mem-bootstrap (3-line summary)
  "where am I in this project?"              →  inline mem-bootstrap (NL match)
  "save this finding from research"          →  inline mem-save (NL match)
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path


# ---------------------------------------------------------------------------
# Inline skill bodies — what Claude executes when the intent matches.

INLINE_MEM_SAVE = """[auto-skill: mem-save] Intent = save. Execute inline (no /mem-save needed):

Routing table (1-2 lines / entry, Source required for facts):
| Episodic (debug, fix, lesson, surprise, anti-pattern) | docs/exp.md |
| Verified semantic fact (with Source URL) | docs/ref.md |
| Tool syntax / gotcha / version | docs/tools.md |
| Resource pointer (env-var name; NEVER value) | docs/secrets.md |
| Session state (current task / next / blocker) | docs/handoff.md |
| Workflow done 2+ times | docs/skills/<name>.md (use memk) |

Apply mem0 ADD / UPDATE / DELETE / NOOP. Conflict with existing → drop old. Confirm path written."""

INLINE_MEM_DISTILL = """[auto-skill: mem-distill] Intent = distill. Execute inline (no /mem-distill needed):

1. Read docs/journal/<today>.md and <yesterday>.md.
2. Classify each entry: lesson → docs/exp.md; verified fact → docs/ref.md; tool note → docs/tools.md;
   resource pointer → docs/secrets.md; pure noise → drop.
3. For each kept entry, apply mem0 ADD / UPDATE / DELETE / NOOP against the target file.
4. Mark distilled journal entries with `(distilled)` suffix.
5. Report: ADD N, UPDATE M, DELETE K, NOOP J, DROP D, LEFT L."""

INLINE_MEM_REFLECT = """[auto-skill: mem-reflect] Intent = reflect/recap. Execute inline (no /mem-reflect needed):

1. Read docs/journal/*.md from last 7 days + docs/exp.md.
2. Score entries by importance × recency × novelty.
3. Pick top 3 patterns (clusters of related entries).
4. Append to docs/exp.md § Reflections:
   ### YYYY-MM-DD: <title>
   **Claim**: <evergreen 1-line>
   **Evidence**: <file:line refs (≥2)>
   **Implication**: <1-line action>
5. Suggest /save (claude-obsidian) for portable reflections.
NEVER invent — every reflection cites ≥2 source entries."""

INLINE_MEM_SKILLIFY = """[auto-skill: mem-skillify] Intent = extract reusable skill. Execute inline (no /mem-skillify needed):

1. Identify the recurring workflow's core steps (parameterize variables).
2. Pick a kebab-case <name> ≤30 chars.
3. mkdir -p docs/skills if missing.
4. Write docs/skills/<name>.md with frontmatter (name, description, created, inputs)
   and sections: Description / Steps (parameterized) / Variations / Token cost / Source.
5. Confirm path. Suggest invocation: `do <name> for <input>`."""

INLINE_MEM_BOOTSTRAP = """[auto-skill: mem-bootstrap] Intent = where-am-I / status. Execute inline (no /mem-bootstrap needed):

Read docs/handoff.md. Emit EXACTLY 3 lines for the user:
1. **doing**: <Current task — 1 line>
2. **next**: <Next step — 1 line>
3. **blocker**: <Blocker — 1 line, or `none`>

If docs/handoff.md is missing/empty, say so and suggest /mem-init."""

INLINE_MEM_HYDE = """[auto-skill: mem-hyde-recall] Intent = HyDE recall (conceptual query). Execute inline:

1. Draft a 1-2 paragraph hypothetical answer to the question (just for retrieval, not authoritative).
2. If `.gowth-mem/index.db` + `sqlite-vec` + embedding key all available:
   embed the hypothetical answer, vector top-K against chunks_vec, RRF-merge with FTS5 BM25 over original.
3. Else: extract ≥5-char keywords from hypothetical answer and grep docs/**/*.md and wiki/**/*.md.
4. Filter temporal-invalid lines (`(superseded)`, expired `valid_until:`).
5. Synthesize against original question, citing each chunk like `docs/exp.md § Lessons`.
6. If no useful match: suggest /mem-reindex or /wiki-query (claude-obsidian)."""

INLINE_MEM_JOURNAL = """[auto-skill: mem-journal] Intent = open today's journal. Execute inline:

1. WS=$CLAUDE_PROJECT_DIR or $PWD.
2. mkdir -p $WS/docs/journal.
3. If docs/journal/<today>.md missing: copy from ${CLAUDE_PLUGIN_ROOT}/templates/journal-day.md, replace YYYY-MM-DD placeholder.
4. Show current contents.
5. Ask user what to log and which section (Logs / Wins / Pains / Questions).
6. Append with HH:MM prefix for Logs entries."""

INLINE_MEM_REINDEX = """[auto-skill: mem-reindex] Intent = rebuild search index. Execute inline:

Run: `python3 ${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_index.py` from workspace root.
- Default: incremental (only re-indexes files with changed mtime).
- Pass `--full` to drop and rebuild.
Report files / chunks indexed and which fallback (FTS5-only vs vector hybrid).
Add `.gowth-mem/` to workspace `.gitignore` if not present."""

INLINE_MEM_COST = """[auto-skill: mem-cost] Intent = estimate bootstrap token footprint. Execute inline:

Sum char count of: AGENTS.md + docs/handoff.md + docs/exp.md + docs/ref.md + docs/tools.md +
docs/secrets.md + docs/files.md + docs/journal/<today>.md + docs/journal/<yesterday>.md.
Estimate tokens = chars / 4. Print per-file breakdown + total.
Cap = 60,000 chars (~15,000 tokens). Warn if >40k or >60k."""


# ---------------------------------------------------------------------------
# Shortcut keyword table (3-4 char codes at START of prompt, OMC ulw-style).

INLINE_MEM_PRUNE = """[auto-skill: mem-prune] Intent = actively DELETE outdated entries from docs/*.md. Execute inline:

Run: `python3 ${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_prune.py --workspace ${CLAUDE_PROJECT_DIR:-$PWD}`
Pass `--dry-run` first if user wants preview.

Deletion rules (in order):
1. Entry containing `valid_until: YYYY-MM-DD` past today → DELETE
2. Entry containing `(superseded)` / `(deprecated)` / `(obsolete)` → DELETE
3. Within-file Jaccard ≥ 0.85 duplicate → DELETE the SHORTER, keep longer/richer

Skips docs/journal/** (raw log is permanent). Report: deleted N, kept K."""

INLINE_MEM_SYNC = """[auto-skill: mem-sync] Intent = sync .gowth-mem/ via git remote. Execute inline:

Pre-req: `.gowth-mem/config.json` configured with `remote` + `branch` (use /mem-config). Token via env GOWTH_MEM_GIT_TOKEN preferred.

Run: `python3 ${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_sync.py --workspace ${CLAUDE_PROJECT_DIR:-$PWD}`

Flags:
  --init        first-time setup (creates .git, push initial state)
  --pull-only   fetch+rebase, no push
  --push-only   commit+push, no pull

What gets synced: AGENTS.md, docs/*, settings.json. Gitignored (per-machine): config.json, state.json, index.db.

On conflict: writes .gowth-mem/SYNC-CONFLICT.md. Resolve markers manually, `git -C .gowth-mem add <files>`, `git -C .gowth-mem rebase --continue`, re-run."""

INLINE_MEM_CONFIG = """[auto-skill: mem-config] Intent = set up .gowth-mem/config.json for git sync. Execute inline:

1. Ensure .gowth-mem/ exists (if not, suggest /mem-init).
2. Ask user for git remote URL (HTTPS or SSH).
3. Ask for branch (default: main).
4. Recommend token via env: `export GOWTH_MEM_GIT_TOKEN=ghp_...`
   (Optional fallback: ask if user wants token in config.json. Warn it's plaintext.)
5. Write `.gowth-mem/config.json`:
   {"remote": "<URL>", "branch": "<branch>"}
   Plus "token": "<value>" only if user explicitly chose that path.
6. Verify .gowth-mem/.gitignore excludes config.json (the _sync.py creates one if missing).
7. Suggest next: /mem-sync --init to push initial state."""

INLINE_MEM_MIGRATE = """[auto-skill: mem-migrate] Intent = migrate v0.9 (workspace-rooted) → v1.0 (.gowth-mem/ centralized). Execute inline:

1. mkdir -p .gowth-mem/docs/journal .gowth-mem/docs/skills
2. Move workspace AGENTS.md → .gowth-mem/AGENTS.md (if exists, target missing)
3. Move workspace docs/{handoff,exp,ref,tools,secrets,files}.md → .gowth-mem/docs/
4. Move workspace docs/journal/* → .gowth-mem/docs/journal/
5. Move workspace docs/skills/* → .gowth-mem/docs/skills/
6. Remove now-empty workspace docs/ dir
7. Create .gowth-mem/settings.json + .gitignore from templates if missing
8. Suggest next: /mem-config → /mem-sync --init → memx (rebuild index)

Idempotent — each move guards against existing target."""

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
    "memm": INLINE_MEM_MIGRATE,
}

SHORTCUT_RE = re.compile(
    r"^\s*(" + "|".join(re.escape(k) for k in SHORTCUT_KEYWORDS) + r")\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Natural English phrase patterns (lower priority than shortcuts).
# (regex, payload, is_inline_skill)

NL_PATTERNS: list[tuple[re.Pattern[str], str, bool]] = [
    (re.compile(r"\b(save\s+(this|it|that)|remember\s+(this|it|that)|note\s+(this|it|that))\b", re.I),
     INLINE_MEM_SAVE, True),
    (re.compile(r"\b(recap|summari[sz]e|sum\s+(this|it)\s+up|reflect\s+on)\b", re.I),
     INLINE_MEM_REFLECT, True),
    (re.compile(r"\b(make\s+this\s+(a\s+)?skill|extract\s+(a\s+)?skill|reusable\s+workflow)\b", re.I),
     INLINE_MEM_SKILLIFY, True),
    (re.compile(r"^\s*(where\s+am\s+i|what's?\s+the\s+status|current\s+state)\b", re.I),
     INLINE_MEM_BOOTSTRAP, True),
    # Plain nudges (no inline skill body):
    (re.compile(r"^\s*(review|critique)\b", re.I),
     "intent=review: examine and point out flaws, do not implement unless asked.", False),
    (re.compile(r"^\s*(fix|debug|repair)\b", re.I),
     "intent=fix: root-cause first, minimal diff, verify before claiming done.", False),
    (re.compile(r"^\s*(research|find|investigate|explain)\b", re.I),
     "intent=research: read first, no edits, cite sources, save findings to docs/ref.md.", False),
    (re.compile(r"^\s*(plan|design|architect)\b", re.I),
     "intent=plan: produce structure, list steps, do not implement yet.", False),
]


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return 0

    workspace = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    today = date.today()
    yesterday = today - timedelta(days=1)
    user = os.environ.get("USER") or os.environ.get("USERNAME") or "user"

    expansions: dict[str, str] = {}
    if re.search(r"@today\b", prompt):
        expansions["@today"] = today.isoformat()
    if re.search(r"@yesterday\b", prompt):
        expansions["@yesterday"] = yesterday.isoformat()
    if re.search(r"@ws\b|@workspace\b", prompt):
        expansions["@ws / @workspace"] = str(workspace)
    if re.search(r"@user\b", prompt):
        expansions["@user"] = user
    if re.search(r"@hot\b", prompt):
        hot = workspace / "wiki" / "hot.md"
        if hot.is_file():
            expansions["@hot"] = f"read {hot.relative_to(workspace)} (claude-obsidian hot cache)"
        else:
            expansions["@hot"] = "wiki/hot.md not found"

    # 1) Shortcut keyword takes priority over NL patterns.
    triggered_block: str | None = None
    nudge: str | None = None

    m = SHORTCUT_RE.match(prompt)
    if m:
        triggered_block = SHORTCUT_KEYWORDS[m.group(1).lower()]
    else:
        # 2) Natural English fallback.
        for pattern, payload, is_inline in NL_PATTERNS:
            if pattern.search(prompt):
                if is_inline:
                    triggered_block = payload
                else:
                    nudge = payload
                break

    if not expansions and triggered_block is None and nudge is None:
        return 0

    parts = ["[openclaw-bridge:user-augment]"]
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
