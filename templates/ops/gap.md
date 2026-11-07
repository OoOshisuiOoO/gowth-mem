---
description: "Gap ledger — open questions the vault cannot answer yet (research or ask)"
---

# /mem-ops gap <add|list|answer|close|tried> [args]

The workspace's open questions live in `<ws>/docs/questions.md`, written only by `_gaps.py`
(every write re-renders the file — never edit it by hand). Two kinds:

- `research` — an agent can find the answer: the vault first (`/mem-recall`), then primary
  sources (repo code, official docs and changelogs, the web). Save what you find as a `[ref]`
  entry with `Source:` before you answer the question, and cite that entry's path.
- `ask` — only the user knows (intent, preference, an operator-only fact).

Default workspace = the active one; add `--ws <name>` for another.

## Commands

`/mem-ops gap add research "<q>"` and `/mem-ops gap add ask "<q>"` map to `add --kind <kind>`:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_gaps.py" add --kind research "<question>" --why "<what shows it is a gap>"
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_gaps.py" add --kind ask "<question>" --why "<context>" --from user
```

`--from` is `session` (default), `user` (the user asked for it) or `curator`. Prints
`Q-xxxxxxxx<TAB>added|duplicate|similar:Q-yyyyyyyy|rejected:<rule>`.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_gaps.py" list                    # open questions, oldest first
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_gaps.py" list --kind ask --json
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_gaps.py" list --closed           # history, with answers
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_gaps.py" answer Q-xxxxxxxx "<answer>" --evidence "<vault path | URL | commit | user>"
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_gaps.py" close Q-xxxxxxxx --evidence "<why>" --reason obsolete
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_gaps.py" tried Q-xxxxxxxx --note "<why it is not answerable now>"
```

`close --reason` is one of `obsolete`, `duplicate`, `answered-elsewhere`, `escalated`.
`tried` bumps the attempt count shown on the line (` · tried <date>×<n>`).

## Status → what to do

| Status | Meaning / next step |
|---|---|
| `added`, `answered`, `closed`, `tried:<n>` | done |
| `duplicate` | the same question exists — open, or closed (`list --closed` shows its answer); use that id |
| `similar:Q-…` | a near-identical OPEN question exists: answer or `tried` that one; `--force` only when yours is really different |
| `rejected:too_short` / `too_long` | a question is 12..300 chars |
| `rejected:secret_leak` | never store a credential — name the env var instead |
| `rejected:control_token` | the text quotes a `[gowth-mem:` hook directive — rephrase it |
| `rejected:not_english` | `gate.english_only` is on — store it in English |
| `rejected:no_evidence` | `answer` / `close` need `--evidence` |
| `rejected:weak_evidence` | a `research` answer must cite a vault path, URL or commit: save the `[ref]` (with `Source:`) first, then answer with its path |
| `rejected:lock_timeout` | another session is writing the ledger — retry |
| `not_found`, `rejected:already_closed` | check `list` / `list --closed` for the right id |

## Rules for the session

1. You learned the answer to an open question → `answer` it now (`research`: write the `[ref]`
   first; `ask`: `--evidence user`).
2. Unknown and not answerable now → `add` it (`research` when an agent can find it, `ask` when
   only the user knows) instead of guessing.
3. At a natural pause, ask the user the OLDEST open `ask` item — verbatim, with its id — and
   record the reply with `answer <id> "<reply>" --evidence user`.
4. Handoff ticks: an `ask` question of the form `Handoff item '<…>' looks done—tick it?`
   (the curator's proposal; the separator dash is stored without spaces). When the user
   answers yes: change that item to `[done]` in `docs/handoff.md` (keep its host and date),
   then `answer <id> "yes, ticked" --evidence user`. When the answer is no: answer it with
   what is still missing, and leave the handoff item as it is.
5. A question that stopped mattering → `close <id> --reason obsolete --evidence "<why>"`.
