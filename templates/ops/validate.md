---
description: "File-level schema validator (learned from supremor/vault-keeper). Checks every topic file's frontmatter required-fields, naming (slug regex), and reserved-path placement — the structural layer that _gate.py (entry-level) doesn't cover — and finds README-only junk topic folders. --fix deterministically repairs aspect frontmatter from the path; --prune-junk deletes the junk folders the scan lists. Keeps wikilinks/recall/MOC working."
---

Validate the structural conformance of memory files. Complements `/mem-ops gate` (which checks entry *content*) by checking file *structure* — the discipline adapted from the TrueProfit `supremor` vault's `claude-code-vault-keeper` validator.

Scan (read-only report):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_validate.py" --scan --all
```

Active workspace only, or JSON detail:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_validate.py" --scan
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_validate.py" --scan --all --json
```

Fix (deterministically add/repair aspect frontmatter — all fields derive from the path, content preserved):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_validate.py" --fix --all
```

Prune junk README-only folders — the `junk-topic-folder` entries the scan lists, nothing else
(explicit on purpose: it deletes; see below):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_validate.py" --prune-junk --all
```

## What it checks (per v3 file type)

| File | Required |
|---|---|
| `<slug>/00-README.md` (MOC) | frontmatter: `slug, title, type, status` (auto-fixed by rebuilding the MOC: `_moc.py --ws X`) |
| `<slug>/YYYY-MM-DD-<aspect>.md` (aspect) | frontmatter: `type: aspect, date, topic, slug, title` |
| `<slug>/lessons.md` | has at least one `## ` entry heading |
| naming | topic slug + aspect slug match `^[a-z0-9][a-z0-9-]{0,59}$` |
| placement | topic files live inside a topic folder (not workspace root, not a reserved subdir) |
| junk folder (v4.7.6) | `junk-topic-folder`: a top-level folder holding only an untouched skeleton `00-README.md`, named after the frontmatter `slug:` of a file in another folder |
| abandoned prune (v4.7.6) | `abandoned-prune`: a README a killed `--prune-junk` left held aside as `.00-README.md.pruning-<pid>` — the next `--prune-junk` puts it back |

`--fix` repairs aspect files only (frontmatter derivable from path). For MOC field gaps, rebuild the MOC.

Junk folders: before v4.7.6 the router took a topic name from any file's frontmatter `slug:`
(for an aspect `<topic>-<aspect>`) and minted `<ws>/<topic>-<aspect>/00-README.md` beside the
real topic. Machines on an older version keep minting them until upgraded. `--prune-junk`
deletes one only when ALL hold: a real (non-symlink) folder at the workspace root; nothing in it
but a plain `00-README.md` (a `.DS_Store` is tolerated); that README is the pristine DEFAULT
skeleton, frontmatter included (`type: misc`, `status: draft`, the default title, no aliases /
tags / links — only the dates may differ); and its name is the `slug:` of a file in another
folder. The README is renamed aside and re-verified before it is deleted (an edit that races the
prune is restored), files are unlinked one by one, the folder removed with `rmdir` (never
recursively), and `_MAP.md` rebuilt. With `--fix --prune-junk` the prune runs FIRST — a slug that
`--fix` stamps in the same run is no evidence. Any curation (an edited line, a title, a type, an
alias), an extra file such as a stranded `lessons.md`, a symlink, or a nested folder keeps it.
The one case this cannot tell apart: a never-touched default skeleton you created on purpose,
named exactly like another file's slug — the scan lists it first; give it a title or summary to
keep it. Non-conformant frontmatter makes a file invisible to `[[wikilinks]]`, the recall layer's scoring, and the auto-MOC — so keeping this clean matters.

## Topic-card findings (v4.9)

Reported for a `00-README.md` only when it holds a card marker (`--cards` also lists `card-none` — no card — and
`card-todo-summary` — the TL;DR one-liner is missing or still the TODO placeholder). The same rows come from
`/mem-topic card check`. Nothing here is auto-fixed:

| Finding | Meaning | Repair |
|---|---|---|
| `card-markers:unbalanced` / `card-markers:duplicate` | a begin or end marker line is missing, or there are two cards | edit the markers back by hand — see `/mem-topic` **Repairing a card** |
| `card-outside-tldr` | the begin marker is not inside the README's first `## TL;DR` section (a hand-written `## ` heading above it, a renamed TL;DR, the card under `## Aspects (auto)`), or the end marker swallowed those sections | move the heading or the card back — see `/mem-topic` **Repairing a card**; until then `card set` / `card summary` answer `rejected:markers_corrupt` |
| `card-bad-heading:<line>` | an `#` / `##` line (or a `## Cross-links (manual)` mention) inside the card | rewrite the card with `card <slug> set` (H3 and deeper only) |
| `card-entry-line:<line>` | a `[decision]` / `[ref]` / … line the indexer would tag as an entry — also after NBSP or other Unicode whitespace, `- ## [x]`, `### - [x]`, or a U+2028 / VT / FF line break inside the line | rewrite the card with `card <slug> set` without entry lines (entries live in aspects) |
| `card-over-budget:<n>/<max>` | the body is longer than `topic_card.max_chars` (default 2000) | shorten it with `card <slug> set` |
| `card-control-token` | the body holds `[gowth-mem:` | rewrite the card with `card <slug> set` |

`<line>` is the README line number. A card that a MOC rebuild (`_moc.py`, also run after every `/mem-lesson`) would
drop — it sits outside the TL;DR section the rebuild keeps — is first archived verbatim to
`<topic>/.history/card-<sha12>.txt` (header `moc-rebuild`); when that archive cannot be written the README is left
unrebuilt and `_moc.py` prints a `WARN` naming it. Restore an archived card's body with `/mem-topic card <slug> set`.

Background: `.claude/research/v3.7-supremor-comparison.md`.
