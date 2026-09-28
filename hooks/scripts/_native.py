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
    `<name>.from-<host>-<project>.md` (a numeric suffix when that clashes too;
    the dry-run plans against the pending writes so it reports what apply
    does — review I4); unmapped project slugs are listed, never guessed;
  * the project is what Claude Code treats as the project: the canonical git
    root (main worktree) when the cwd is inside a repo, else the cwd
    (`project_root`, review I5) — settings.local.json lives there; a `**` glob
    on a non-repo base wires the repos found beneath it, and native memory
    slugs map by glob prefix.

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
from _lock import file_lock  # type: ignore
from _memfile import HOST_LINE_LIMIT, HOST_MARGIN, floor_lines, memfile_path, memory_dir, split  # type: ignore

SETTINGS_KEY = "autoMemoryDirectory"
REPO_SCAN_DEPTH = 3
_SKIP_DIRS = {"node_modules", "vendor", "target", "dist", "build", "__pycache__"}


# ─── paths and values ─────────────────────────────────────────────────────

def project_root(path) -> Path:
    """The directory Claude Code treats as the project — where it reads
    `.claude/settings.local.json` from: the canonical git root (the MAIN
    worktree, via `--git-common-dir`) when `path` is inside a repository, else
    the directory itself (review I5; measured on 2.1.283)."""
    p = Path(path).expanduser()
    try:
        p = p.resolve()
    except OSError:
        p = Path(path)
    if not p.is_dir():
        p = p.parent
    for args in (("rev-parse", "--path-format=absolute", "--git-common-dir"),
                 ("rev-parse", "--show-toplevel")):
        try:
            r = subprocess.run(["git", "-C", str(p), *args], capture_output=True, text=True, timeout=10)
        except Exception:
            return p
        out = (r.stdout or "").strip()
        if r.returncode != 0 or not out:
            continue
        q = Path(out)
        if args[-1] == "--git-common-dir":
            if q.name == ".git" and q.parent.is_dir():
                return q.parent.resolve()
            continue                      # bare or unusual layout → toplevel
        return q.resolve()
    return p


def settings_local_path(project_dir: Path) -> Path:
    return project_root(project_dir) / ".claude" / "settings.local.json"


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
    """The autoMemoryDirectory value a project declares (local first), or None.
    `project_dir` may be any directory inside the project (review I5)."""
    root = project_root(project_dir)
    for name in ("settings.local.json", "settings.json"):
        p = root / ".claude" / name
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
    root = project_root(project_dir)
    p = settings_local_path(root)
    want = memory_dir_value(ws)
    data: dict = {}
    if p.is_file():
        if _is_tracked(root):
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


def _glob_entries(config: dict) -> list:
    """[(base, ws, recursive)] for every workspace_map pattern with a literal base."""
    out: list = []
    for pattern, ws in ((config or {}).get("workspace_map") or {}).items():
        pat = str(pattern)
        d = _glob_base(pat)
        if d is None:
            continue
        out.append((d, str(ws), pat.rstrip("/").endswith("**")))
    return out


def _is_repo(d: Path) -> bool:
    try:
        return (d / ".git").exists()
    except OSError:
        return False


def _dirs_under(base: Path, depth: int = REPO_SCAN_DEPTH) -> list:
    """Directories under `base`, at most `depth` levels down, hidden and build
    dirs skipped. Bounded walk (≤ 5,000 scandir calls)."""
    found: list = []
    frontier = [(base, 0)]
    visited = 0
    while frontier and visited < 5000:
        d, lvl = frontier.pop()
        visited += 1
        try:
            children = [c for c in os.scandir(d) if c.is_dir(follow_symlinks=False)]
        except OSError:
            continue
        for c in sorted(children, key=lambda e: e.name):
            if c.name.startswith(".") or c.name in _SKIP_DIRS:
                continue
            cp = Path(c.path)
            found.append(cp)
            if lvl + 1 < depth:
                frontier.append((cp, lvl + 1))
    return sorted(found)


