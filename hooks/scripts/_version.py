#!/usr/bin/env python3
"""v4.7.2 — plugin version self-report + upgrade-drift detection.

WHY THIS EXISTS
---------------
Claude Code installs a plugin into `~/.claude/plugins/cache/<market>/<plugin>/
<version>/` and records the chosen dir in `installed_plugins.json`. Upgrading is
per-machine and not guaranteed: `claude plugin marketplace update` refreshes the
catalog but does NOT move `installPath`/`version`, autoUpdate is off by default
for non-Anthropic marketplaces, and issue #52218 can leave the registry pinned
at an old cache dir. Old version dirs are never pruned, so the stale plugin
keeps running happily — nothing breaks loudly.

A live machine sat on v3.9.0 for months after v4.7.1 shipped. Its every-10-turn
Stop block was the only evidence, and only because the reason string happened to
embed an absolute path containing "3.9.0". Meanwhile that machine kept writing
into the SHARED vault without v4.0 auto-tagging, without v4.1 `fix_aspect` on new
aspects (→ aspects invisible to wikilinks/recall/MOC), without the v4.3 index
repair and without the `english_only` gate. Version drift across machines is a
data-quality problem, not just a UI annoyance.

So: report the running version in the SessionStart header (drift becomes visible
in one glance, on every machine, every session) and compare it against the local
marketplace clone (a plain file read — no network on the startup path).

Pure stdlib. Every entry point is exception-proof: an unknown version must never
break a hook, and must never claim a drift it cannot prove.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

PLUGIN_MARKET = "gowth-mem"
PLUGIN_NAME = "gowth-mem"

# Only dotted-numeric versions are comparable. Registries in the wild also carry
# git shas as "version" (e.g. frontend-design pins "0120fb83da5d") — comparing
# those numerically would invent a drift that does not exist.
_SEMVERISH = re.compile(r"^\d+(?:\.\d+)*$")


def plugin_root() -> Path:
    """Root of the plugin tree ACTUALLY EXECUTING (…/hooks/scripts/_version.py).

    Deliberately derived from `__file__`, not from `$CLAUDE_PLUGIN_ROOT`: the
    whole point is to report which copy of the code is running, and the env var
    is supplied by the same registry entry we are trying to audit.
    """
    return Path(__file__).parent.parent.parent


def _claude_dir(claude_dir: Path | None = None) -> Path:
    if claude_dir is not None:
        return Path(claude_dir)
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(override) if override else Path.home() / ".claude"


def read_plugin_version(root: Path) -> str | None:
    """`<root>/.claude-plugin/plugin.json` → version, or None."""
    try:
        data = json.loads((Path(root) / ".claude-plugin" / "plugin.json")
                          .read_text(encoding="utf-8"))
        ver = data.get("version")
        return ver.strip() if isinstance(ver, str) and ver.strip() else None
    except Exception:
        return None


def running_version() -> str | None:
    """Version of the plugin tree this process was launched from."""
    return read_plugin_version(plugin_root())


def marketplace_root(market: str = PLUGIN_MARKET,
                     claude_dir: Path | None = None) -> Path:
    return _claude_dir(claude_dir) / "plugins" / "marketplaces" / market


def marketplace_version(market: str = PLUGIN_MARKET,
                        claude_dir: Path | None = None) -> str | None:
    """Version available in this machine's marketplace clone (local read).

    None when the clone is absent — a machine that installed by other means gets
    no nudge rather than a wrong one.
    """
    return read_plugin_version(marketplace_root(market, claude_dir))


def parse_version(v: str | None) -> tuple[int, ...] | None:
    if not isinstance(v, str):
        return None
    v = v.strip()
    if not _SEMVERISH.match(v):
        return None
    try:
        return tuple(int(p) for p in v.split("."))
    except Exception:
        return None


def is_outdated(running: str | None, available: str | None) -> bool:
    """True only when both versions are comparable AND running < available.

    Numeric, not lexical: "3.10.0" < "3.9.0" as strings.
    """
    a, b = parse_version(running), parse_version(available)
    if a is None or b is None:
        return False
    # Pad so 4.7 vs 4.7.1 compares sanely.
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) < b + (0,) * (n - len(b))


# ── The HOST's version (Claude Code itself), for hook-protocol feature gates ──
#
# Claude Code exports `AI_AGENT` to every subprocess it spawns — hooks
# included — since 2.1.120 (CHANGELOG: "so `gh` can attribute traffic"):
# `claude-code_2-1-283_harness` (`_agent` for tool subprocesses; older builds
# used `claude-code/<ver>`). It only rewrites a value that is unset or already
# claude-code-prefixed, so a user-chosen AI_AGENT survives and reads as
# "unknown" here — callers must treat None as "assume an old host".
_AI_AGENT_RE = re.compile(r"^claude-code[_/](\d+)[-.](\d+)[-.](\d+)(?:[_/]|$)")

# CHANGELOG 2.1.163: "Stop and SubagentStop hooks can now return
# `hookSpecificOutput.additionalContext` to give Claude feedback and keep the
# turn going without being labeled a hook error".
STOP_CONTEXT_MIN_CC = (2, 1, 163)


def claude_code_version(env=None) -> tuple[int, int, int] | None:
    """Running Claude Code version from `AI_AGENT`, or None. Never raises."""
    try:
        raw = (os.environ if env is None else env).get("AI_AGENT")
        if not isinstance(raw, str):
            return None
        m = _AI_AGENT_RE.match(raw.strip())
        return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None
    except Exception:
        return None


def supports_stop_context(env=None) -> bool:
    """True when the host accepts Stop `hookSpecificOutput.additionalContext`.

    Unknown host → False: `decision: block` is the one Stop shape every Claude
    Code version acts on, and a silently dropped directive is worse than a
    mislabeled one.
    """
    ver = claude_code_version(env)
    return ver is not None and ver >= STOP_CONTEXT_MIN_CC


def version_tag(running: str | None = "__auto__") -> str:
    """`" v4.7.1"` for the bootstrap header; `""` when unknown.

    Changes only on release, so it costs one prompt-cache miss per upgrade —
    exactly when the cached prefix is stale anyway.
    """
    ver = running_version() if running == "__auto__" else running
    ver = ver.strip() if isinstance(ver, str) else ""
    return f" v{ver}" if ver else ""


def drift_nudge(running: str | None = "__auto__",
                available: str | None = "__auto__",
                claude_dir: Path | None = None) -> str:
    """One block telling the user this machine is running stale code, or "".

    Never raises. Silent unless the drift is provable from two readable
    manifests.
    """
    try:
        run = running_version() if running == "__auto__" else running
        avail = (marketplace_version(claude_dir=claude_dir)
                 if available == "__auto__" else available)
        if not is_outdated(run, avail):
            return ""
        return (
            "\n=== gowth-mem upgrade pending on THIS machine ===\n"
            f"Running v{run}, but v{avail} is installed in this machine's marketplace clone.\n"
            "Claude Code left installed_plugins.json pinned to the old cache dir, so every\n"
            f"gowth-mem hook is still executing v{run} code against the shared vault "
            "(older\nversions write entries that newer recall/MOC/gate paths cannot see).\n"
            "Fix here:  claude plugin update gowth-mem -y   → then restart Claude Code.\n"
            "If it still reports the old version, run /mem-doctor.\n"
            "Tell the user this in ONE line, then continue — do not turn it into a project.\n"
        )
    except Exception:
        return ""


if __name__ == "__main__":
    run = running_version()
    avail = marketplace_version()
    print(f"running:     {run or 'unknown'}  ({plugin_root()})")
    print(f"marketplace: {avail or 'unknown'}  ({marketplace_root()})")
    nudge = drift_nudge()
    print(nudge if nudge else "status: up to date")
