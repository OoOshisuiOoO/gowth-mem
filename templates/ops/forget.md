---
description: Active forgetting — archive raw journal transcript past its TTL (default 7 days) out of active recall, after salvaging any curated [type] entries. The brain-like "synaptic pruning" step that keeps memory lean so the AI actually reads it. Recoverable via .archive/ + git history.
---

Run active forgetting over journals. Journals are the **ephemeral working-memory buffer** (data-quality canon §3): raw `[auto-precompact-dump]` transcript lives ~7 days, then the signal is distilled into topic files and the raw is forgotten. Without this step journals grow unbounded (observed: a single journal reached 1.8 MB / 26,812 lines and the agent stopped reading it).

Run with the Bash tool:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_forget.py" --all-workspaces "$@"
```

Pass `--dry-run` to preview without changing anything. `--ttl-days N` overrides the cutoff. `--no-salvage` skips lifting curated entries.

**v4.1 aspect retention**: `--aspects` (or settings `topic_layout.auto_archive_enabled: true` for the Stop-hook run) also archives **topic aspects older than `topic_layout.archive_threshold_days`** (default 90 = the ">3 months → archive" policy). v4.9: EVERY curated entry shape is salvaged into the topic's `lessons.md` first — `- [type]` bullets (and `1.` items), `## [type]` blocks (H3+ stay inside), bare `[type]` lines with their continuation — each followed by a `(salvaged from archived aspect <file>)` line; an aspect is archived ONLY when every one of its entries reads back from `lessons.md`, and a topic whose `entries-<ws>` lock is busy is reported as left for the next run. Before v4.9 only bullets were salvaged. Every topic keeps its newest 3 aspects regardless of age; `00-README.md`/`lessons.md` are never touched. Age = filename date (knowledge date), not mtime. After an aspect pass, regen MOCs with `/mem-topic --regen-index`.

**v4.9 recovery — `--resalvage-archive`**: aspects archived BEFORE v4.9 lost their `## [type]` blocks and bare `[type]` lines from the synced layer (they live only in this machine's `.archive/topics/` and git history). This reads every `.archive/topics/<ws>/<topic>/*.md.gz` and salvages all entry shapes into the live topic's `lessons.md` (same parser, SHA1 dedup and provenance; under the entries lock). It deletes, moves and rewrites nothing in `.archive/`; a topic whose folder no longer exists is listed and left alone; a re-run adds nothing. Always preview first:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_forget.py" --resalvage-archive --all-workspaces --dry-run
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_forget.py" --resalvage-archive --all-workspaces
```

Run it on every machine that archived aspects (the archive is machine-local), then `_moc.py --ws <ws>`.

## What it does (per workspace journal/ dir)

1. **SALVAGE** — lift any genuinely-curated entries (v4.9: `- [type]` bullets, `## [type]` blocks, bare `[type]` lines) from old journals into `<ws>/journal/_salvage.md` (SHA1-deduped, one provenance line each). Raw transcript prose has no such entry, so it is **not** salvaged — that's the noise being forgotten. Route salvaged lines into topic files via `/mem-distill`, then delete them from `_salvage.md`.
2. **ARCHIVE** — gzip journals older than `journal.raw_ttl_days` (settings.json, default 7) into `~/.gowth-mem/.archive/journal/<ws>/`, then remove the original. A journal also archives if it is >1 day old AND over `journal.max_bytes` (too big to read).

## Recoverability (nothing is lost)

- gzip copy under `.archive/journal/<ws>/<name>-<mtime>.md.gz` (gitignored, local cold storage)
- memory-repo git history — journals are committed before removal
- Restore: `gzip -d` the archive copy, or `git -C ~/.gowth-mem show <rev>:workspaces/<ws>/journal/<name>.md`

## Safety

- **Never** touches today's journal or any journal within the TTL window (the live buffer the agent is still using).
- Runs automatically on the Stop hook when `journal.auto_forget_enabled` is true (settings.json) — near-noop when nothing is past TTL. Disable by setting it to `false`.

Canon: `shared/research/data-quality-2026.md` §3 (retention TTL) + `.claude/research/v3.6-brain-storage.md` (why journals are ephemeral).
