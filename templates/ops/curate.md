---
description: "Daily curator — status, a dry digest (prep), or run it now"
---

# /mem-ops curate [status|prep|now] [--ws <ws>]

The daily curator (v4.9) consolidates one workspace's session logs and vault changes into
reviewed entries, corrections, topic cards, open questions and a worklog
(`<ws>/docs/worklog/<date>-<host>-<run6>.md`). Once a day the Stop hook asks the main session to
launch it as ONE background `gowth-mem:curator` subagent (protocol: `agents/curator.md` in the
plugin). This op shows its state, previews its digest, or runs it now. Default workspace = the
active one; add `--ws <name>` for another. No sub-argument = `status`.

## status (default)

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_curate.py" status                  # every workspace
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_curate.py" status --ws <ws> --json
```

One line per workspace: the last run (date, host, run, phase), misses (claimed days that
produced no worklog — two of them pause the automatic trigger until a run succeeds), claims
today, open questions (ask / research), cards TODO / over budget / stale, and the session-log
turns no worklog has processed yet.

## prep — the digest, dry (writes nothing)

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_curate.py" prep --ws <ws> --dry
```

Prints the digest the curator would read: §1 header, §2 unprocessed turns, §3 corrections,
§4 vault changes, §4b project commits, §4c entries written in the window, §5 stale handoff
items, §6 open questions, §7 topics touched, §8 budget report, §9 old hypotheses, §10 research
candidates — each within its own budget, with a `+N more` line where it was cut. Show the user
the section headings and counts, not the whole text, unless asked.

## now — run the curator from this session

1. Claim the next attempt marker:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_curate.py" claim --ws <ws>
   ```
   `claim-<date>-<n><TAB>claimed` → continue. `-<TAB>refused:recent_claim:<m>m` → a run claimed
   <m> minutes ago has not written its worklog yet: tell the user to wait for it (or for
   `curator.retry_minutes`) and stop.
2. Print the dispatch directive and follow it exactly — ONE background subagent, then continue:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_curate.py" directive --ws <ws>
   ```
3. When it reports, relay its summary line, and its `ask Q-…` lines verbatim with how to answer
   them (`/mem-ops gap answer <id> "<answer>" --evidence user`).

## Permissions (outside bypass mode)

A background subagent cannot ask for permission: its first denied call ends the run with
`curator ws=<ws>: blocked — <command> <reason>`, and two such days pause the curator. Every
call it makes is `GOWTH_MEM_HOME=<vault> python3 <plugin>/hooks/scripts/<tool> …`, and it reads
the vault and its own protocol. Allow exactly that in `~/.claude/settings.json`
(`permissions.allow`), with `<vault>` = the absolute vault path (`~/.gowth-mem` unless
GOWTH_MEM_HOME says otherwise) and `<plugin>` = this plugin's root (the paths printed by
`directive`; they change with every plugin version — update the rules after an upgrade):

```json
"Bash(GOWTH_MEM_HOME=<vault> python3 <plugin>/hooks/scripts/_curate.py:*)",
"Bash(GOWTH_MEM_HOME=<vault> python3 <plugin>/hooks/scripts/_query.py:*)",
"Bash(GOWTH_MEM_HOME=<vault> python3 <plugin>/hooks/scripts/_topic.py:*)",
"Bash(GOWTH_MEM_HOME=<vault> python3 <plugin>/hooks/scripts/_gaps.py:*)",
"Bash(GOWTH_MEM_HOME=<vault> python3 <plugin>/hooks/scripts/_lesson.py:*)",
"Bash(GOWTH_MEM_HOME=<vault> python3 <plugin>/hooks/scripts/_card.py:*)",
"Read(~/.gowth-mem/**)",
"Read(//<plugin>/**)"
```

(`Read(//<absolute path>/**)` for a vault outside your home directory.) The curator has no
Write, Edit or Agent tool: it changes the vault only through these CLIs, never runs git, and
never launches subagents.

## Settings (`curator.*`)

`enabled` (true) · `hour` (0) · `min_new_turns` (5) · `first_window_days` (7) · `stale_days` (14) ·
`max_cards` (5) · `max_research` (2) · `max_research_tries` (3) · `retry_minutes` (120) ·
`hold_days` (30 — a session log with unprocessed turns outlives `journal.raw_ttl_days` by up to
this) · `max_prep_chars` (40000) · `model` ("sonnet") · `hosts` ([] — when set, only these hosts
curate; otherwise one host per workspace, with failover after 2 days).
