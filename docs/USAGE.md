# How to use gowth-mem (v4.9)

Tiếng Việt: [USAGE.vi.md](USAGE.vi.md). Release notes: [SHIPPED-FEATURES.md](SHIPPED-FEATURES.md).

gowth-mem is one memory vault (`~/.gowth-mem`, a git repo you own) that Claude Code reads and writes through hooks.
Most of it runs by itself; this page says what runs on its own, what you type, and what to do when something says no.

---

## 1. What happens without you typing anything

| When | What gowth-mem does |
|---|---|
| A session starts | Pulls the vault (`auto-sync --pull-only`), delivers and commits the shipped rules canon, refreshes the managed block of `MEMORY.md` (rules, handoff, **open questions**, topics, secret pointers) |
| You send a prompt | If the prompt **names a topic that has a card**, injects that card once per session; if a curated entry really matches the prompt, injects up to 3 entries (≤ 2,000 chars). A corrected entry arrives together with its correction. Most prompts get nothing — that is by design |
| A turn ends | Logs the turn to `journal/sessions/<date>-<sid8>.md`; every 10 real turns asks Claude to launch a background **memory teammate** that saves the durable facts; every 15 turns a fresh-context **self-review**; **once a day per workspace, the curator** (section 3) |
| `/compact` | Dumps the recent human turns into today's journal, commits, then pulls/rebases/pushes |

Everything the hooks inject is labelled as data from your vault, not instructions. Bot and pipeline prompts are
ignored (section 8).

---

## 2. Install or upgrade

**New machine**

```text
/plugin marketplace add OoOshisuiOoO/gowth-mem
/plugin install gowth-mem@gowth-mem
```

Restart Claude Code, then run `/mem-install` (creates `~/.gowth-mem`, asks for the git remote, branch and token
strategy, pushes the first commit). Second machine: `git clone <remote> ~/.gowth-mem`, then `/mem-ops config`.

**Upgrade an existing machine to v4.9** — all three, in Claude Code:

```text
/plugin marketplace update gowth-mem
/plugin update gowth-mem@gowth-mem
/reload-plugins
```

`marketplace update` alone only refreshes the catalogue; `/reload-plugins` (or a restart) is what loads the new
hooks. Check with `/mem-doctor` — it also heals a registry that still points at an old version.

The first session after the upgrade:

- writes `shared/memory-rules.md` (+ the research canon) and a `.gitattributes` line, and **commits** them;
- re-renders `MEMORY.md` with the 12-line rules digest;
- makes the curator due: its first run reads the session turns of the last 7 days (`curator.first_window_days`) that
  no worklog has covered yet — existing session logs count, so it can start on the first turn of the day.

**Recommended once per project:** `/mem-setup native` — loads memory through Claude Code's own `MEMORY.md`
(re-attached after every compaction) instead of the hook bootstrap. `/mem-cost` then shows `mode=native`.

**Two machines with the same hostname?** Put a different `"host_id"` in each machine's `~/.gowth-mem/config.json`
(the curator picks one host per workspace by that name).

---

## 3. The daily curator — keeps knowledge from going stale

Once a day per workspace, after a normal turn, Claude is told to launch ONE background subagent,
`gowth-mem:curator`. You keep working; it reports back with one line:

```text
curator ws=devops: 3 facts, 1 corrections, 2 cards, 2 questions (+2/-1), 1 researched — workspaces/devops/docs/worklog/2026-11-08-mac-3f2a9c.md
ask Q-1a2b3c4d: Handoff item '[next] migrate the ingress to v2' looks done — tick it?
```

What it does with the turns no earlier run has covered:

1. reviews the entries written that day; a wrong one gets a **correction** (it never edits or deletes);
2. adds durable facts the teammate missed (unverified ones as `[hypothesis] … Verify: …`);
3. refreshes up to 5 **topic cards**;
4. opens questions for contradictions and gaps, researches up to 2 `research` questions, and turns stale handoff
   items into **"looks done — tick it?"** `ask` questions (it never edits the handoff);
5. writes a **worklog** with evidence: `docs/worklog/<date>-<host>-<run>.md`.

It writes only through the plugin's gated commands. It never runs git, never edits files by hand, and never launches
other agents.

**When it runs:** the first real turn of the day after `curator.hour` (default 0), when the workspace has at least
`curator.min_new_turns` (5) unprocessed turns, and only if nothing else was asked of Claude in that turn. It never
runs in `claude -p` / SDK sessions. One machine curates a workspace (another takes over after 2 days of silence).

**Commands**

```text
/mem-ops curate              # status per workspace: last run, misses, open questions, cards TODO, unprocessed turns
/mem-ops curate prep         # preview the digest it would read (writes nothing)
/mem-ops curate now          # run it now from this session (one background subagent)
```

