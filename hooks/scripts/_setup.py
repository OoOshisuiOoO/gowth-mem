#!/usr/bin/env python3
"""Claude setup portability (v4.1) — backup the machine's Claude Code setup
into the synced vault so a new machine restores in one pass.

What gets captured into `~/.gowth-mem/shared/setup/` (git-synced):
  manifest.json      machine, timestamp, counts
  plugins.json       marketplaces (name → git URL | "builtin") + installed plugins
  mcp.global.json    global MCP servers with env VALUES redacted to <env:NAME>
  settings.json      ~/.claude/settings.json (sanitized)
  CLAUDE.global.md   ~/.claude/CLAUDE.md (sanitized)
  keybindings.json   ~/.claude/keybindings.json (if present)
  skills/            ~/.claude/skills/ tree (text files sanitized)
  RESTORE.md         human/AI-readable restore steps + one-paste /plugin block
  restore.sh         file copies + MCP merge into ~/.claude.json

Secret safety: every text written passes _privacy.sanitize — the vault must
NEVER hold a real secret value (canon: pointers only). MCP env values are
structurally replaced by `<env:NAME>` pointers and listed in `required_env`.

New machine flow:
  1. /mem-install (or clone the vault repo)
  2. bash ~/.gowth-mem/shared/setup/restore.sh
  3. paste the /plugin block RESTORE.md prints into Claude Code, restart

CLI:
  python3 _setup.py --backup [--dry-run]
  python3 _setup.py --status
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import atomic_write  # type: ignore
from _home import gowth_home, shared_dir  # type: ignore
from _privacy import sanitize  # type: ignore

TEXT_SUFFIXES = {".md", ".txt", ".json", ".sh", ".py", ".yaml", ".yml", ".toml"}
MAX_SKILL_FILE_BYTES = 512 * 1024  # skip pathological blobs inside skills/


def default_claude_dir() -> Path:
    import os
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))


def default_claude_json() -> Path:
    return Path.home() / ".claude.json"


def setup_dir() -> Path:
    return shared_dir() / "setup"


# ---------------------------------------------------------------- collectors

def collect_marketplaces(claude_dir: Path) -> dict:
    """name → git origin URL, or 'builtin' when the clone has no remote."""
    out: dict[str, str] = {}
    root = claude_dir / "plugins" / "marketplaces"
    if not root.is_dir():
        return out
    for m in sorted(p for p in root.iterdir() if p.is_dir()):
        try:
            url = subprocess.run(
                ["git", "-C", str(m), "config", "--get", "remote.origin.url"],
                capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            url = ""
        out[m.name] = url or "builtin"
    return out


def collect_plugins(claude_dir: Path) -> dict:
    """fullname → {version, scope} from installed_plugins.json (v2 format)."""
    reg = claude_dir / "plugins" / "installed_plugins.json"
    out: dict[str, dict] = {}
    try:
        data = json.loads(reg.read_text())
    except Exception:
        return out
    for name, entries in (data.get("plugins") or {}).items():
        if isinstance(entries, list) and entries:
            e = entries[0]
            out[name] = {"version": e.get("version", ""), "scope": e.get("scope", "user")}
    return out


def collect_mcp(claude_json: Path) -> tuple[dict, list[str]]:
    """Global mcpServers with env values redacted to `<env:NAME>` pointers.
    Returns (servers, required_env_names)."""
    try:
        data = json.loads(claude_json.read_text())
    except Exception:
        return {}, []
    servers = {}
    required: list[str] = []
    for name, cfg in (data.get("mcpServers") or {}).items():
        cfg = dict(cfg)
        env = cfg.get("env")
        if isinstance(env, dict) and env:
            cfg["env"] = {k: f"<env:{k}>" for k in env}
            required.extend(env.keys())
        servers[name] = cfg
    return servers, sorted(set(required))


# ------------------------------------------------------------------- backup

def _write_text(path: Path, text: str, dry_run: bool) -> int:
    """Sanitize then write. Returns number of redactions applied."""
    clean, n = sanitize(text)
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, clean)
    return n


# VCS/tooling internals that must never enter the vault. Live bug: a skill
# installed as a git CLONE leaked 78 `.git/objects/**` files into the synced
# backup, and their 444 mode broke the next run's overwrite.
SKIP_DIR_PARTS = {".git", ".hg", ".svn", "__pycache__", "node_modules"}


def _copy_skills(src: Path, dst: Path, dry_run: bool) -> tuple[int, int]:
    """Copy the skills tree, sanitizing text files. Returns (files, redactions)."""
    if not src.is_dir():
        return 0, 0
    files = redactions = 0
    for p in sorted(src.rglob("*")):
        if not p.is_file() or p.name == ".DS_Store":
            continue
        rel = p.relative_to(src)
        if SKIP_DIR_PARTS.intersection(rel.parts):
            continue
        if p.stat().st_size > MAX_SKILL_FILE_BYTES:
            continue
        target = dst / rel
        if p.suffix.lower() in TEXT_SUFFIXES:
            try:
                redactions += _write_text(target, p.read_text(errors="ignore"), dry_run)
            except Exception:
                continue
        elif not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                target.unlink()   # older backups may hold read-only (444) copies
            shutil.copy2(p, target)
        files += 1
    return files, redactions


def _restore_md(marketplaces: dict, plugins: dict, required_env: list[str]) -> str:
    mkt_lines = [f"/plugin marketplace add {url}"
                 for name, url in sorted(marketplaces.items()) if url != "builtin"]
    plug_lines = [f"/plugin install {name}" for name in sorted(plugins)]
    env_block = ("\n".join(f"- `{k}` — set in your shell profile / secret store"
                           for k in required_env) or "- (none)")
    return f"""# Restore Claude Code setup

