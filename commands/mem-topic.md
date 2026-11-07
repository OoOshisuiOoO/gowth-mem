---
description: "List, inspect, route a snippet, or edit a topic's card"
---

Manage the topic registry under the active workspace (`~/.gowth-mem/workspaces/<ws>/`).

v3.0: a TOPIC is a FOLDER. Each `<slug>/` contains:
- `00-README.md` — MOC (TL;DR + Aspects auto + Cross-links manual); since v4.9 the TL;DR can hold the topic's **card**
- `YYYY-MM-DD-<aspect>.md` — dated aspect files (the actual content)
- `lessons.md` — per-topic 5-field ledger
- `.history/card-<sha12>.txt` — verbatim archive of each replaced card (synced, never indexed)

Subcommands (mutually exclusive):

- **list** (default if no args): show table `slug | title | aspects | last touched`. Run:
  ```bash
  python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_topic.py" --list
  ```

- **inspect `<slug>`**: open `~/.gowth-mem/workspaces/<ws>/<slug>/00-README.md` and show first 80 lines, followed by the names of all sibling dated aspect files (newest-first).

- **route `<text>`**: show which topic folder slug `_topic.route()` would pick and which dated aspect file it would append to:
  ```bash
  python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_topic.py" --route "<text>"
  ```
  Output format: `slug=<topic> aspect=<today-aspect> path=workspaces/<ws>/<slug>/YYYY-MM-DD-<aspect>.md`

- **regen-index**: regenerate `topics/_index.md` (workspace-wide topic listing) and every topic's `00-README.md` from current files:
  ```bash
  python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_moc.py" --ws "<ws>"
  ```

- **ensure `<slug>`**: create `workspaces/<ws>/<slug>/00-README.md` if missing, with the v3 topic-readme skeleton (frontmatter + TL;DR + Aspects auto + Cross-links manual). Use when manually starting a new topic:
  ```bash
  # Generic (renders topic-readme-skeleton.md):
  python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_topic.py" --ensure "<slug>"

  # With initial metadata:
  python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_topic.py" --ensure "<slug>" \
      --title "StarRocks FE OOM" --parents starrocks \
      --summary "FE JVM heap exhausted → metadata write fail"
  ```

  `ensure` only creates the topic FOLDER + `00-README.md` MOC. It does NOT spawn a dated aspect file. The first `[exp]`/`[ref]`/`[decision]` line routed in via `mems` creates today's `YYYY-MM-DD-<aspect>.md`.

- **card `<slug>`** `[show | set | summary "<line>" | check]` (v4.9): read or write the topic's curated card — see **Topic cards** below.

## Topic cards (v4.9)

A card is the CURRENT truth of a topic, kept inside its README's `## TL;DR` between `<!-- gowth-mem:card v=1 updated=YYYY-MM-DD -->` and `<!-- /gowth-mem:card -->`. MOC rebuilds keep it byte for byte while it sits inside the TL;DR (the first `## TL;DR` heading of the README — `## TL;DR:` or `## TL;DR — …` count too). A card a rebuild would drop (a hand edit put it below another `## ` heading, under `## Aspects (auto)`, or renamed the TL;DR) is first archived verbatim to `<slug>/.history/card-<sha12>.txt` (header `moc-rebuild`); when that archive cannot be written, the README is not rebuilt at all. `<ws>` is the active workspace unless the user names one. Never edit the card text or its markers by hand — every change goes through `_card.py` (the one exception: **Repairing a card** below). Run the commands exactly as below (the heredoc's closing `CARD` must start its line).

`card <slug>` / `card <slug> show` — print the card body (nothing when the topic has no card):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_card.py" --ws "<ws>" --topic "<slug>" --show
```

`card <slug> set` — replace the whole card body (from stdin; a file path instead of `-` works too). Keep the five H3 sections; dates and sources make it checkable:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_card.py" --ws "<ws>" --topic "<slug>" --set - <<'CARD'
### Core
- <1-3 lines: what this topic is, the current truth>
### State (as of YYYY-MM-DD)
- <dated current facts, newest first>
### Routing
| Question | Start | Then |
|---|---|---|
| <question> | [[<aspect-or-lessons>]] | <next file or check> |
### Gotchas
- <rule paid for, with the incident date>
### Sources
- <path:line | URL | commit>
CARD
```

`card <slug> summary "<line>"` — set the TL;DR one-liner above the card (replaces the TODO placeholder; one line, at most 160 chars):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_card.py" --ws "<ws>" --topic "<slug>" --summary "<one line>"
```

`card check` — the card status of every topic in the workspace (`--json` for detail):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_card.py" --ws "<ws>" --check
```