**Answering its `ask` lines:** reply in chat, or

```text
/mem-ops gap answer Q-1a2b3c4d "yes, done on 2026-11-07" --evidence user
```

For a "tick it?" question Claude also marks the handoff item `[done]` when you say yes.

**Permissions.** In bypass mode nothing is needed. Otherwise a background agent cannot ask you, so its first denied
command ends the run (`curator ws=…: blocked — …`) and two such days pause it. Allow exactly its commands in
`~/.claude/settings.json` → `permissions.allow`. `/mem-ops curate now` prints the real paths; the shape is:

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

`<vault>` is the absolute vault path, `<plugin>` the plugin's install directory — it contains the version, so update
the rules after each upgrade. Add `"WebSearch"` / `"WebFetch(domain:<docs host>)"` if it may research on the web.
(Verified on Claude Code 2.1.301 in default permission mode.)

**Tuning / turning it off** — `~/.gowth-mem/settings.json`:

```jsonc
"curator": {
  "enabled": true,          // false = never dispatched (everything else keeps working)
  "hour": 0,                // earliest local hour of the day
  "min_new_turns": 5,
  "hosts": [],              // e.g. ["mac-studio"]: only these machines curate
  "model": "sonnet",
  "max_cards": 5, "max_research": 2, "max_research_tries": 3,
  "max_prep_chars": 80000   // digest size; ~100 session turns per run
}
```

If you see `[gowth-mem:curator-paused ws=…]`: two days were claimed but no worklog was written (no subagent tool,
a denied permission, the session ended first). Run `/mem-ops curate now` in a longer session.

---

## 4. "Must research" — the gap ledger

When Claude does not know something and cannot find it now, it becomes a tracked question in
`<ws>/docs/questions.md` instead of a guess:

- `research` — an agent can find the answer (vault, code, official docs, web);
- `ask` — only you know (intent, ownership, policy, "did we already do X?").

Open questions appear in `MEMORY.md` under `## Open questions` (asks first), so every session sees them.

```text
/mem-ops gap add research "Does pelican-router v2 support gRPC health checks?" --why "the v2 migration depends on it"
/mem-ops gap add ask "Which AWS account should the staging bucket live in?"
/mem-ops gap list                   # open, oldest first
/mem-ops gap list --closed          # history with answers
/mem-ops gap answer Q-xxxxxxxx "<answer>" --evidence "<vault path of the [ref] entry | URL | commit | user>"
/mem-ops gap tried Q-xxxxxxxx --note "no primary source yet"
/mem-ops gap close Q-xxxxxxxx --reason obsolete --evidence "the feature was dropped"
```

Answering a `research` question: first save the fact as a `[ref]` with `Source:` (section 6), then answer with the
path that save printed (a URL or a commit also works). A bare file name or `file:line` is refused as
`weak_evidence`; an `ask` question is answered with `--evidence user`. The curator researches up to 2 questions per
run and turns a question it failed 3 times into an `ask`.

If the vault has `gate.english_only` on, questions and entries must be written in English.

---

## 5. Topic cards — the current truth of a topic

A card lives inside the topic's `00-README.md` (in its TL;DR): five short sections, at most 2,000 characters.

```text
### Core        what the topic is now (1–3 lines)
### State       dated facts, newest first
### Routing     question → which file to open first
### Gotchas     rules that were paid for, with dates
### Sources     path:line, URL or commit
```

```text
/mem-topic card pelican-router              # show
/mem-topic card pelican-router set          # Claude writes/replaces it (the old card is archived in .history/)
/mem-topic card pelican-router summary "Ingress gateway on the edge cluster; v1 until gRPC checks land"
/mem-topic card check                       # status of every topic: ok, none, todo-summary, stale, over-budget, invalid
```

A card is injected when a prompt **names** the topic — its slug (`pelican-router`), the slug as words
(`pelican router`) or an alias — once per session. Generic words never trigger one (`test`, `config`, `next step` …).
Cards are written only through these commands (MOC rebuilds never touch them); the curator refreshes up to 5 per run.
If a command answers `rejected:<reason>`, nothing was written — the reason says what to fix.

---

## 6. Saving and correcting memory

Save one entry (gate + routing + dedup + tags + index):

```text
/mem-save [decision] "Keep pelican-router v1 until v2 has gRPC health checks, because v2 drops the legacy probe"
/mem-save [ref] "pelican-router batch upstream timeout is 30s. Source: deploy/pelican.yaml@3f2a9c81d0e4" --topic pelican-router
/mem-save            # no arguments: distill this whole session into topic files
```