Generated by gowth-mem `_setup.py`. New-machine flow:

## 1. One script (files: skills, settings, CLAUDE.md, MCP)

```bash
bash ~/.gowth-mem/shared/setup/restore.sh
```

## 2. One paste block (plugins) — paste into Claude Code

```text
{chr(10).join(mkt_lines)}
{chr(10).join(plug_lines)}
```

Restart Claude Code so hooks register.

## 3. Required env vars (values were redacted — vault stores pointers only)

{env_block}
"""


RESTORE_SH = """#!/usr/bin/env bash
# Generated by gowth-mem _setup.py — restore Claude Code setup on a new machine.
# Idempotent; existing files are backed up as *.pre-restore before overwrite.
set -uo pipefail
SETUP_DIR="$(cd "$(dirname "$0")" && pwd)"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
mkdir -p "$CLAUDE_DIR"

# 1. personal skills
if [ -d "$SETUP_DIR/skills" ]; then
  mkdir -p "$CLAUDE_DIR/skills"
  cp -R "$SETUP_DIR/skills/." "$CLAUDE_DIR/skills/"
  echo "restored: skills/ → $CLAUDE_DIR/skills/"
fi

# 2. global CLAUDE.md + settings.json + keybindings.json
for pair in "CLAUDE.global.md:CLAUDE.md" "settings.json:settings.json" "keybindings.json:keybindings.json"; do
  src="${pair%%:*}"; dst="${pair##*:}"
  if [ -f "$SETUP_DIR/$src" ]; then
    [ -f "$CLAUDE_DIR/$dst" ] && cp "$CLAUDE_DIR/$dst" "$CLAUDE_DIR/$dst.pre-restore"
    cp "$SETUP_DIR/$src" "$CLAUDE_DIR/$dst"
    echo "restored: $dst"
  fi
done

# 3. global MCP servers → merge into ~/.claude.json (env values stay <env:NAME>
#    pointers — export the real values in your shell; see RESTORE.md §3)
python3 - "$SETUP_DIR/mcp.global.json" "$HOME/.claude.json" <<'PYEOF'
import json, sys, os
src, dst = sys.argv[1], sys.argv[2]
if os.path.exists(src):
    servers = json.load(open(src)).get("mcpServers", {})
    data = json.load(open(dst)) if os.path.exists(dst) else {}
    merged = data.get("mcpServers", {})
    added = [k for k in servers if k not in merged]
    merged.update({k: v for k, v in servers.items() if k not in merged})
    data["mcpServers"] = merged
    json.dump(data, open(dst, "w"), indent=2)
    print(f"restored: MCP servers merged (+{len(added)}: {', '.join(added) or 'none new'})")
PYEOF

echo
echo "NEXT: open Claude Code and paste the /plugin block from:"
echo "  $SETUP_DIR/RESTORE.md"
"""


def backup(claude_dir: Path | None = None, claude_json: Path | None = None,
           dry_run: bool = False) -> dict:
    claude_dir = claude_dir or default_claude_dir()
    claude_json = claude_json or default_claude_json()
    if not claude_dir.is_dir():
        return {"skipped": f"no claude dir at {claude_dir}"}

    out = setup_dir()
    marketplaces = collect_marketplaces(claude_dir)
    plugins = collect_plugins(claude_dir)
    mcp_servers, required_env = collect_mcp(claude_json)

    redactions = 0
    redactions += _write_text(out / "plugins.json", json.dumps(
        {"marketplaces": marketplaces, "plugins": plugins}, indent=2), dry_run)
    redactions += _write_text(out / "mcp.global.json", json.dumps(
        {"mcpServers": mcp_servers, "required_env": required_env}, indent=2), dry_run)

    for src, dst in (("settings.json", "settings.json"),
                     ("CLAUDE.md", "CLAUDE.global.md"),
                     ("keybindings.json", "keybindings.json")):
        p = claude_dir / src
        if p.is_file():
            redactions += _write_text(out / dst, p.read_text(errors="ignore"), dry_run)

    skills_n, skills_red = _copy_skills(claude_dir / "skills", out / "skills", dry_run)
    redactions += skills_red

    _write_text(out / "RESTORE.md", _restore_md(marketplaces, plugins, required_env), dry_run)
    _write_text(out / "restore.sh", RESTORE_SH, dry_run)
    if not dry_run:
        (out / "restore.sh").chmod(0o755)

    manifest = {
        "machine": platform.node(),
        "backed_up_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "marketplaces": len(marketplaces),
        "plugins": len(plugins),
        "mcp_servers": len(mcp_servers),
        "skills": skills_n,
        "redactions": redactions,
    }
    if not dry_run:
        atomic_write(out / "manifest.json", json.dumps(manifest, indent=2))
    return {**manifest, "dry_run": dry_run, "out": str(out)}


def status() -> dict:
    mp = setup_dir() / "manifest.json"
    if not mp.is_file():
        return {"status": "no backup yet — run: python3 _setup.py --backup"}
    return json.loads(mp.read_text())


def main() -> int:
    ap = argparse.ArgumentParser(description="Backup/restore Claude Code setup via the vault.")
    ap.add_argument("--backup", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not gowth_home().is_dir():
        print("no ~/.gowth-mem directory — run /mem-install first")
        return 0
    if args.backup:
        r = backup(dry_run=args.dry_run)
        print(json.dumps(r, indent=2))
    else:
        print(json.dumps(status(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