`--set` and `--summary` print `<readme>\t<status>`:
- `written` — saved; a replaced card was first archived verbatim to `<slug>/.history/card-<sha12>.txt`.
- `confirmed` — same body as before: only `updated=` moved to today (a reviewed card is no longer stale).
- `unchanged` — same body, already confirmed today.
- `rejected:<reason>` — NOTHING was written; fix what the reason names and run it once more:
  `no_topic` (only an existing topic folder with a `00-README.md` — never a domain, an ambiguous name, a symlink out of the vault, or a reserved workspace dir: `memory`, `journal`, `docs`, `skills`, `research`; nothing is created), `bad_ws`,
  `control_token` (text containing the hook prefix `[gowth-mem:`), `private_tag` (no `<private>` / `</private>` tag in any spelling — write "private-block tags" instead),
  `secret_leak` (store a pointer, never the value; ALSO when the README itself already holds text the write's privacy filter would redact — a hand-pasted secret, or a `<private>` … `</private>` pair across its sections: remove that span from the README by hand, then retry),
  `comment:<line>` (no `<!--`), `bad_heading:<line>` (H3 and deeper only — `#`/`##` would break the README), `entry_line:<line>`
  (no `[decision]`/`[ref]`/… entry lines — entries live in aspects; the card summarises them. A line counts as one the way the indexer reads it: after any whitespace (NBSP too), a bullet, `#` runs (`- ## [decision]`, `### - [exp]`), or a U+2028 / VT / FF line break),
  `forbidden:<word>` (never `(superseded` / `(deprecated` / `(obsolete` — write the current truth instead),
  `not_english` (with `gate.english_only`), `over_budget:<n>/<max>` (`topic_card.max_chars`, default 2000),
  `too_long` / `multiline` (summary), `tldr_heading` (the README's first `## TL;DR` is no heading line of its own — mid-line, a `### TL;DR`, inside the frontmatter: make the real TL;DR heading the first `## TL;DR` in the file),
  `markers_corrupt` (see **Repairing a card**), `archive_failed` (`.history/` is not writable — fix it, retry), `unreadable`, `lock_timeout` (retry).

`--check` statuses: `ok`, `none` (no card), `todo-summary`, `stale` (`updated=` older than the newest aspect), `over-budget`, `invalid` (broken markers or card lines), `legacy` (no `00-README.md`).

### Repairing a card

`--set` / `--summary` answer `rejected:markers_corrupt`, and `--check` (or `/mem-ops validate`) shows the finding. Edit the README AROUND the card — never the card text — then run `card check` again until the row is no longer `invalid`, and **regen-index** (`python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_moc.py" --ws "<ws>"`) to refresh the Aspects list:

- `card-outside-tldr` — the begin marker is not inside the TL;DR section. A hand-written `## ` heading between the TL;DR summary and the begin marker: move that heading (and its text) below the end marker, e.g. under `## Cross-links (manual)`. The `## TL;DR` heading renamed or removed: put `## TL;DR` back as the first `## TL;DR` line, above the card. The card (begin marker through end marker) under `## Aspects (auto)` or below `## Cross-links (manual)`: move it back right after the TL;DR summary. The end marker dragged below `## Aspects (auto)` / `## Cross-links (manual)`: move it back to right after the card's last line.
- `card-markers:unbalanced` — a marker line is missing: restore it on its own line (`<!-- gowth-mem:card v=1 updated=YYYY-MM-DD -->` before the card, `<!-- /gowth-mem:card -->` after it).
- `card-markers:duplicate` — two cards: copy the one to drop (begin through end marker) to `<slug>/.history/card-manual-YYYY-MM-DD.txt`, then remove it from the README.
- A card a MOC rebuild dropped is in `<slug>/.history/card-<sha12>.txt` (header `moc-rebuild`): put its body (the lines between the marker lines) back with `card <slug> set`.

## Routing rules (v3)

- The router uses keyword overlap (≥3 common words) against existing topic `00-README.md` TL;DR + Cross-links (never the card) and the topic's aspects.
- `_topic.py --append "<entry>" --topic <slug>` forces an EXISTING topic for one entry (dedup, gate, tags unchanged); `rejected:no_topic` when there is no such topic — nothing is created.
- New slugs come from the top-2 distinctive keywords.
- Default fallback topic: `misc` (configurable via `settings.topic_routing.default_topic`).
- Default fallback aspect: `note` (configurable via `settings.topic_routing.default_aspect`).
- Reserved subdirs blocked as topic slugs: `docs`, `journal`, `skills`, `research`, `memory` (Claude Code's auto-memory dir) — also for `--topic` (`_topic.py --append`, `_lesson.py`, `_card.py`), by name or through a symlink: `rejected:no_topic` / `not appended`.
- Reserved aspect names blocked: `readme`, `lessons`, `00-readme`.

## Side-channels

- `[secret-ref] <env-var>` lines bypass topic routing → appended to `shared/secrets.md` (one file for every workspace, so every append takes the single `shared-entries` lock).
- `[skill-ref] <slug>` lines bypass topic routing → appended to `workspaces/<ws>/skills/<slug>.md`.
- `[lesson]` entries are written via `/mem-lesson`, not `/mem-topic`. They land in `<slug>/lessons.md`.
