"""Conflict packager: write a structured SYNC-CONFLICT.md and reset working
tree to the local side so files stay parseable (no <<<<<<< markers in topics).

Called by auto-sync.py / _sync.py when `git pull --rebase` reports CONFLICT.
The conflict is then resolved by the user via the /mem-sync-resolve skill,
which reads SYNC-CONFLICT.md and applies the chosen version through atomic_write.
"""
from __future__ import annotations

import re
import socket
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import atomic_write, safe_write  # type: ignore
from _debug import log_debug  # type: ignore
from _home import conflict_md, gowth_home  # type: ignore

# v4.8: <ws>/memory/MEMORY.md merges itself (union of both free zones, block
# regenerated) instead of going through the AI-mediated SYNC-CONFLICT.md.
_MEMFILE_RE = re.compile(r"^workspaces/([^/]+)/memory/MEMORY\.md$")


def _git(cwd: Path, *args: str, check: bool = False) -> tuple[int, str, str]:
    # errors="replace": some conflicted blobs (binaries, mixed-encoding text)
    # are not valid UTF-8; lossy decode is acceptable for conflict packaging.
    r = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        errors="replace",
    )
    if check and r.returncode != 0:
        raise subprocess.CalledProcessError(r.returncode, r.args, r.stdout, r.stderr)
    return r.returncode, r.stdout, r.stderr


def _show(cwd: Path, ref: str, path: str) -> str:
    rc, out, _ = _git(cwd, "show", f"{ref}:{path}")
    return out if rc == 0 else "(file missing on this side)"


def merge_memfile(gh: Path, rel: str) -> bool:
    """Resolve a rebase conflict on `workspaces/<ws>/memory/MEMORY.md`.

    Both sides' free zones (Claude's own auto-memory index below the end
    marker) are unioned — local lines first, exact-line dedupe — and the
    managed block is regenerated from the vault, so the result never carries
    conflict markers. The file is written and staged. Returns True when the
    conflict is resolved. Call AFTER other conflicted files were reset to the
    local side: the block reads docs/handoff.md from the working tree.
    """
    m = _MEMFILE_RE.match(rel)
    if not m:
        return False
    ws = m.group(1)
    try:
        from _memfile import render, split  # type: ignore
        sides = []
        for stage in (":3", ":2"):           # :3 = local (rebase), :2 = incoming
            text = _show(gh, stage, rel)
            sides.append("" if text.startswith("(file missing") else text)
        lines: list[str] = split(sides[0])[1].splitlines()
        for ln in split(sides[1])[1].splitlines():
            if ln.strip() and ln not in lines:
                lines.append(ln)
        while lines and not lines[-1].strip():
            lines.pop()
        block = render(ws, free_zone_lines=len(lines))
        content = block + ("\n".join(lines) + "\n" if lines else "")
        safe_write(gh / rel, content)
        rc, _, err = _git(gh, "add", "--", rel)
        if rc != 0:
            log_debug("conflict", f"git add failed for {rel}: {err.strip()[:200]}")
            return False
        return True
    except Exception as exc:
        log_debug("conflict", f"merge_memfile failed for {rel}: {exc}")
        return False


def package_conflict() -> "Path | None":
    """Inspect current rebase state and write SYNC-CONFLICT.md.

    Resets tracked conflicted files to the local side (`--ours` from the
    rebase's perspective is the incoming/remote; `--theirs` is the local
    branch — we use --theirs to keep the user's local copy).

    v4.8: conflicts on `<ws>/memory/MEMORY.md` are merged by `merge_memfile`.
    When nothing else conflicted, the rebase is continued and None is
    returned — the caller proceeds to push instead of stopping on a
    SYNC-CONFLICT.md that no user needs to read."""
    gh = gowth_home()
    rc, out, _ = _git(gh, "diff", "--name-only", "--diff-filter=U")
    conflict_files = [f for f in out.splitlines() if f.strip()]
    if not conflict_files:
        return conflict_md()

    memfiles = [f for f in conflict_files if _MEMFILE_RE.match(f)]
    others = [f for f in conflict_files if f not in memfiles]

    # Capture the three sides of the OTHER files before their index entries
    # are resolved below (git add drops stages 1-3).
    sections: list[str] = []
    for f in others:
        sections.append(_describe(gh, f))
    for f in others:
        _git(gh, "checkout", "--theirs", "--", f)   # local branch's version
        _git(gh, "add", "--", f)

    unmerged = [f for f in memfiles if not merge_memfile(gh, f)]
    for f in unmerged:
        sections.append(_describe(gh, f))
        _git(gh, "checkout", "--theirs", "--", f)
        _git(gh, "add", "--", f)

    remaining = others + unmerged
    if not remaining:
        rc, _, err = _git(gh, "-c", "core.editor=true", "rebase", "--continue")
        if rc == 0:
            return None
        log_debug("conflict", f"rebase --continue failed after memfile merge: {err.strip()[:200]}")
        sections.append("### (rebase --continue failed after merging MEMORY.md)\n")
        sections.append(err.strip()[:500] + "\n")

    host = socket.gethostname()
    parts: list[str] = [
        "# SYNC CONFLICT\n",
        f"Pull from origin hit conflicts on {len(remaining)} file(s).",
        f"Host: {host}",
        "",
        "## Conflicting files\n",
    ]
    parts.extend(sections)

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
    return out_path


def _describe(gh: Path, f: str) -> str:
    """The three-sided description of one conflicted file for SYNC-CONFLICT.md.
    Must run while the file's index stages (:1 :2 :3) still exist."""
    local_text = _show(gh, ":3", f)   # :3 = --theirs (local during rebase)
    remote_text = _show(gh, ":2", f)  # :2 = --ours (incoming during rebase)
    ancestor_text = _show(gh, ":1", f)
    parts = [f"### {f}\n",
             "**Local (this machine)**:\n```", local_text.rstrip(), "```\n",
             "**Remote (incoming)**:\n```", remote_text.rstrip(), "```\n"]
    if ancestor_text and ancestor_text != "(file missing on this side)":
        parts += ["**Common ancestor**:\n```", ancestor_text.rstrip(), "```\n"]
    parts.append("**Choose**: keep-local | keep-remote | merge | manual\n")
    return "\n".join(parts)


if __name__ == "__main__":
    p = package_conflict()
    print(f"wrote {p}")
