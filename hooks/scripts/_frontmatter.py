"""Tiny YAML-frontmatter parser/writer for v2.2 topic + MOC files.

Schema is small (slug, title, status, parents, links, aliases, last_touched, type, workspace, folder, last_rebuilt) and we don't need full PyYAML — a regex-based scalar/list parser handles every value gowth-mem writes.

If we ever need richer types we can swap in pyyaml; for now keep deps zero.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_FRONT_RE = re.compile(r"^---\s*\n(?P<body>.*?)\n---\s*\n?", re.DOTALL)


def _split_csv_quoted(s: str) -> list[str]:
    """Split a YAML-ish flow list on commas while respecting quoted strings.

    Handles single and double quotes, including escaped quote pairs ('' or "")
    that YAML uses for embedded quotes.
    """
    parts: list[str] = []
    buf: list[str] = []
    in_quote: str | None = None
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if in_quote:
            buf.append(ch)
            if ch == in_quote:
                # YAML doubled-quote escape
                if i + 1 < n and s[i + 1] == in_quote:
                    buf.append(s[i + 1])
                    i += 2
                    continue
                in_quote = None
        elif ch in ('"', "'"):
            in_quote = ch
            buf.append(ch)
        elif ch == ",":
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
        i += 1
    if buf:
        parts.append("".join(buf).strip())
    cleaned: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if (p[0] == '"' and p[-1] == '"') or (p[0] == "'" and p[-1] == "'"):
            p = p[1:-1]
        cleaned.append(p)
    return cleaned


def _parse_value(raw: str) -> Any:
    raw = raw.strip()
    if not raw:
        return ""
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return _split_csv_quoted(inner)
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        return raw[1:-1]
    return raw


def parse(text: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body). Empty dict if no frontmatter."""
    m = _FRONT_RE.match(text)
    if not m:
        return {}, text
    body_start = m.end()
    fm: dict[str, Any] = {}
    for line in m.group("body").splitlines():
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue
        kv = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if not kv:
            continue
        key = kv.group(1)
        fm[key] = _parse_value(kv.group(2))
    return fm, text[body_start:]


def parse_file(path: Path) -> tuple[dict, str]:
    try:
        return parse(path.read_text(errors="ignore"))
    except Exception:
        return {}, ""


def _emit_value(v: Any) -> str:
    if isinstance(v, list):
        if not v:
            return "[]"
        return "[" + ", ".join(str(x) for x in v) + "]"
    if v is None:
        return ""
    return str(v)


def render(fm: dict, body: str) -> str:
    """Inverse of parse: render a markdown file with frontmatter block."""
    if not fm:
        return body
    lines = ["---"]
    for k, v in fm.items():
        lines.append(f"{k}: {_emit_value(v)}")
    lines.append("---")
    head = "\n".join(lines) + "\n\n"
    if not body.startswith("\n"):
        head = head[:-1]
    return head + body


def update(path: Path, **changes: Any) -> None:
    """Read file, merge changes into frontmatter, atomic-write back."""
    from _atomic import atomic_write  # local import: avoid cycle on bare import

    fm, body = parse_file(path)
    fm.update(changes)
    atomic_write(path, render(fm, body))
