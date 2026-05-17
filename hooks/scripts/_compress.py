#!/usr/bin/env python3
"""Pre-storage compression — rtk-inspired token reduction.

Run BEFORE writing journal/aspect/log content to disk. Conservative by
default: only collapses obvious repetition that adds tokens without info.

Two passes:
  1. ``collapse_repeats(text, min_repeat=3)``
       3+ adjacent identical lines → ``<line>  (×N)``
  2. ``group_by_prefix(lines, max_per_group=5)``
       Adjacent lines sharing a ``key: value`` prefix get merged into
       ``key: [N items: a, b, c, ...]`` when the run is >= max_per_group.

``compress_block(text, mode)`` applies both. Mode is informational only
today (``"journal"`` | ``"aspect"`` | ``"log"``) — kept as a hook for future
mode-specific tuning.

No LLM. Pure stdlib. Idempotent (running it twice gives the same output).
"""
from __future__ import annotations

import re


_PREFIX_RE = re.compile(r"^(\s*[-*]?\s*[A-Za-z][\w.-]*)\s*[:=]\s*(.+)$")
_DEFAULT_MIN_REPEAT = 3
_DEFAULT_MAX_PER_GROUP = 5


def collapse_repeats(text: str, min_repeat: int = _DEFAULT_MIN_REPEAT) -> str:
    """Collapse runs of ``min_repeat``+ adjacent identical lines.

    ``foo\\nfoo\\nfoo\\nfoo`` (4 lines) → ``foo  (×4)`` (1 line).
    Trailing newline is preserved. Empty lines are never collapsed.
    """
    if min_repeat < 2:
        raise ValueError("min_repeat must be >= 2")
    lines = text.splitlines()
    if not lines:
        return text
    out: list[str] = []
    i = 0
    while i < len(lines):
        cur = lines[i]
        j = i + 1
        while j < len(lines) and lines[j] == cur:
            j += 1
        run = j - i
        if run >= min_repeat and cur.strip():
            out.append(f"{cur}  (×{run})")
        else:
            out.extend([cur] * run)
        i = j
    suffix = "\n" if text.endswith("\n") else ""
    return "\n".join(out) + suffix


def _extract_prefix(line: str) -> tuple[str, str] | None:
    m = _PREFIX_RE.match(line)
    if not m:
        return None
    return m.group(1).strip(), m.group(2).strip()


def group_by_prefix(text: str, max_per_group: int = _DEFAULT_MAX_PER_GROUP) -> str:
    """Merge adjacent ``key: value`` lines sharing the same key.

    Runs of length >= ``max_per_group`` collapse into a single
    ``key: [N items: v1, v2, v3, ...]`` line. Shorter runs are preserved
    verbatim.
    """
    if max_per_group < 2:
        raise ValueError("max_per_group must be >= 2")
    lines = text.splitlines()
    if not lines:
        return text
    out: list[str] = []
    i = 0
    while i < len(lines):
        pref = _extract_prefix(lines[i])
        if pref is None:
            out.append(lines[i])
            i += 1
            continue
        key, _ = pref
        values: list[str] = []
        j = i
        while j < len(lines):
            np = _extract_prefix(lines[j])
            if not np or np[0] != key:
                break
            values.append(np[1])
            j += 1
        run = j - i
        if run >= max_per_group:
            head = ", ".join(values[: max_per_group])
            tail_n = run - max_per_group
            tail = f", +{tail_n} more" if tail_n > 0 else ""
            out.append(f"{key}: [{run} items: {head}{tail}]")
        else:
            out.extend(lines[i:j])
        i = j
    suffix = "\n" if text.endswith("\n") else ""
    return "\n".join(out) + suffix


def compress_block(text: str, mode: str = "journal",
                   min_repeat: int = _DEFAULT_MIN_REPEAT,
                   max_per_group: int = _DEFAULT_MAX_PER_GROUP) -> str:
    """Apply collapse_repeats + group_by_prefix in sequence.

    Returns the compressed text. Conservative: if both passes are no-ops,
    the original string is returned unchanged.
    """
    step1 = collapse_repeats(text, min_repeat=min_repeat)
    step2 = group_by_prefix(step1, max_per_group=max_per_group)
    return step2


def main() -> int:
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="rtk-style pre-storage compressor")
    ap.add_argument("--mode", default="journal", choices=["journal", "aspect", "log"])
    ap.add_argument("--min-repeat", type=int, default=_DEFAULT_MIN_REPEAT)
    ap.add_argument("--max-per-group", type=int, default=_DEFAULT_MAX_PER_GROUP)
    ap.add_argument("file", nargs="?", help="file to compress; reads stdin if omitted")
    args = ap.parse_args()

    if args.file:
        with open(args.file, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read()
    else:
        raw = sys.stdin.read()

    out = compress_block(raw, mode=args.mode,
                         min_repeat=args.min_repeat,
                         max_per_group=args.max_per_group)
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
