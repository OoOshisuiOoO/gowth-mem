"""Conflict packager: write a structured SYNC-CONFLICT.md and reset working
tree to the local side so files stay parseable (no <<<<<<< markers in topics).

Called by auto-sync.py / _sync.py when `git pull --rebase` reports CONFLICT.
The conflict is then resolved by the user via the /mem-sync resolve skill,
which reads SYNC-CONFLICT.md and applies the chosen version through atomic_write.

v4.9 (review safety-sync#4): the resolve flow finishes with
`python3 _conflict.py --continue` — under the sync lock it runs the
commit-boundary privacy pass (refusing while a file that holds a secret
cannot be rewritten), stages the resolution and continues the rebase with
the `gowth-mem@<host>` identity the commit paths use. A bare continue failed
on a machine with no git identity, and the skill then looped on a
SYNC-CONFLICT.md that nothing rewrote. Exit 0 = rebase finished, 2 = a later
replayed commit stopped on a new conflict (SYNC-CONFLICT.md rewritten),
1 = anything else (git's message is printed).
"""
from __future__ import annotations

import argparse
import os
import re
import shlex
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


def _git(cwd: Path, *args: str, check: bool = False, env: "dict | None" = None) -> tuple[int, str, str]:
    # errors="replace": some conflicted blobs (binaries, mixed-encoding text)
    # are not valid UTF-8; lossy decode is acceptable for conflict packaging.
    r = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        errors="replace",
        env=env,
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
        for stage in (":3", ":2", ":1"):     # :3 = local (rebase), :2 = incoming, :1 = base
            text = _show(gh, stage, rel)
            sides.append("" if text.startswith("(file missing") else text)
        safe_write(gh / rel, merge_texts(ws, sides[0], sides[1], sides[2]))
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


