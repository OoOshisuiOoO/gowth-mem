#!/usr/bin/env python3
"""Main-context chars per 100 real turns, v4.8 hooks, on a COPY of a real vault.

Same accounting as docs/audits/2026-09-28-audit-flow.md §A3 (1 session start +
2 compactions per 100 turns, default cadences 10/15 → 7 journal-only, 3
review-only, 3 both). Per-prompt recall is measured on REAL prompts: the
`**User:**` lines of the copy's session logs (newest first, up to 100).

    GOWTH_MEM_HOME=<vault-copy> python3 tests/probes/tokens_per_100.py --ws personal --project <dir-wired-or-not>
Never writes the vault (state.json telemetry is skipped: select() is pure).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "hooks" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _home  # type: ignore  # noqa: E402
import _recall_prompt  # type: ignore  # noqa: E402

USER_RE = re.compile(r"^\*\*User:\*\*\s*(.+?)\s*$")


def hook_chars(source: str, cwd: str) -> int:
    ev = json.dumps({"source": source, "cwd": cwd, "session_id": "probe"})
    r = subprocess.run([sys.executable, str(SCRIPTS / "bootstrap-load.py")], input=ev,
                       capture_output=True, text=True)
    if not r.stdout.strip():
        return 0
    return len(json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"])


def real_prompts(ws: str, limit: int = 100) -> list:
    out: list = []
    logs = sorted((_home.workspace_dir(ws) / "journal" / "sessions").glob("*.md"), reverse=True)
    for p in logs:
        for ln in p.read_text(errors="ignore").splitlines():
            m = USER_RE.match(ln)
            if m and len(m.group(1)) >= 40 and not m.group(1).startswith(("/", "!")):
                out.append(m.group(1))
                if len(out) >= limit:
                    return out
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws", default=_home.active_workspace())
    ap.add_argument("--project", default=str(Path.cwd()))
    args = ap.parse_args()
    ws = args.ws
    settings = _home.read_settings()
    start = hook_chars("startup", args.project)
    compact = hook_chars("compact", args.project)
    prompts = real_prompts(ws)
    injected_prompts = 0
    injected_chars = 0
    injected = set()
    for p in prompts:
        hits = _recall_prompt.select(p, ws, settings, injected)
        if hits:
            injected_prompts += 1
            block = _recall_prompt.format_block(ws, hits, _recall_prompt.cfg(settings)["max_chars"])
            injected_chars += len(block)
            injected.update(h["id"] for h in hits if isinstance(h.get("id"), int))
    n = max(1, len(prompts))
    recall_per_100 = int(injected_chars * 100 / n)
    journal_dir = 608 * 10          # measured v4.7.6 directive sizes (audit A2)
    review_dir = 700 * 6
    both_hdr = 246 * 3
    hook_total = start + 2 * compact + journal_dir + review_dir + both_hdr + recall_per_100
    follow_on = 3100 * 10 + 4500 * 6   # teammate dispatch+relay (unchanged), judge at contract
    print(f"workspace={ws} project={args.project}")
    print(f"SessionStart startup emission: {start} chars; compact: {compact} chars")
    print(f"real prompts sampled: {len(prompts)}; got memory: {injected_prompts} "
          f"({100 * injected_prompts // n}%); injected chars: {injected_chars} → {recall_per_100} per 100 prompts")
    print(f"hook-injected per 100 turns: {hook_total} chars (≈{hook_total // 4} tok) "
          f"[start {start} + 2×compact {2 * compact} + journal {journal_dir} + review {review_dir} + both {both_hdr} + recall {recall_per_100}]")
    print(f"follow-on per 100 turns (teammate 10×3.1k + judge 6×4.5k contract): {follow_on} chars (≈{follow_on // 4} tok)")
    print(f"audit baseline (v4.7.6): hook-injected 17,303 chars; follow-on 122k–203k chars")
    return 0


if __name__ == "__main__":
    sys.exit(main())
