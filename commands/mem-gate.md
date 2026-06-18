---
description: Hard write-rules quality gate — scan existing memory for junk entries that violate the data-quality canon, or check a single entry. Deterministic (no LLM) — rejects placeholders, too-short, hedged-without-evidence, ref-without-Source, decision-without-rationale, tool-without-version, and secret leaks. The code-level enforcement so the AI cannot push junk data.
---

The hard write-rules gate (`_gate.py`) is the **code-level enforcement** of the data-quality canon §1. It runs automatically inside `_topic.append_entry` and `_lesson.append_lesson`, so junk is rejected *before* it lands. This command lets you **scan existing files** for entries that would be rejected, or **check** a candidate entry.

Scan the whole vault for junk (non-destructive report):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_gate.py" --scan --all
```

Scan the active workspace, or get file:line detail as JSON:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_gate.py" --scan
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_gate.py" --scan --all --json
```

Check one entry (exit 0 = accept, 1 = reject):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_gate.py" --check '[ref] sqlite WAL allows concurrent readers. Source: https://sqlite.org/wal.html'
```

## The hard rules (deterministic, no LLM)

| Reject reason | Trigger |
|---|---|
| `secret_leak` | AKIA / `sk-` / `ghp_` / `xox` / JWT / PEM pattern → BLOCK (store a pointer, never the value) |
| `placeholder` | body is `todo / tbd / fixme / misc / random / stuff / ...` |
| `too_short` | body < 20 chars after stripping `[tag]` |
| `hedged_no_evidence` | `maybe/i think/probably/seems/might` AND no Source/`code`/path/URL |
| `ref_without_source` | `[ref]` with no `Source:` and no URL |
| `decision_without_rationale` | `[decision]` with no `because/since/rationale/why/vì/để` |
| `tool_without_version_or_syntax` | `[tool]` with no version and no `` `command` `` |

The non-deterministic rules (poignancy, durability, multi-claim splitting) stay agent discipline — see `shared/research/extraction-reuse-2026.md` §3 and `data-quality-2026.md` §1.

## Settings

`settings.json` → `gate.enabled` (default true) turns the write-path gate on/off; `gate.strict` (default true) toggles the per-type schema rules (rules 5-7). The base rules (secret/placeholder/too-short) always apply when enabled.
