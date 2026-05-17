---
description: rtk-style pre-storage compression — collapses 3+ adjacent identical lines + merges adjacent `key: value` runs sharing the same key. Deterministic, idempotent, no LLM. Use before writing large journal/aspect blocks.
---

Compress a markdown chunk before writing it to memory. Strips obvious token-waste without altering semantics.

Run with the Bash tool:

```bash
# from file
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_compress.py" "$@" path/to/file.md

# or pipe
cat path/to/file.md | python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_compress.py" "$@" > compressed.md
```

## Two passes

1. **collapse_repeats** (default min_repeat=3): runs of 3+ identical adjacent lines collapse to `<line>  (×N)`.
2. **group_by_prefix** (default max_per_group=5): runs of `key: value` lines sharing the same key collapse into `key: [N items: v1, v2, v3, +K more]`.

## Flags

- `--mode {journal,aspect,log}` — currently informational only (hook for future tuning).
- `--min-repeat N` — minimum run length for collapse_repeats (default 3, must be >=2).
- `--max-per-group N` — minimum run length for group_by_prefix (default 5, must be >=2).

## Properties

- **Idempotent**: running it twice gives the same output.
- **Conservative**: short runs are kept verbatim; empty lines never collapse.
- **No LLM**: pure stdlib, deterministic, zero token spend.

## When to use

- After raw log dumps before writing into a journal entry
- After concatenating multiple aspect drafts before promotion
- Manually before `/mem-save` if you know the input is bursty
