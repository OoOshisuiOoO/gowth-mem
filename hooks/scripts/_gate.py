#!/usr/bin/env python3
"""Hard write-time quality gate (v3.6) — deterministic rules that REJECT junk
*before* it is written to memory. The code-level enforcement of the data-quality
canon (`shared/research/data-quality-2026.md` §1) + the 2026 deep-research
ingestion-gate consensus (mem0 confidence gate, Zep fact-rating, LangMem
importance, Letta "never append speculation").

Why a code gate, not just docs: the canon already documented these rules, yet
11 MB of raw junk still accumulated — because nothing *enforced* them at the
write path. `evaluate()` runs inside `_topic.append_entry` and
`_lesson.append_lesson`; a REJECT verdict blocks the write (the helper returns
`written=False`) and logs the reason. No LLM in the path (gowth-mem is
deterministic): every rule here is checkable by regex / length / format alone.
LLM-scored gates (confidence/importance) stay the agent's discipline in AGENTS.md.

Verdict actions:
  ACCEPT  — passes all gates, write it.
  REJECT  — fails a hard rule (reason given); caller must not write.

CLI:
  python3 _gate.py --check '[ref] ...'        # evaluate one entry, print verdict
  python3 _gate.py --scan [--ws X|--all] [--json]   # find junk in EXISTING files
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _home import (  # type: ignore
    active_workspace, docs_dir, gowth_home, iter_topic_files, journal_dir,
    list_workspaces, read_settings,
)

# ── thresholds (canon §1) ────────────────────────────────────────────────
MIN_BODY_CHARS = 20            # canon §1: body < 20 chars after prefix → DROP
HEDGE_RATIO_MAX = 0.25         # canon §1: hedge words / total > 0.25 → DROP

TAG_RE = re.compile(r"^\s*[-*]?\s*\[([a-z][a-z-]*)\]\s*", re.IGNORECASE)
HEDGE_RE = re.compile(
    r"\b(maybe|i think|i guess|probably|might be|may be|perhaps|possibly|"
    r"seems like|seems to|kinda|sort of|could be|not sure|dunno)\b",
    re.IGNORECASE,
)
# Evidence tokens: presence of any means the claim is grounded (escapes hedge gate).
EVIDENCE_RE = re.compile(
    r"(source:|tried:|fix:|root cause:|https?://|`[^`]+`|"
    r"\b[\w./-]+\.(py|md|js|ts|go|rs|json|yaml|yml|sh|sql|toml)\b|"
    r"\b[0-9a-f]{7,40}\b|:\d+\b|version:)",
    re.IGNORECASE,
)
RATIONALE_RE = re.compile(
    r"\b(because|since|so that|in order to|rationale|reason:|why:|due to|"
    r"vì|bởi|để|do)\b", re.IGNORECASE,
)
# canon §1: [tool] needs a version marker OR a fenced/inline command.
VERSION_RE = re.compile(r"(version:|v?\d+\.\d+|\b\d+\.\d+\.\d+\b|@[\w.-]+)", re.IGNORECASE)
COMMAND_RE = re.compile(r"`[^`]+`|```")
PLACEHOLDER_RE = re.compile(
    r"^\s*(todo|tbd|fixme|xxx|wip|misc|random|stuff|note to self|"
    r"investigate later|placeholder|n/?a|\.\.\.|-+)\s*$",
    re.IGNORECASE,
)
# canon §1a: secret patterns → BLOCK the write outright.
SECRET_RES = [
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"\bsk-[A-Za-z0-9]{32,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{30,}"),
    re.compile(r"\bgho_[A-Za-z0-9]{30,}"),
    re.compile(r"\bghu_[A-Za-z0-9]{30,}"),
    re.compile(r"\bxox[bpsa]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
]


@dataclass
class GateResult:
    ok: bool
    reason: str = ""          # rule id when rejected
    detail: str = ""          # short human explanation

    @property
    def action(self) -> str:
        return "accept" if self.ok else "reject"


def _strip_prefix(content: str) -> tuple[str, str]:
    """Return (tag, body) — tag lowercased without brackets, body = rest."""
    m = TAG_RE.match(content or "")
    if not m:
        return "", (content or "").strip()
    return m.group(1).lower(), (content or "")[m.end():].strip()


def has_secret(text: str) -> bool:
    return any(rx.search(text or "") for rx in SECRET_RES)


def evaluate(content: str, *, strict: bool | None = None) -> GateResult:
    """Deterministic hard gate for a single memory entry. First failure wins.

    `strict` (default from settings.gate.strict, else True) toggles the
    per-type schema rules (ref→Source, decision→rationale, tool→version).
    The base rules (empty / too-short / placeholder / secret) always apply.
    """
    if strict is None:
        try:
            strict = bool(read_settings().get("gate", {}).get("strict", True))
        except Exception:
            strict = True

    raw = content or ""
    if not raw.strip():
        return GateResult(False, "empty", "blank entry")
    if has_secret(raw):
        return GateResult(False, "secret_leak", "secret pattern detected — store a pointer, never the value")

    tag, body = _strip_prefix(raw)

    if PLACEHOLDER_RE.match(body):
        return GateResult(False, "placeholder", f"junk placeholder: {body[:30]!r}")
    if len(body) < MIN_BODY_CHARS:
        return GateResult(False, "too_short", f"body {len(body)} < {MIN_BODY_CHARS} chars")

    # Hedge gate: dominated by uncertainty AND no grounding evidence.
    words = re.findall(r"\w+", body.lower())
    if words:
        hedge_hits = len(HEDGE_RE.findall(body))
        if hedge_hits and not EVIDENCE_RE.search(raw):
            ratio = hedge_hits / max(1, len(words))
            if ratio > HEDGE_RATIO_MAX or len(words) < 12:
                return GateResult(False, "hedged_no_evidence",
                                  "uncertain language with no source/evidence")

    if strict and tag:
        if tag == "ref" and not re.search(r"source:", raw, re.IGNORECASE) and not re.search(r"https?://", raw):
            return GateResult(False, "ref_without_source", "[ref] requires Source:")
        if tag == "decision" and not RATIONALE_RE.search(raw):
            return GateResult(False, "decision_without_rationale", "[decision] requires a because/since/rationale clause")
        if tag == "tool" and not (VERSION_RE.search(raw) or COMMAND_RE.search(raw)):
            return GateResult(False, "tool_without_version_or_syntax", "[tool] requires a version or a `command`")

    return GateResult(True)


def evaluate_lesson(symptom: str, tried: str, root_cause: str, fix: str, source: str = "") -> GateResult:
    """Lighter gate for the 5-field lesson schema (fields already structured)."""
    joined = " ".join(filter(None, [symptom, tried, root_cause, fix, source]))
    if has_secret(joined):
        return GateResult(False, "secret_leak", "secret pattern in lesson fields")
    if len((symptom or "").strip()) < 8:
        return GateResult(False, "too_short", "symptom too short to be reusable")
    if PLACEHOLDER_RE.match((symptom or "").strip()):
        return GateResult(False, "placeholder", "placeholder symptom")
    return GateResult(True)


# ── scanner: find junk in EXISTING files (non-destructive reporter) ───────
ENTRY_LINE_RE = re.compile(r"^\s*[-*]\s+\[[a-z-]+\]", re.IGNORECASE)


def scan_workspace(ws: str) -> list[dict]:
    out: list[dict] = []
    files: list[Path] = []
    try:
        files.extend(iter_topic_files(ws))
    except Exception:
        pass
    dd = docs_dir(ws)
    if dd.is_dir():
        files.extend(p for p in dd.glob("*.md") if p.is_file())
    for f in files:
        try:
            lines = f.read_text(errors="ignore").splitlines()
        except Exception:
            continue
        for i, line in enumerate(lines, 1):
            if not ENTRY_LINE_RE.match(line):
                continue
            entry = line.lstrip("-*").strip()
            v = evaluate(entry)
            if not v.ok:
                out.append({"file": str(f), "line": i, "reason": v.reason, "text": entry[:100]})
    return out


def _cli() -> int:
    ap = argparse.ArgumentParser(description="Hard write-time quality gate (deterministic).")
    ap.add_argument("--check", help="Evaluate a single entry string")
    ap.add_argument("--scan", action="store_true", help="Scan existing files for junk entries")
    ap.add_argument("--ws", help="Workspace (default: active)")
    ap.add_argument("--all", action="store_true", help="Scan all workspaces")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.check is not None:
        v = evaluate(args.check)
        if args.json:
            print(json.dumps({"action": v.action, "reason": v.reason, "detail": v.detail}))
        else:
            print(f"{v.action.upper()}" + (f": {v.reason} — {v.detail}" if not v.ok else ""))
        return 0 if v.ok else 1

    if args.scan:
        if not gowth_home().is_dir():
            print("no ~/.gowth-mem directory")
            return 0
        wss = list_workspaces() if args.all else [args.ws or active_workspace()]
        findings: list[dict] = []
        for ws in wss:
            findings.extend(scan_workspace(ws))
        if args.json:
            print(json.dumps(findings, indent=2))
            return 0
        if not findings:
            print("gate scan: no junk entries found. Store is clean.")
            return 0
        print(f"gate scan: {len(findings)} entr(y/ies) would be REJECTED by the hard rules:")
        by_reason: dict[str, int] = {}
        for f in findings:
            by_reason[f["reason"]] = by_reason.get(f["reason"], 0) + 1
        for reason, n in sorted(by_reason.items(), key=lambda x: -x[1]):
            print(f"  {n:>4}  {reason}")
        print("  (run with --json for file:line detail)")
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
