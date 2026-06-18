---
description: Active forgetting — archive raw journal transcript past its TTL (default 7 days) out of active recall, after salvaging any curated [type] entries. The brain-like "synaptic pruning" step that keeps memory lean so the AI actually reads it. Recoverable via .archive/ + git history.
---

Run active forgetting over journals. Journals are the **ephemeral working-memory buffer** (data-quality canon §3): raw `[auto-precompact-dump]` transcript lives ~7 days, then the signal is distilled into topic files and the raw is forgotten. Without this step journals grow unbounded (observed: a single journal reached 1.8 MB / 26,812 lines and the agent stopped reading it).

Run with the Bash tool:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_forget.py" --all-workspaces "$@"
```

Pass `--dry-run` to preview without changing anything. `--ttl-days N` overrides the cutoff. `--no-salvage` skips lifting curated entries.

## What it does (per workspace journal/ dir)

1. **SALVAGE** — lift any genuinely-curated bullet entries (`- [decision]`, `- [ref]`, `- [tool]`, `- [exp]`, …) from old journals into `<ws>/journal/_salvage.md` (SHA1-deduped). Raw transcript prose has no such bullet, so it is **not** salvaged — that's the noise being forgotten. Route salvaged lines into topic files via `/mem-distill`, then delete them from `_salvage.md`.
2. **ARCHIVE** — gzip journals older than `journal.raw_ttl_days` (settings.json, default 7) into `~/.gowth-mem/.archive/journal/<ws>/`, then remove the original. A journal also archives if it is >1 day old AND over `journal.max_bytes` (too big to read).

## Recoverability (nothing is lost)

- gzip copy under `.archive/journal/<ws>/<name>-<mtime>.md.gz` (gitignored, local cold storage)
- memory-repo git history — journals are committed before removal
- Restore: `gzip -d` the archive copy, or `git -C ~/.gowth-mem show <rev>:workspaces/<ws>/journal/<name>.md`

## Safety

- **Never** touches today's journal or any journal within the TTL window (the live buffer the agent is still using).
- Runs automatically on the Stop hook when `journal.auto_forget_enabled` is true (settings.json) — near-noop when nothing is past TTL. Disable by setting it to `false`.

Canon: `shared/research/data-quality-2026.md` §3 (retention TTL) + `.claude/research/v3.6-brain-storage.md` (why journals are ephemeral).
