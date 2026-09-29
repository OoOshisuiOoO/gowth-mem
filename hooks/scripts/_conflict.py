"""Conflict packager: write a structured SYNC-CONFLICT.md and reset working
tree to the local side so files stay parseable (no <<<<<<< markers in topics).

Called by auto-sync.py / _sync.py when `git pull --rebase` reports CONFLICT.
The conflict is then resolved by the user via the /mem-sync resolve skill,
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
        from _memfile import merge_texts  # type: ignore
        sides = []
        for stage in (":3", ":2"):           # :3 = local (rebase), :2 = incoming
            text = _show(gh, stage, rel)
            sides.append("" if text.startswith("(file missing") else text)
        safe_write(gh / rel, merge_texts(ws, sides[0], sides[1]))
        rc, _, err = _git(gh, "add", "--", rel)
        if rc != 0:
            log_debug("conflict", f"git add failed for {rel}: {err.strip()[:200]}")
            return False
        return True
    except Exception as exc:
        log_debug("conflict", f"merge_memfile failed for {rel}: {exc}")
        return False


MAX_REPLAYS = 50   # bound on replayed commits handled in one package_conflict() call


def _unmerged(gh: Path) -> list[str]:
    _rc, out, _ = _git(gh, "diff", "--name-only", "--diff-filter=U")
    return [f for f in out.splitlines() if f.strip()]


def _resolve_stop(gh: Path, conflict_files: list[str], sections: list[str]) -> list[str]:
    """Handle one stopped rebase step: reset OTHER conflicted files to the local
    side (describing them first — git add drops stages 1-3), merge every
    MEMORY.md. Returns the files that still need a human."""
    memfiles = [f for f in conflict_files if _MEMFILE_RE.match(f)]
    others = [f for f in conflict_files if f not in memfiles]
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
    return others + unmerged


def package_conflict() -> "Path | None":
    """Inspect current rebase state and write SYNC-CONFLICT.md.

    Resets tracked conflicted files to the local side (`--ours` from the
    rebase's perspective is the incoming/remote; `--theirs` is the local
    branch — we use --theirs to keep the user's local copy).

    v4.8: conflicts on `<ws>/memory/MEMORY.md` are merged by `merge_memfile`.
    When nothing else conflicted, the rebase is continued and None is
    returned — the caller proceeds to push instead of stopping on a
    SYNC-CONFLICT.md that no user needs to read.

    Review C1: a rebase replays EVERY unpushed local commit (several after an
    offline stretch or a PreCompact --commit-only), and each one can stop on
    MEMORY.md again. Every stop is merged in turn (bounded by MAX_REPLAYS); a
    replayed commit that became empty is skipped; the loop ends when the
    rebase finishes (None) or a non-MEMORY.md conflict needs the user."""
    gh = gowth_home()
    conflict_files = _unmerged(gh)
    if not conflict_files:
        return conflict_md()

    sections: list[str] = []
    remaining: list[str] = []
    exhausted = True
    for _ in range(MAX_REPLAYS):
        remaining = _resolve_stop(gh, conflict_files, sections)
        if remaining:
            exhausted = False
            break
        rc, out, err = _git(gh, "-c", "core.editor=true", "rebase", "--continue")
        if rc != 0 and not _unmerged(gh):
            # Review m3(b): another session's Stop wrote a tracked file (usually a
            # MEMORY.md re-render) while the rebase was stopped; git refuses to
            # continue over unstaged changes. Fold them into the replay — nothing
            # is discarded — and retry once. (No text-based --skip: git >= 2.33
            # drops an emptied replay itself, and a skip on a misread message
            # would hard-reset a real commit.)
            _git(gh, "add", "-u")
            rc, out, err = _git(gh, "-c", "core.editor=true", "rebase", "--continue")
        if rc == 0:
            return None
        conflict_files = _unmerged(gh)
        if conflict_files:
            continue                      # the next replayed commit stopped too
        exhausted = False
        log_debug("conflict", f"rebase --continue failed after memfile merge: {err.strip()[:200]}")
        _rc, status, _ = _git(gh, "status", "--porcelain")
        sections.append("### (rebase --continue failed after merging MEMORY.md)\n")
        sections.append((err + out).strip()[:500] + "\n")
        sections.append("Working tree at that moment (`git status --porcelain`):\n```\n"
                        + status.strip()[:1500] + "\n```\n")
        remaining = [ln[3:] for ln in status.splitlines() if ln[:2].strip()]
        break
    if exhausted:
        # Review m3(c): the bound was hit with a stop still open — resolve it so
        # no marker is left in the tree, then hand over to the user.
        remaining = _resolve_stop(gh, conflict_files, sections)
        merged = [f for f in conflict_files if f not in remaining]
        sections.append(f"### (gave up after {MAX_REPLAYS} replayed commits)\n")
        if merged:
            sections.append("Merged and staged, rebase left stopped: " + ", ".join(merged)
                            + " — run `git -C ~/.gowth-mem rebase --continue` (or /mem-sync resolve).\n")
        remaining = remaining or merged

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
        "Run `/mem-sync resolve` in Claude Code. The skill will walk each file,\n"
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
