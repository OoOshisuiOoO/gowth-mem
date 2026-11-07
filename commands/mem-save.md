---
description: "Save one [type] entry, or distill this session into topic files (no args)"
argument-hint: "[type] \"<entry>\" [--topic <slug>]   (no arguments = distill this session)"
---

# /mem-save [type] "<entry>" [--topic <slug>]

## With an entry — save exactly one

When the arguments start with a `[type]` tag (`[decision]`, `[exp]`, `[ref]`, `[tool]`,
`[reflection]`, `[skill-ref]`, `[secret-ref]`, `[goal]`, `[hypothesis]`), save that ONE entry through
the plugin's write path (routing + dedup + quality gate + tags + index) — never by editing topic files:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_topic.py" --append "$(cat <<'ENTRY'
[decision] <the entry text, verbatim — its [type] tag first>
ENTRY
)"
```

- Pass the entry through the quoted heredoc above (it may contain quotes, `$` or backticks).
- `--topic <slug>` (after the closing `)"`) forces an EXISTING topic folder; without it the entry is
  routed by keyword overlap. The workspace is the active one (add `--ws <ws>` to pick another).
- Keep the type's required part: `[decision]` a rationale ("because …"), `[ref]` a `Source:`, `[tool]` a
  version, `[hypothesis]` a `Verify:`, `[goal]` a `Status:` and `Done when:`. A correction is a new entry
  of the same type: `Correction: <old> → <new> (<date>; Corrects: <vault-relative path>; Source: …)`.

The command prints `<path>\t<status>`. Tell the user what happened:

| Status | Meaning — what to do |
|---|---|
| `written` | saved at `<path>` (recallable right away) |
| `duplicate` | the same entry is already in the vault — nothing to do |
| `rejected:no_topic` | `--topic` names no existing topic folder (or a domain, an ambiguous name) — pick one from `/mem-topic list` or drop `--topic` |
| `rejected:bad_ws` | the workspace name is invalid |
| `rejected:lock_timeout` | another writer held the topic lock — run the same command again |
| `rejected:<rule>` | the quality gate refused it (e.g. a `[ref]` without `Source:`, a hedge without evidence, a secret value) — fix what the rule names and save again; do not force it |

## Without arguments — distill this session

Invoke the `mem-save` skill to run the full dreaming cycle on this conversation.

Pre-processing scripts run automatically (prune, consolidation ranking, contradiction lint).
Then scan the conversation for high-signal items, classify, route to topic files, and report.
