---
description: "File-level schema validator (learned from supremor/vault-keeper). Checks every topic file's frontmatter required-fields, naming (slug regex), and reserved-path placement — the structural layer that _gate.py (entry-level) doesn't cover. --fix deterministically repairs aspect frontmatter from the path. Keeps wikilinks/recall/MOC working."
---

Validate the structural conformance of memory files. Complements `/mem-gate` (which checks entry *content*) by checking file *structure* — the discipline adapted from the TrueProfit `supremor` vault's `claude-code-vault-keeper` validator.

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

## What it checks (per v3 file type)

| File | Required |
|---|---|
| `<slug>/00-README.md` (MOC) | frontmatter: `slug, title, type, status` (auto-fixed by rebuilding the MOC: `_moc.py --ws X`) |
| `<slug>/YYYY-MM-DD-<aspect>.md` (aspect) | frontmatter: `type: aspect, date, topic, slug, title` |
| `<slug>/lessons.md` | has at least one `## ` entry heading |
| naming | topic slug + aspect slug match `^[a-z0-9][a-z0-9-]{0,59}$` |
| placement | topic files live inside a topic folder (not workspace root, not a reserved subdir) |

`--fix` repairs aspect files only (frontmatter derivable from path). For MOC field gaps, rebuild the MOC. Non-conformant frontmatter makes a file invisible to `[[wikilinks]]`, the recall layer's scoring, and the auto-MOC — so keeping this clean matters.

Background: `.claude/research/v3.7-supremor-comparison.md`.