def package_conflict(host: "str | None" = None) -> "Path | None":
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
    rebase finishes (None) or a non-MEMORY.md conflict needs the user.

    v4.9 (spec §3.4): `rebase --continue` commits the replayed change, so it
    runs with the identity the commit paths pass (`gowth-mem@<host>`, host =
    config.json host_id or the hostname) — without one (CI, a fresh machine,
    `user.useConfigOnly`) every self-merge ended in SYNC-CONFLICT.md."""
    gh = gowth_home()
    conflict_files = _unmerged(gh)
    if not conflict_files:
        return conflict_md()
    from _git import identity_args  # type: ignore
    ident = identity_args(host)

    sections: list[str] = []
    remaining: list[str] = []
    exhausted = True
    for _ in range(MAX_REPLAYS):
        remaining = _resolve_stop(gh, conflict_files, sections)
        if remaining:
            exhausted = False
            break
        rc, out, err = _git(gh, *ident, "-c", "core.editor=true", "rebase", "--continue")
        if rc != 0 and not _unmerged(gh):
            # Review m3(b): another session's Stop wrote a tracked file (usually a
            # MEMORY.md re-render) while the rebase was stopped; git refuses to
            # continue over unstaged changes. Fold them into the replay — nothing
            # is discarded — and retry once. (No text-based --skip: git >= 2.33
            # drops an emptied replay itself, and a skip on a misread message
            # would hard-reset a real commit.)
            # review R5: the folded files may be memory files Claude just wrote —
            # sanitize first; a file the sanitizer could not write is never folded
            # (v4.9 safety-sync#2: every changed synced markdown file, not only memory/)
            skipped: list = []
            try:
                from _memsan import sanitize_before_commit  # type: ignore
                skipped = list(sanitize_before_commit().get("skipped") or [])
            except Exception as exc:
                log_debug("conflict", f"sanitize before fold failed: {exc}")
            if not skipped:
                _git(gh, "add", "-u")
                rc, out, err = _git(gh, *ident, "-c", "core.editor=true", "rebase", "--continue")
            else:
                err = (err or "") + f"\n(not folded: {', '.join(skipped)} could not be sanitized)"
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
                            + f" — finish with `{continue_command()}` (or /mem-sync resolve).\n")
        remaining = remaining or merged

    machine = socket.gethostname()
    parts: list[str] = [
        "# SYNC CONFLICT\n",
        f"Pull from origin hit conflicts on {len(remaining)} file(s).",
        f"Host: {machine}",
        "",
        "## Conflicting files\n",
    ]
    parts.extend(sections)

    # review safety-sync#4: the finish step carries the gowth-mem identity — a
    # bare continue fails on a machine with no git identity
    parts.append(
        "\n## How to resolve\n\n"
        "Run `/mem-sync resolve` in Claude Code. The skill will walk each file,\n"
        "ask you which version to keep (or merge), apply via atomic_write,\n"
        f"then finish the rebase with `{continue_command()}`\n"
        "(it redacts secrets and continues as gowth-mem@<host>), delete this file and\n"
        "push with `/mem-sync`.\n\n"
        f"To abort the rebase entirely: `git -C {shlex.quote(str(gh))} rebase --abort`, then delete\n"
        "this file — the next sync replays the same commits and can stop here again.\n"
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


# ── `--continue`: finish the rebase after /mem-sync resolve (review safety-sync#4) ──

CONTINUE_LOCK_TIMEOUT = 30.0      # the CLI waits like /mem-sync does


def continue_command() -> str:
    """The shell command that finishes a resolved rebase (this script, absolute
    path — SYNC-CONFLICT.md is read as text, nothing would expand a variable)."""
    return f"python3 {shlex.quote(str(Path(__file__).resolve()))} --continue"


def _rebase_in_progress(gh: Path) -> bool:
    git_dir = gh / ".git"
    return (git_dir / "rebase-merge").is_dir() or (git_dir / "rebase-apply").is_dir()


def continue_rebase(host: "str | None" = None, lock_timeout: float = CONTINUE_LOCK_TIMEOUT) -> int:
    """Finish a stopped rebase once the user's choices are applied.

    Under `file_lock("sync")`: refuses (exit 1) while paths are still
    unmerged (a stop nobody packaged — `add -A` would commit its markers);
    runs the commit-boundary privacy pass and refuses (exit 1) while a file
    that holds a secret cannot be rewritten; `git add -A` (SYNC-CONFLICT.md
    excluded); `git <identity> -c core.editor=true rebase --continue`
    (GIT_EDITOR=true too: it outranks core.editor). A later replayed commit
    that stops is handed to `package_conflict` ONLY when git left unmerged
    paths: MEMORY.md-only stops merge themselves (exit 0), anything else
    rewrites SYNC-CONFLICT.md (exit 2). Any other failure prints git's own
    message (exit 1) — the old flow re-packaged a conflict that no longer
    existed and looped. Nothing is deleted or pushed here: the resolve flow
    removes SYNC-CONFLICT.md after exit 0 and pushes with `_sync.py`.
    Never raises."""
    gh = gowth_home()
    if not (gh / ".git").is_dir():
        print(f"continue: {gh} is not a git repository — nothing to continue", file=sys.stderr)
        return 1
    try:
        from _git import identity_host  # type: ignore
        from _lock import file_lock  # type: ignore
        host = host or identity_host()
        lock = file_lock("sync", timeout=lock_timeout)
        try:
            lock.__enter__()
        except TimeoutError:
            print("continue: another session holds the sync lock — run the same command again shortly",
                  file=sys.stderr)
            return 1
        try:
            return _continue_locked(gh, host)
        finally:
            lock.__exit__(None, None, None)
    except Exception as exc:
        log_debug("conflict", f"--continue failed: {exc!r}")
        print(f"continue: failed ({exc}); `git -C {shlex.quote(str(gh))} status` shows where the rebase "
              f"stands", file=sys.stderr)
        return 1


def _continue_locked(gh: Path, host: str) -> int:
    unmerged = _unmerged(gh)
    if not _rebase_in_progress(gh):
        if unmerged:
            print(f"continue: {len(unmerged)} unmerged path(s) but no rebase in progress "
                  f"(e.g. {unmerged[0]}) — resolve them by hand: git -C {shlex.quote(str(gh))} status",
                  file=sys.stderr)
            return 1
        print(f"continue: no rebase in progress — nothing left to continue. Delete {conflict_md()} "
              f"if it is still there, then push with /mem-sync.")
        return 0
    if unmerged:
        # package_conflict stages every stop it packages, so this stop was never
        # packaged (a pull run by hand?) — `add -A` would commit its markers
        print(f"continue: {len(unmerged)} path(s) are still unmerged (e.g. {unmerged[0]}) — nothing was "
              f"committed. Remove their conflict markers and `git add` them, then run the same command.",
              file=sys.stderr)
        return 1
    try:
        from _memsan import sanitize_before_commit  # type: ignore
        report = sanitize_before_commit()
    except Exception as exc:
        print(f"continue: the privacy pass failed ({exc}) — nothing was committed", file=sys.stderr)
        return 1
    skipped = list(report.get("skipped") or [])
    if skipped:
        print(f"continue: refusing — {len(skipped)} file(s) hold a secret the sanitizer could not "
              f"rewrite (lock held or write failed): {', '.join(skipped[:5])}. Nothing was committed; "
              f"run the same command again in a moment.", file=sys.stderr)
        return 1
    if report.get("sanitized"):
        print(f"continue: redacted {report.get('secrets', 0)} secret(s) in: "
              f"{', '.join(report['sanitized'][:5])}")
    # SYNC-CONFLICT.md is never committed: the default .gitignore ignores it; an
    # older vault's may not, so it is excluded there (an exclude that names an
    # IGNORED file makes `git add` fail — hence the check)
    ignored = _git(gh, "check-ignore", "-q", "--", "SYNC-CONFLICT.md")[0] == 0
    add = ["add", "-A"] if ignored else ["add", "-A", "--", ".", ":(exclude)SYNC-CONFLICT.md"]
    rc, _out, err = _git(gh, *add)
    if rc != 0:
        print(f"continue: git add failed — nothing was committed:\n{err.strip()[:1500]}", file=sys.stderr)
        return 1
    from _git import identity_args  # type: ignore
    rc, out, err = _git(gh, *identity_args(host), "-c", "core.editor=true", "rebase", "--continue",
                        env={**os.environ, "GIT_EDITOR": "true"})
    if rc == 0:
        print(f"continue: rebase finished (replayed as gowth-mem@{host}). Next: delete {conflict_md()}, "
              f"then push with /mem-sync.")
        return 0
    if _unmerged(gh):
        cm = package_conflict(host=host)
        if cm is None:
            print(f"continue: rebase finished (a later memory/MEMORY.md stop merged itself). Next: delete "
                  f"{conflict_md()}, then push with /mem-sync.")
            return 0
        print(f"continue: a later replayed commit hit a NEW conflict — {cm} was rewritten; "
              f"resolve it the same way.", file=sys.stderr)
        return 2
    detail = ((err or "") + (out or "")).strip()
    print(f"continue: the rebase did not continue and nothing new conflicted. git said:\n{detail[:1500]}\n"
          f"(If it names local changes, run the same command again.)", file=sys.stderr)
    return 1


def main(argv: "list | None" = None) -> int:
    ap = argparse.ArgumentParser(description="Package a sync conflict, or finish a resolved rebase.")
    ap.add_argument("--continue", dest="cont", action="store_true",
                    help="finish the rebase after /mem-sync resolve applied the choices")
    args = ap.parse_args(argv)
    if args.cont:
        return continue_rebase()
    p = package_conflict()
    print(f"wrote {p}" if p is not None else "nothing left to package (MEMORY.md merged itself)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
