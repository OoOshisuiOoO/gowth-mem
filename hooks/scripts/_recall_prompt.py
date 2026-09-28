#!/usr/bin/env python3
"""UserPromptSubmit hook (v4.8): gated per-prompt recall, deterministic, no LLM.

Until v4.8 nothing read memory back after SessionStart: UserPromptSubmit only
checked for SYNC-CONFLICT.md and /mem-recall was invoked by no hook, template or
skill. This hook runs the same BM25 query as /mem-recall on every real prompt
and injects at most `recall.on_prompt_max_entries` (3) entries / `on_prompt_max_chars`
(2,000) chars — and, by default, NOTHING:

  * the prompt is capped at `on_prompt_prompt_cap` (2,000) chars before profiling
    (a pasted 40 KB log must not feed the regexes);
  * a chunk qualifies only when >= `on_prompt_min_terms` (2) distinct query terms
    occur in its heading+content AND its bm25 score is <= `on_prompt_score_threshold`
    (bm25 is negative; lower is better; calibrated on the live vault, Task 12);
  * a chunk is injected at most once per session (`state.json.session[<sid>].recall.ids`);
  * journal/ and memory/MEMORY.md never qualify (MEMORY.md is already in context;
    raw journal is not memory); research/ and handoff-archive follow the
    default excludes of `_query`.

SWE-ContextBench (2026): concise, correctly selected memory improves task
success and cost; autonomous retrieval that picks wrong experience scores below
no memory — hence the strict gate and the silent default.

Output (only when something qualifies):
  {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                          "additionalContext": "[gowth-mem:recall ws=<ws>] …"}}
Always exits 0; empty stdout means "no memory to add".
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _atomic import atomic_write  # type: ignore
from _debug import log_debug  # type: ignore
from _home import (  # type: ignore
    active_workspace,
    clamp_context,
    read_settings,
    setting,
    state_path,
)

DEFAULT_MAX_ENTRIES = 3
DEFAULT_MAX_CHARS = 2_000
DEFAULT_MIN_TERMS = 2
DEFAULT_SCORE_THRESHOLD = -4.0
DEFAULT_PROMPT_CAP = 2_000
SNIPPET_CHARS = 700
IDS_KEEP = 300
FETCH_LIMIT = 8
EXCLUDES = ("journal/", "memory/MEMORY.md")
_TAG_RE = re.compile(r"^\s*(?:#{1,6}\s*)?(?:-\s*)?\[(?:[a-z-]+)\]\s*")
_WS_RE = re.compile(r"\s+")


def cfg(settings: dict) -> dict:
    return {
        "max_entries": max(0, setting("recall.on_prompt_max_entries", int, DEFAULT_MAX_ENTRIES, settings=settings)),
        "max_chars": max(200, setting("recall.on_prompt_max_chars", int, DEFAULT_MAX_CHARS, settings=settings)),
        "min_terms": max(1, setting("recall.on_prompt_min_terms", int, DEFAULT_MIN_TERMS, settings=settings)),
        "score_threshold": setting("recall.on_prompt_score_threshold", float, DEFAULT_SCORE_THRESHOLD, settings=settings),
        "prompt_cap": max(100, setting("recall.on_prompt_prompt_cap", int, DEFAULT_PROMPT_CAP, settings=settings)),
    }


def capped_prompt(prompt: str, settings: dict) -> str:
    return (prompt or "")[: cfg(settings)["prompt_cap"]]


def select(prompt: str, ws: str, settings: dict, injected: set) -> list:
    """Chunks worth injecting for this prompt (pure apart from reading index.db)."""
    c = cfg(settings)
    if c["max_entries"] <= 0:
        return []
    p = capped_prompt(prompt, settings)
    try:
        from _profile import keywords_of  # type: ignore
        from _query import query_ex  # type: ignore
    except Exception as exc:
        log_debug("recall-prompt", f"import failed: {exc}")
        return []
    terms = {t.lower() for t in keywords_of(p) if t}
    if len(terms) < c["min_terms"]:
        return []
    res = query_ex(ws, "", p, limit=FETCH_LIMIT, exclude=EXCLUDES)
    if res.get("error"):
        return []
    out: list = []
    for h in res.get("hits") or []:
        if h.get("id") in injected:
            continue
        text = ((h.get("heading") or "") + " " + (h.get("content") or "")).lower()
        matched = sum(1 for t in terms if t in text)
        if matched < c["min_terms"]:
            continue
        try:
            score = float(h.get("bm25_score") or 0.0)
        except (TypeError, ValueError):
            continue
        if score > c["score_threshold"]:
            continue
        out.append(h)
        if len(out) >= c["max_entries"]:
            break
    return out


def _title_and_snippet(h: dict) -> tuple:
    heading = (h.get("heading") or "").strip()
    content = (h.get("content") or "").strip()
    title = _TAG_RE.sub("", heading) if heading else ""
    if not title and content:
        title = _TAG_RE.sub("", content.splitlines()[0]).strip()
    body = content
    if heading and body.startswith(heading):
        body = body[len(heading):]
    snippet = _WS_RE.sub(" ", body).strip()
    if snippet.startswith(title):
        snippet = snippet[len(title):].strip(" -—:")
    title = _WS_RE.sub(" ", title)[:160]
    room = max(0, SNIPPET_CHARS - len(title))
    if len(snippet) > room:
        snippet = snippet[: max(0, room - 1)].rstrip() + "…"
    return title, snippet


def format_block(ws: str, hits: list, max_chars: int) -> str:
    if not hits:
        return ""
    lines = [f"[gowth-mem:recall ws={ws}] related memory (read the file for more):"]
    total = len(lines[0])
    for h in hits:
        title, snippet = _title_and_snippet(h)
        tag = f"[{h['tag']}] " if h.get("tag") else ""
        line = f"- {h.get('path', '')} {tag}{title}"
        if snippet:
            line += f" — {snippet}"
        if total + len(line) + 1 > max_chars:
            break
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines) if len(lines) > 1 else ""


def _load_state() -> dict:
    p = state_path()
    try:
        data = json.loads(p.read_text()) if p.is_file() else {}
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


def _injected_ids(state: dict, sid: str) -> set:
    try:
        ids = state.get("session", {}).get(sid, {}).get("recall", {}).get("ids", [])
        return {i for i in ids if isinstance(i, int)}
    except Exception:
        return set()


def _record(sid: str, hits: list, chars: int) -> None:
    """Best-effort telemetry under the shared state lock; skipped on timeout."""
    try:
        from _lock import file_lock  # type: ignore
        with file_lock("state", timeout=2.0):
            state = _load_state()
            sessions = state.setdefault("session", {})
            if not isinstance(sessions, dict):
                sessions = state["session"] = {}
            sess = sessions.setdefault(sid, {"turn_count": 0})
            rec = sess.get("recall") if isinstance(sess.get("recall"), dict) else {}
            ids = [i for i in rec.get("ids", []) if isinstance(i, int)]
            ids.extend(int(h["id"]) for h in hits if isinstance(h.get("id"), int))
            sess["recall"] = {
                "injected": int(rec.get("injected", 0) or 0) + 1,
                "entries": int(rec.get("entries", 0) or 0) + len(hits),
                "chars": int(rec.get("chars", 0) or 0) + chars,
                "ids": ids[-IDS_KEEP:],
            }
            atomic_write(state_path(), json.dumps(state, indent=1))
    except Exception as exc:
        log_debug("recall-prompt", f"telemetry skipped: {exc}")


def main() -> int:
    try:
        raw = sys.stdin.read()
    except Exception:
        raw = ""
    try:
        ev = json.loads(raw) if raw.strip() else {}
    except Exception:
        ev = {}
    if not isinstance(ev, dict):
        return 0
    prompt = ev.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return 0
    try:
        settings = read_settings()
        if not setting("recall.on_prompt_enabled", bool, True, settings=settings):
            return 0
        cwd = ev.get("cwd")
        ws = active_workspace(Path(cwd)) if isinstance(cwd, str) and cwd else active_workspace()
        sid = str(ev.get("session_id") or "default")
        injected = _injected_ids(_load_state(), sid)
        hits = select(prompt, ws, settings, injected)
        if not hits:
            return 0
        block = clamp_context(format_block(ws, hits, cfg(settings)["max_chars"]))
        if not block.strip():
            return 0
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                 "additionalContext": block}}))
        _record(sid, hits, len(block))
        log_debug("recall-prompt", f"ws={ws} injected {len(hits)} entries, {len(block)} chars")
    except Exception as exc:
        log_debug("recall-prompt", f"failed: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