def _repos_under(base: Path, depth: int = REPO_SCAN_DEPTH) -> list:
    """Git repositories (main worktrees or `.git` files) under `base`."""
    return [d for d in _dirs_under(base, depth) if _is_repo(d)]


def _known_slugs(claude_dir: "Path | None") -> "set | None":
    """Slugs of the projects Claude Code has been used in on this machine
    (`<claude_dir>/projects/<slug>/` exists), or None when there is no
    projects dir to consult (fresh machine, tests)."""
    if claude_dir is None:
        return None
    d = Path(claude_dir) / "projects"
    if not d.is_dir():
        return None
    try:
        return {c.name for c in os.scandir(d) if c.is_dir()}
    except OSError:
        return None


def projects_for_workspaces(config: dict, cwd: "Path | None" = None,
                            claude_dir: "Path | None" = None) -> list:
    """[(project_dir, ws)] to wire: for every workspace_map glob whose base
    exists on this machine — the base itself when it is a repository or the
    pattern is not recursive, else the directories beneath it that Claude
    Code has been used in (review I5: a `**` glob on a parent directory used
    to wire the parent, which no session ever runs in; the live devops glob
    covers ~140 repositories, third-party clones included, so only the ones
    with a `projects/<slug>` dir in `claude_dir` are wired — every repo when
    no projects dir exists, the base itself when nothing is beneath it) —
    plus the cwd's own project root when given."""
    rows: list = []
    seen: set = set()
    known = _known_slugs(claude_dir)

    def _add(d: Path, ws: str) -> None:
        key = str(d.resolve())
        if key in seen:
            return
        seen.add(key)
        rows.append((d.resolve(), ws))

    for base, ws, recursive in _glob_entries(config):
        if not base.is_dir():
            continue
        targets = [base]
        if recursive and not _is_repo(base):
            dirs = _dirs_under(base)
            if known is not None:
                # the project dirs Claude Code has been used in (repo or not);
                # a bare leaf directory with nothing known and no repo beneath
                # is itself the project
                targets = [d for d in [base] + dirs if project_slug(d) in known]
                if not targets and not any(_is_repo(d) for d in dirs):
                    targets = [base]
            else:
                targets = [d for d in dirs if _is_repo(d)] or [base]
        for t in targets:
            _add(t, ws)
    if cwd is not None:
        c = project_root(cwd)
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


def _ws_for_slug(slug: str, config: dict) -> "str | None":
    """Workspace for a native project slug: exact match on a glob base, or the
    LONGEST base whose recursive (`**`) glob covers it — Claude Code's slug is
    the path with every non-alnum char as '-', so `<base-slug>-…` is 'under'
    the base (review I5)."""
    best = None
    for base, ws, recursive in _glob_entries(config):
        pre = project_slug(base)
        if slug == pre or (recursive and slug.startswith(pre + "-")):
            if best is None or len(pre) > len(best[0]):
                best = (pre, ws)
    return best[1] if best else None


def _project_label(slug: str) -> str:
    label = re.sub(r"[^A-Za-z0-9_-]", "-", slug.rstrip("-").rsplit("-", 1)[-1])[:32]
    return label or "project"


