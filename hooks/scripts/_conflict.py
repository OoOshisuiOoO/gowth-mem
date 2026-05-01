"""Conflict packager: write a structured SYNC-CONFLICT.md and reset working
tree to the local side so files stay parseable (no <<<<<<< markers in topics).

Called by auto-sync.py / _sync.py when `git pull --rebase` reports CONFLICT.
The conflict is then resolved by the user via the /mem-sync-resolve skill,
which reads SYNC-CONFLICT.md and applies the chosen version through atomic_write.
"""
from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import atomic_write  # type: ignore
from _home import conflict_md, gowth_home  # type: ignore


def _git(cwd: Path, *args: str, check: bool = False) -> tuple[int, str, str]:
    r = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
    )
    if check and r.returncode != 0:
        raise subprocess.CalledProcessError(r.returncode, r.args, r.stdout, r.stderr)
    return r.returncode, r.stdout, r.stderr


def _show(cwd: Path, ref: str, path: str) -> str:
    rc, out, _ = _git(cwd, "show", f"{ref}:{path}")
    return out if rc == 0 else "(file missing on this side)"


def package_conflict() -> Path:
    """Inspect current rebase state and write SYNC-CONFLICT.md.

    Resets tracked conflicted files to the local side (`--ours` from the
    rebase's perspective is the incoming/remote; `--theirs` is the local
    branch — we use --theirs to keep the user's local copy)."""
    gh = gowth_home()
    rc, out, _ = _git(gh, "diff", "--name-only", "--diff-filter=U")
    conflict_files = [f for f in out.splitlines() if f.strip()]
    if not conflict_files:
        return conflict_md()

    host = socket.gethostname()
    parts: list[str] = [
        "# SYNC CONFLICT\n",
        f"Pull from origin hit conflicts on {len(conflict_files)} file(s).",
        f"Host: {host}",
        "",
        "## Conflicting files\n",
    ]

    for f in conflict_files:
        local_text = _show(gh, ":3", f)   # :3 = --theirs (local during rebase)
        remote_text = _show(gh, ":2", f)  # :2 = --ours (incoming during rebase)
        ancestor_text = _show(gh, ":1", f)
        parts.append(f"### {f}\n")
        parts.append("**Local (this machine)**:\n```")
        parts.append(local_text.rstrip())
        parts.append("```\n")
        parts.append("**Remote (incoming)**:\n```")
        parts.append(remote_text.rstrip())
        parts.append("```\n")
        if ancestor_text and ancestor_text != "(file missing on this side)":
            parts.append("**Common ancestor**:\n```")
            parts.append(ancestor_text.rstrip())
            parts.append("```\n")
        parts.append(
            "**Choose**: keep-local | keep-remote | merge | manual\n"
        )

    parts.append(
        "\n## How to resolve\n\n"
        "Run `/mem-sync-resolve` in Claude Code. The skill will walk each file,\n"
        "ask you which version to keep (or merge), apply via atomic_write,\n"
        "then `git rebase --continue` and push.\n\n"
        "To abort the rebase entirely: `git -C ~/.gowth-mem rebase --abort`.\n"
    )

    body = "\n".join(parts) + "\n"
    out_path = conflict_md()
    atomic_write(out_path, body)

    # Reset working copies to local side so files remain valid markdown
    # (no raw <<<<<<< markers leak into topics/*.md).
    for f in conflict_files:
        # `git checkout --theirs <f>` during rebase = local branch's version
        _git(gh, "checkout", "--theirs", "--", f)
        # Stage so rebase --continue won't trip on dirty index when user
        # later runs /mem-sync-resolve.
        _git(gh, "add", "--", f)

    return out_path


if __name__ == "__main__":
    p = package_conflict()
    print(f"wrote {p}")
