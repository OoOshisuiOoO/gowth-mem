"""Tiny YAML-frontmatter parser/writer for v2.2 topic + MOC files.

Schema is small (slug, title, status, parents, links, aliases, last_touched, type, workspace, folder, last_rebuilt) and we don't need full PyYAML — a regex-based scalar/list parser handles every value gowth-mem writes.

If we ever need richer types we can swap in pyyaml; for now keep deps zero.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_FRONT_RE = re.compile(r"^---\s*\n(?P<body>.*?)\n---\s*\n?", re.DOTALL)


def _parse_value(raw: str) -> Any:
    raw = raw.strip()
    if not raw:
        return ""
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [s.strip().strip("\"'") for s in inner.split(",") if s.strip()]
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
