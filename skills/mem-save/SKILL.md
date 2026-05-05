---
name: mem-save
description: "Run the OpenClaw-inspired dreaming pipeline: prune → consolidate → lint → distill high-signal items from the current conversation into topic files. Use anytime — don't wait for auto-journal's 10-turn threshold."
---

# mem-save — Dreaming Pipeline (v2.9)

Run the full OpenClaw dreaming cycle on demand. Same pipeline as `auto-journal.py` but triggered manually — use when a session has valuable content and you don't want to wait for the 10-turn auto threshold.

## 1. Resolve active workspace

```bash
WS=$(python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_workspace.py" active)
echo "active workspace: $WS"
```

## 2. Pre-processing scripts (run sequentially)

### 2a. Prune — remove stale/low-quality entries

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_prune.py"
```

### 2b. Consolidate — Light→REM→Deep ranking

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_consolidate.py"
```

Read stdout for the consolidation report: promote/maintain/prune candidates with scores.

### 2c. Lint — contradiction detection

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_lint.py"
```

Read stdout for any detected contradictions between entries. If contradictions found, resolve them during the distill phase (keep the newer/better-sourced entry, DELETE the stale one).

## 3. Scan conversation for high-signal items

Scan ALL user turns and your replies in this conversation (not just the last 10). For each high-signal item, classify into ONE type:

| Prefix | What | Destination | Section |
|---|---|---|---|
| `[decision]` | choice + rationale | `workspaces/$WS/<slug>.md` | `## [decision]` |
| `[exp]` | debug / fix / lesson | `workspaces/$WS/<slug>.md` | `## [exp]` |
| `[reflection]` | pattern / takeaway | `workspaces/$WS/<slug>.md` | `## [exp]` |
| `[ref]` | verified external fact | `workspaces/$WS/<slug>.md` | `## [ref]` (Source REQUIRED) |
| `[tool]` | topic-specific gotcha | `workspaces/$WS/<slug>.md` OR `workspaces/$WS/docs/tools.md` | |
| `[secret-ref]` | env-var POINTER | `shared/secrets.md` | (NEVER the value) |

## 4. Quality gates — DROP if

- Entry < 20 chars
- Code-only (no prose explanation)
- `[ref]` without `Source:` link
- Vague / hedged ("maybe", "I think") without backing evidence

## 5. Topic routing

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_topic.py" --route "<your text>" --ws "$WS"
```

Pick existing `workspaces/$WS/**/<slug>.md` (excluding docs/journal/skills) if keywords overlap (>=3 common words); otherwise create new `workspaces/$WS/<new-slug>.md` with v2.3 frontmatter.

Reserved names: docs, journal, skills, _MAP.md, AGENTS.md, workspace.json.

Lazy-nest into domain folders only when >=5 topics share a theme.

## 6. Apply mem0 write semantics

For each entry against its target file:

- **ADD** — new and not redundant.
- **UPDATE** — similar entry exists with stale info -> rewrite that line.
- **DELETE** — directly contradicts new entry (or flagged by lint).
- **NOOP** — duplicate already present.

Update `frontmatter.last_touched` on every write.

## 7. Integrate consolidation results

If `_consolidate.py` reported **prune_candidates**, review them:
- If the entry is truly stale/superseded, DELETE it.
- If still relevant, leave it (the scoring is advisory, not automatic).

If **promote** items were identified, ensure they have complete entries (add Source if missing, expand terse entries).

## 8. Update handoff

Update `workspaces/$WS/docs/handoff.md` (prefix `host:<machine>`) with current task / next step / blocker.

## 9. Refresh MOC + index

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_moc.py" --ws "$WS"
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_index.py"
```

## 10. Report summary

One line:

```
dreaming: ws=$WS, kept N, dropped M, promoted K, pruned P, contradictions C
```

## Hard rules

- **Never write a real secret value.** `shared/secrets.md` stores pointers only.
- **`[ref]` without Source -> reject.**
- **Conflict with existing entry** -> DELETE old, keep new.
- **Cross-workspace writes** require explicit workspace switch.
- **All file writes** go through `_atomic.atomic_write`.