Each type has a required part: `[decision]` a reason ("because …"), `[ref]` a `Source:`, `[tool]` a version or exact
syntax, `[hypothesis]` a `Verify:`, `[goal]` a `Status:` and `Done when:`. The answer is `written`, `duplicate`
(already there — nothing to do) or `rejected:<rule>` (fix what the rule names and save again).

**Something in memory is wrong?** Never edit or delete it. Save a correction of the same type that names it:

```text
/mem-save [decision] "Correction: batch upstream timeout 60s → 30s (2026-11-07; Corrects: workspaces/demo/pelican-router/2026-10-20-timeouts.md; Source: deploy/pelican.yaml@3f2a9c81d0e4)"
```

Recall then shows `(corrected <date>: <path>)` next to the old entry and brings the correction with it. Keep the
type's required part in the correction; a `[hypothesis]` that is now settled becomes a `[ref]`. Refresh the card too.

**Find things:** `/mem-recall <question>` (BM25, no LLM; `--topic <slug>`, `--keyword <tag>`, `--days N`,
`--type decision|ref|…`).

The full rulebook Claude follows — lookup order, evidence labels (CONFIRMED / UNVERIFIED / FALSE POSITIVE), where
each kind of knowledge goes — is `~/.gowth-mem/shared/memory-rules.md`. Edit it if you like: an edited copy is never
overwritten by later releases.

---

## 7. Every day, in practice

- Start of a session: glance at `MEMORY.md`'s **Open questions** — answer the `ask` items you can
  (`/mem-ops gap answer <id> "<answer>" --evidence user`).
- When Claude says it does not know: it should either find out or add a `research` / `ask` question — say so if it
  guesses instead.
- When you correct Claude, it records a correction; the curator also catches corrections it missed.
- End of a session: `/mem-handoff` (or just tell Claude to update the handoff).
- Once in a while: `/mem-ops curate` (status), `/mem-topic card check`, `/mem-cost`.

---

## 8. Bots, pipelines and `claude -p`

- `GOWTH_MEM_OFF=1` in a process's environment turns every gowth-mem hook off for it (nothing is read or written).
  Use it for bots that drive Claude Code (tmux sessions, `claude -p` stages):

  ```bash
  GOWTH_MEM_OFF=1 claude -p "…"
  ```

- Without it, prompts that look like pipeline stages are still ignored (not counted, logged, recalled or dumped):
  `STAGE=…`, `### TASK … ###`, `### DAILY … ###`, Claude Code's session summarizer — also inside a pasted
  `<pasted_content id=…>` block. Add your own bot's prefixes in settings:

  ```jsonc
  "capture": { "pipeline_prefixes": ["[deploy-bot]", "Nightly report for"] }   // plain prefixes, not regexes
  ```

---

## 9. Recovering entries lost by older versions

Before v4.9, archiving old topic aspects kept only `- [type]` bullets in `lessons.md`; `## [type]` blocks and bare
`[type]` lines stayed only in the machine-local `.archive/` and git history. v4.9 salvages every shape before it
archives. To bring the already-archived entries back (run it on every machine that archived aspects):

```text
/mem-ops forget --resalvage-archive --dry-run     # counts only
/mem-ops forget --resalvage-archive               # appends them to each topic's lessons.md
/mem-topic regen-index
```

It deletes, moves and rewrites nothing in `.archive/`, skips entries already present, and a second run adds nothing.

---

## 10. Troubleshooting

| You see | Do |
|---|---|
| `curator ws=…: blocked — <command> …` | add the allow rules in section 3 (or use bypass mode), then `/mem-ops curate now` |
| `[gowth-mem:curator-paused ws=…]` | `/mem-ops curate now` in a longer session |
| No curator run for days | `/mem-ops curate` — it says why (fewer than 5 new turns, another host curates, `enabled: false`, a `hosts` list without this machine) |
| `rejected:weak_evidence` | a `research` answer needs the `[ref]` entry's vault path, a URL or a commit |
| `rejected:not_english` | `gate.english_only` is on: write the entry in English |
| `rejected:control_token` / `rejected:private_tag` | the text quotes a `[gowth-mem:` directive or a `<private>` tag — rephrase |
| `rejected:lock_timeout` | another session was writing — run the same command again |
| A card is never injected | the prompt must name the topic (slug or slug words); `/mem-topic card check` must say `ok` |
| `SYNC-CONFLICT.md` reminder | `/mem-sync resolve` (it finishes the rebase with `_conflict.py --continue`) |
| Hooks behave like the old version | `/reload-plugins`, then `/mem-doctor` |
| Something looks off in the vault | `/mem-ops validate`, `/mem-ops gate --scan`, `/mem-cost` |
