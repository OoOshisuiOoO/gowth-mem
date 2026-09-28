#!/usr/bin/env python3
"""v4.8: wire Claude Code's auto-memory to the vault (per project), import the
machine-local memory directories, report status.

Measured on Claude Code 2.1.283 (2026-09-28): `autoMemoryDirectory` is honoured
from a project's `.claude/settings.local.json` and names the memory directory
ITSELF — Claude writes its topic files and updates MEMORY.md right there. With
the value pointing at `<vault>/workspaces/<ws>/memory`, Claude's own memory
lands in the git-synced vault and every project mapped to the same workspace
shares it; gowth-mem keeps the managed block at the top of MEMORY.md
(`_memfile.py`).

Rules:
  * wiring is an explicit one-time command per machine (`/mem-setup native`),
    never a hook side effect;
  * a different existing value is a `conflict` unless --force;
  * a git-tracked settings.local.json is never written (`tracked`);
  * invalid JSON is never overwritten (`invalid`);
  * import runs dry by default; files pass through the privacy sanitizer;
    a name clash keeps the vault copy and writes the incoming file as
    `<name>.from-<host>.md`; unmapped project slugs are listed, never guessed.

CLI:
  python3 _native.py wire   [--dry-run] [--force] [--project DIR --ws WS]
  python3 _native.py import [--apply]
  python3 _native.py status
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import atomic_write, safe_write  # type: ignore
from _debug import log_debug  # type: ignore
from _home import active_workspace, read_config  # type: ignore
from _memfile import memfile_path, memory_dir, split  # type: ignore

SETTINGS_KEY = "autoMemoryDirectory"


# ─── paths and values ─────────────────────────────────────────────────────

def settings_local_path(project_dir: Path) -> Path:
    return Path(project_dir) / ".claude" / "settings.local.json"


def memory_dir_value(ws: str) -> str:
    """The value to store: `~/…` when the vault lives under $HOME (portable
    across machines that keep the default vault location), else absolute."""
    d = memory_dir(ws)
    home = Path.home()
    for a, b in ((d, home), (d.resolve(), home.resolve())):
        try:
            return "~/" + a.relative_to(b).as_posix()
        except ValueError:
            continue
    return str(d)


def _env(env) -> dict:
    return env if env is not None else dict(os.environ)


def _claude_dir(env=None) -> Path:
    e = _env(env)
    v = e.get("CLAUDE_CONFIG_DIR")
    if v:
        return Path(v).expanduser()
    home = e.get("HOME") or str(Path.home())
    return Path(home) / ".claude"


def _expand(value: str, env=None) -> Path:
    e = _env(env)
    if value == "~" or value.startswith("~/"):
        home = e.get("HOME") or str(Path.home())
        return Path(home + value[1:])
    return Path(value)


def _same_dir(a: Path, b: Path) -> bool:
    if a == b:
        return True
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def host_auto_memory_disabled(env=None) -> bool:
    """True when the host will not load auto memory at all (env kill switch or
    `autoMemoryEnabled: false` in the Claude config dir's settings.json)."""
    e = _env(env)
    if str(e.get("CLAUDE_CODE_DISABLE_AUTO_MEMORY", "")).strip() == "1":
        return True
    p = _claude_dir(e) / "settings.json"
    try:
        data = json.loads(p.read_text())
    except Exception:
        return False
    v = data.get("autoMemoryEnabled") if isinstance(data, dict) else None
    return v is False or (isinstance(v, str) and v.strip().lower() == "false")


def configured_dir(project_dir: Path, env=None) -> "str | None":
    """The autoMemoryDirectory value a project declares (local first), or None."""
    for name in ("settings.local.json", "settings.json"):
        p = Path(project_dir) / ".claude" / name
        try:
            data = json.loads(p.read_text())
        except Exception:
            continue
        v = data.get(SETTINGS_KEY) if isinstance(data, dict) else None
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def is_wired(project_dir: Path, ws: str, env=None) -> bool:
    e = _env(env)
    if host_auto_memory_disabled(e):
        return False
    v = configured_dir(project_dir, e)
    if not v:
        return False
    return _same_dir(_expand(v, e), memory_dir(ws))


# ─── wiring ──────────────────────────────────────────────────────────────

def _is_tracked(project_dir: Path, rel: str = ".claude/settings.local.json") -> bool:
    try:
        r = subprocess.run(["git", "-C", str(project_dir), "ls-files", "--error-unmatch", rel],
                           capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def wire(project_dir: Path, ws: str, *, force: bool = False, dry_run: bool = False) -> str:
    """Merge autoMemoryDirectory into <project>/.claude/settings.local.json.
    Returns wired | already | conflict | tracked | invalid (dry_run: would-wire
    instead of wired)."""
    p = settings_local_path(project_dir)
    want = memory_dir_value(ws)
    data: dict = {}
    if p.is_file():
        if _is_tracked(project_dir):
            return "tracked"
        try:
            data = json.loads(p.read_text())
        except Exception:
            return "invalid"
        if not isinstance(data, dict):
            return "invalid"
    cur = data.get(SETTINGS_KEY)
    if isinstance(cur, str) and cur.strip():
        if cur.strip() == want or _same_dir(_expand(cur.strip()), memory_dir(ws)):
            return "already"
        if not force:
            return "conflict"
    if dry_run:
        return "would-wire"
    data[SETTINGS_KEY] = want
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(p, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return "wired"


def _glob_base(pattern: str) -> "Path | None":
    base = pattern
    for suf in ("/**", "/*"):
        if base.endswith(suf):
            base = base[: -len(suf)]
    base = base.rstrip("/")
    if not base or any(ch in base for ch in "*?["):
        return None
    return Path(base).expanduser()


def projects_for_workspaces(config: dict, cwd: "Path | None" = None) -> list:
    """[(project_dir, ws)] for every workspace_map glob whose base directory
    exists on this machine, plus the cwd's own mapping when given."""
    rows: list = []
    seen: set = set()
    for pattern, ws in ((config or {}).get("workspace_map") or {}).items():
        d = _glob_base(str(pattern))
        if d is None or not d.is_dir():
            continue
        key = str(d.resolve())
        if key in seen:
            continue
        seen.add(key)
        rows.append((d.resolve(), str(ws)))
    if cwd is not None:
        c = Path(cwd).resolve()
        if str(c) not in seen:
            rows.append((c, active_workspace(c)))
    return rows


# ─── import of machine-local auto memory ─────────────────────────────────

def project_slug(project_dir: Path) -> str:
    """Claude Code's directory name for a project (every non-alnum char → '-')."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(Path(project_dir).resolve()))


def _sanitized(text: str) -> str:
    try:
        from _privacy import sanitize  # type: ignore
        cleaned, _n = sanitize(text)
        return cleaned if isinstance(cleaned, str) else text
    except Exception:
        return text


def import_native(claude_dir: Path, *, apply: bool = False) -> dict:
    """Copy `<claude_dir>/projects/<slug>/memory/*.md` into the mapped
    workspace's memory/. Dry-run unless apply=True."""
    report = {"imported": [], "renamed": [], "skipped_unmapped": [], "identical": [],
              "index_lines_added": 0, "workspaces": []}
    rows = projects_for_workspaces(read_config())
    slug_to_ws = {project_slug(p): ws for p, ws in rows}
    host = socket.gethostname().split(".")[0] or "host"
    projects = Path(claude_dir) / "projects"
    if not projects.is_dir():
        return report
    for mem in sorted(projects.glob("*/memory")):
        if not mem.is_dir():
            continue
        slug = mem.parent.name
        ws = slug_to_ws.get(slug)
        if ws is None:
            report["skipped_unmapped"].append(slug)
            continue
        if ws not in report["workspaces"]:
            report["workspaces"].append(ws)
        dest = memory_dir(ws)
        for f in sorted(mem.glob("*.md")):
            if f.name == "MEMORY.md":
                continue
            try:
                incoming = _sanitized(f.read_text(errors="ignore"))
            except OSError:
                continue
            target = dest / f.name
            if target.exists():
                if target.read_text(errors="ignore") == incoming:
                    report["identical"].append(f.name)
                    continue
                target = dest / f"{f.stem}.from-{host}{f.suffix}"
                if target.exists() and target.read_text(errors="ignore") == incoming:
                    report["identical"].append(target.name)
                    continue
                report["renamed"].append(target.name)
            else:
                report["imported"].append(f.name)
            if apply:
                dest.mkdir(parents=True, exist_ok=True)
                safe_write(target, incoming)
        src_index = mem / "MEMORY.md"
        if src_index.is_file():
            try:
                lines = [ln for ln in src_index.read_text(errors="ignore").splitlines() if ln.strip()]
            except OSError:
                lines = []
            target = memfile_path(ws)
            existing = target.read_text(errors="ignore") if target.is_file() else ""
            block, free = split(existing)
            have = set(free.splitlines())
            new = [ln for ln in lines if ln not in have and not ln.startswith("<!-- gowth-mem")]
            report["index_lines_added"] += len(new)
            if apply and new:
                dest.mkdir(parents=True, exist_ok=True)
                free_part = (free.rstrip("\n") + "\n") if free.strip() else ""
                safe_write(target, block + free_part + "\n".join(_sanitized(ln) for ln in new) + "\n")
    return report


# ─── status ──────────────────────────────────────────────────────────────

def status(cwd: "Path | None" = None, env=None) -> dict:
    e = _env(env)
    c = Path(cwd or Path.cwd())
    ws = active_workspace(c)
    mf = memfile_path(ws)
    lines = 0
    free_lines = 0
    if mf.is_file():
        text = mf.read_text(errors="ignore")
        lines = text.count("\n")
        free_lines = split(text)[1].count("\n")
    projects = []
    for p, w in projects_for_workspaces(read_config(), cwd=c):
        projects.append({"path": str(p), "ws": w, "wired": is_wired(p, w, e),
                         "tracked": _is_tracked(p) if settings_local_path(p).is_file() else False})
    return {
        "workspace": ws,
        "wired": is_wired(c, ws, e),
        "configured": configured_dir(c, e),
        "host_disabled": host_auto_memory_disabled(e),
        "memfile_exists": mf.is_file(),
        "memfile_lines": lines,
        "free_zone_lines": free_lines,
        "free_zone_over_budget": free_lines > 190,
        "projects": projects,
    }


# ─── CLI ─────────────────────────────────────────────────────────────────

def _cli() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="gowth-mem v4.8 native auto-memory wiring")
    sub = ap.add_subparsers(dest="cmd")
    w = sub.add_parser("wire")
    w.add_argument("--dry-run", action="store_true")
    w.add_argument("--force", action="store_true")
    w.add_argument("--project", default="")
    w.add_argument("--ws", default="")
    i = sub.add_parser("import")
    i.add_argument("--apply", action="store_true")
    sub.add_parser("status")
    args = ap.parse_args()
    try:
        if args.cmd == "wire":
            if args.project:
                targets = [(Path(args.project).resolve(), args.ws or active_workspace(Path(args.project)))]
            else:
                targets = projects_for_workspaces(read_config(), cwd=Path.cwd())
            for p, ws in targets:
                res = wire(p, ws, force=args.force, dry_run=args.dry_run)
                print(f"{res:11} {p}  →  ws={ws}  ({memory_dir_value(ws)})")
            if args.dry_run:
                print("dry-run: nothing written. Re-run without --dry-run to wire.")
            else:
                print("wired projects load MEMORY.md from the vault on their NEXT session start.")
            return 0
        if args.cmd == "import":
            rep = import_native(_claude_dir(), apply=args.apply)
            print(json.dumps(rep, indent=2, ensure_ascii=False))
            if not args.apply:
                print("dry-run: nothing written. Re-run with --apply to import.")
            return 0
        print(json.dumps(status(Path.cwd()), indent=2, ensure_ascii=False))
        return 0
    except Exception as exc:
        log_debug("native", f"cli failed: {exc}")
        print(f"native: error: {exc}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(_cli())
