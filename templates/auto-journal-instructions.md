[gowth-mem:auto-journal ws={ws}] {journal_every} turns elapsed.

## Dispatch protocol (MAIN session — read this first)

**If you were dispatched as the memory teammate, SKIP this section entirely —
start at "Teammate protocol" below. Never dispatch further subagents.**

This work must NOT run in the main context: it costs 10-20 tool calls that dilute
the conversation and break the user's flow. The main session's ONLY job here:

1. **Dispatch ONE background subagent** (Task/Agent tool — a context-inheriting
   fork if your harness has one, else a fresh general-purpose subagent) as the
   memory teammate. Its prompt: "Read <this file> and execute the teammate
   protocol below for ws={ws}. Turn source = the session log path given in the
   hook reason."
2. **Continue your work immediately.** When the teammate completes, relay its
   1-line confirmation and nothing more.

Fallback (only when NO subagent tool exists in this harness, or the reason gave
no session-log path and you are not a context-inheriting fork): execute the
teammate protocol below yourself, inline, silently.

## Teammate protocol (the dispatched subagent executes everything below)

Anchors (a fresh subagent has no SessionStart bootstrap — derive, don't guess):
- **Vault root** = the directory above `workspaces/` in your session-log path
  (this respects `GOWTH_MEM_HOME`; never assume `~/.gowth-mem/`).
- **Plugin scripts** (`_moc.py`, …) live at `<directory of this file>/../hooks/scripts/`.
- **Active workspace** = {ws} (also in the hook reason). Other workspaces:
  enumerate with `ls <vault-root>/workspaces/`.

Do this WITHOUT user prompting before yielding control:

1. Read the turn source: the session log passed in your prompt
   (`workspaces/<ws>/journal/sessions/<date>-<sid8>.md` — each `## turn` block
   records User / Claude / Actions). If you inherited the main conversation's
   context (fork), you may scan the last {journal_every} turns directly instead.
2. For each high-signal item, classify into ONE of these types and prepend the prefix:
   [goal]        user objective/intent        → workspaces/<target_ws>/<slug>/<YYYY-MM-DD>-<aspect>.md  (Status: + Done when: REQUIRED)
   [decision]    choice + rationale          → workspaces/<target_ws>/<slug>/<YYYY-MM-DD>-<aspect>.md  (## [decision])
   [exp]         debug / fix / lesson         → workspaces/<target_ws>/<slug>/<YYYY-MM-DD>-<aspect>.md  (## [exp])
   [reflection]  pattern / takeaway           → workspaces/<target_ws>/<slug>/<YYYY-MM-DD>-<aspect>.md  (## [reflection])
   [ref]         verified external fact       → workspaces/<target_ws>/<slug>/<YYYY-MM-DD>-<aspect>.md  (## [ref], Source REQUIRED)
   [tool]        topic-specific gotcha        → workspaces/<target_ws>/<slug>/<YYYY-MM-DD>-<aspect>.md  OR  workspaces/<target_ws>/docs/tools.md
   [hypothesis]  UNVERIFIED claim/assumption  → workspaces/<target_ws>/<slug>/<YYYY-MM-DD>-<aspect>.md  (Verify: path REQUIRED)
   [secret-ref]  env-var POINTER              → shared/secrets.md  (NEVER value)
   Use [goal] for the user's objectives (with Status:), [hypothesis] for unverified claims (with Verify:).
3. **Workspace + topic routing** (v3.0: topic = FOLDER):
   - Workspace: route each entry to the workspace that best matches its topic:
     * {ws} (active) = default target for entries about the current session's work
     * another workspace (see Anchors above for how to enumerate them) = route
       there only if the entry clearly belongs to that workspace's domain
     * shared/ = cross-workspace resources (secrets, tools)
   - Topic inside the chosen workspace:
     * Pick existing topic folder workspaces/<target_ws>/<slug>/ if keywords overlap (≥3 common words).
     * Otherwise create new topic folder workspaces/<target_ws>/<new-slug>/ with `00-README.md`
       (frontmatter: slug/title/type:misc/status:draft/maturity:draft/created/last_touched/parents/links/aliases/tags)
       PLUS today's dated aspect file `YYYY-MM-DD-<aspect>.md` for the entry.
     * Always append entries to the dated aspect file, NEVER to `00-README.md` (auto-regenerated MOC).
     * Reserved subdirs at ws root: docs, journal, skills, research. Reserved files inside topic folder:
       00-README.md, lessons.md, _MAP.md.
4. Apply quality gates per `shared/research/data-quality-2026.md` §1 — DROP if:
   - Entry < 20 chars
   - Code-only (no prose)
   - [ref] without Source
   - Vague / hedged ("maybe", "I think") without backing
   - Secret pattern hit (§1a): AKIA* / sk-* / ghp_* / xox* / PRIVATE KEY / JWT → never write, quarantine in handoff with [secret-ref] pointer only
4b. (v4.0 auto-tagging) The write path appends deterministic inline `#tags` to each
   entry's FIRST line and unions them into the aspect file's frontmatter `tags:`
   automatically — you do NOT add `#tags` by hand. To make those tags useful, write
   **content-dense first lines** (put the key nouns/identifiers/paths up front) and
   never leave a MOC TL;DR as `TODO`.
5. Apply mem0 ADD / UPDATE / DELETE / NOOP (canon §5) against existing target file content.
   Numeric dedup: Jaccard ≥ 0.85 → UPDATE/merge; overlap ≥ 0.4 + polarity flip → contradiction lint.
   Update frontmatter.last_touched on every write. Never blind append.
6. Update workspaces/{ws}/docs/handoff.md (prefix host:<machine>) with new task / next / blocker.
7. After writes, run `_moc.py --ws <target_ws>` for each workspace that received writes
   (refreshes the workspace MOC + every topic README in that workspace).
8. Confirm in 1 line: "auto-journal: ws={ws}(+others), kept N, dropped M, promoted K, conflicts resolved J".
   That line is your ENTIRE final report back to the main session.

Don't write the user a long message about this — just do the work silently and continue.
This is automation, not a conversation step.