def import_native(claude_dir: Path, *, apply: bool = False) -> dict:
    """Copy `<claude_dir>/projects/<slug>/memory/*.md` into the mapped
    workspace's memory/. Dry-run unless apply=True; the dry-run plans against
    the pending writes, so its report is exactly what apply does (review I4)."""
    report = {"imported": [], "renamed": [], "skipped_unmapped": [], "identical": [],
              "index_lines_added": 0, "workspaces": []}
    config = read_config()
    host = socket.gethostname().split(".")[0] or "host"
    projects = Path(claude_dir) / "projects"
    if not projects.is_dir():
        return report
    pending: dict = {}                      # target path → content planned so far

    def _current(path: Path) -> "str | None":
        if path in pending:
            return pending[path]
        try:
            return path.read_text(errors="ignore") if path.exists() else None
        except OSError:
            return None

    def _plan(path: Path, content: str) -> None:
        pending[path] = content
        if apply:
            path.parent.mkdir(parents=True, exist_ok=True)
            safe_write(path, content)

    for mem in sorted(projects.glob("*/memory")):
        if not mem.is_dir():
            continue
        slug = mem.parent.name
        ws = _ws_for_slug(slug, config)
        if ws is None:
            report["skipped_unmapped"].append(slug)
            continue
        if ws not in report["workspaces"]:
            report["workspaces"].append(ws)
        dest = memory_dir(ws)
        label = _project_label(slug)
        for f in sorted(mem.glob("*.md")):
            if f.name == "MEMORY.md":
                continue
            try:
                incoming = _sanitized(f.read_text(errors="ignore"))
            except OSError:
                continue
            target = dest / f.name
            cur = _current(target)
            if cur is None:
                report["imported"].append(f.name)
                _plan(target, incoming)
                continue
            if cur == incoming:
                report["identical"].append(f.name)
                continue
            names = [f"{f.stem}.from-{host}-{label}{f.suffix}"]
            names += [f"{f.stem}.from-{host}-{label}-{n}{f.suffix}" for n in range(2, 51)]
            for name in names:
                cand = dest / name
                cur2 = _current(cand)
                if cur2 is None:
                    report["renamed"].append(name)
                    _plan(cand, incoming)
                    break
                if cur2 == incoming:
                    report["identical"].append(name)
                    break
        src_index = mem / "MEMORY.md"
        if src_index.is_file():
            try:
                lines = [ln for ln in src_index.read_text(errors="ignore").splitlines() if ln.strip()]
            except OSError:
                lines = []
            target = memfile_path(ws)

            def _merge_index() -> None:
                existing = _current(target) or ""
                block, free = split(existing)
                have = set(free.splitlines())
                new = [ln for ln in lines if ln not in have and not ln.startswith("<!-- gowth-mem")]
                report["index_lines_added"] += len(new)
                if new:
                    free_part = (free.rstrip("\n") + "\n") if free.strip() else ""
                    _plan(target, block + free_part + "\n".join(_sanitized(ln) for ln in new) + "\n")

            if apply:
                with file_lock(f"memfile-{ws}", timeout=10.0):   # _memfile.write holds it too
                    _merge_index()
            else:
                _merge_index()
    return report


# ─── status ──────────────────────────────────────────────────────────────

def status(cwd: "Path | None" = None, env=None) -> dict:
    e = _env(env)
    c = Path(cwd or Path.cwd())
    root = project_root(c)
    ws = active_workspace(c)
    mf = memfile_path(ws)
    lines = 0
    free_lines = 0
    if mf.is_file():
        text = mf.read_text(errors="ignore")
        lines = text.count("\n")
        free_lines = split(text)[1].count("\n")
    projects = []
    for p, w in projects_for_workspaces(read_config(), cwd=c, claude_dir=_claude_dir(e)):
        projects.append({"path": str(p), "ws": w, "wired": is_wired(p, w, e),
                         "tracked": _is_tracked(p) if settings_local_path(p).is_file() else False})
    try:
        unmapped = import_native(_claude_dir(e), apply=False)["skipped_unmapped"]
    except Exception:
        unmapped = []
    return {
        "workspace": ws,
        "project_root": str(root),
        "wired": is_wired(root, ws, e),
        "configured": configured_dir(root, e),
        "host_disabled": host_auto_memory_disabled(e),
        "memfile_exists": mf.is_file(),
        "memfile_lines": lines,
        "free_zone_lines": free_lines,
        # spec §4.1: the block cannot shrink below its floor, so the free zone is
        # over budget once it leaves less than the floor under the host's limit
        "free_zone_over_budget": free_lines > HOST_LINE_LIMIT - HOST_MARGIN - floor_lines(),
        "projects": projects,
        "unmapped": unmapped,
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
                root = project_root(Path(args.project))
                targets = [(root, args.ws or active_workspace(root))]
            else:
                targets = projects_for_workspaces(read_config(), cwd=Path.cwd(), claude_dir=_claude_dir())
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
