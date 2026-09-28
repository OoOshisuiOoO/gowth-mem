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
    occur in its heading+content, those cover >= `on_prompt_min_coverage` (0.5)
    of the prompt's content terms, the prompt's SUBJECT (its first identifier,
    else its longest content word — `_profile.profile`) is among them, AND its
    bm25 score is <= `on_prompt_score_threshold` (bm25 is negative; lower is
    better). Review I1 measured the 2-term + threshold gate alone on the live
    vault copy: it injected on 97% of 300 real prompts and 14/20 realistic
    generic ones; coverage + subject brings that to 0-3/20 generic per
    workspace while 29/30 paraphrased real queries are still served;
  * a chunk is injected at most once per session (`state.json.session[<sid>].recall.ids`);
    every profiled prompt is counted in `state.json.recall_daily[<date>]`
    (prompts / injected / entries, 14 days) so the injection rate is observable;
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
import math
import re
import sys
from datetime import date
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
DEFAULT_MIN_COVERAGE = 0.5
DAILY_KEEP = 14
# bm25 magnitudes scale with idf ≈ ln(N/df), so the cut-off that silences generic
# prompts on a 15k-chunk vault would block everything on a small one. Measured
# 2026-09-28 on the live copy (15,609 live chunks): generic prompts that pass the
# 2-term gate score -5.0 … -9.7 (0 injections only at <= -12); the 30 sampled real
# queries score -14 … -88 (median -50). A 6-chunk fixture: seeded queries score
# -1.6 … -9, generic prompts never pass the term gate. Default "auto" =
# min(AUTO_MIN, AUTO_B - AUTO_A * ln(live chunks)): 15,609 → -12.5; 1,000 → -8.2;
# 100 → -4.6; <= 9 → -1.0. A number in settings pins it.
AUTO_A = 1.55
AUTO_B = 2.5
AUTO_MIN = -1.0
DEFAULT_PROMPT_CAP = 2_000
SNIPPET_CHARS = 700
IDS_KEEP = 300
FETCH_LIMIT = 8
EXCLUDES = ("journal/", "memory/MEMORY.md")
_TAG_RE = re.compile(r"^\s*(?:#{1,6}\s*)?(?:-\s*)?\[(?:[a-z-]+)\]\s*")
_WS_RE = re.compile(r"\s+")


def _threshold_setting(settings: dict) -> "float | None":
    """None = auto (the default and the string "auto"); a number pins it."""
    raw = setting("recall.on_prompt_score_threshold", object, None, settings=settings)
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        if raw.strip().lower() == "auto":
            return None
        try:
            return float(raw)
        except ValueError:
            return None
    return None


def cfg(settings: dict) -> dict:
    return {
        "max_entries": max(0, setting("recall.on_prompt_max_entries", int, DEFAULT_MAX_ENTRIES, settings=settings)),
        "max_chars": max(200, setting("recall.on_prompt_max_chars", int, DEFAULT_MAX_CHARS, settings=settings)),
        "min_terms": max(1, setting("recall.on_prompt_min_terms", int, DEFAULT_MIN_TERMS, settings=settings)),
        "min_coverage": min(1.0, max(0.0, setting("recall.on_prompt_min_coverage", float,
                                                  DEFAULT_MIN_COVERAGE, settings=settings))),
        "score_threshold": _threshold_setting(settings),
        "prompt_cap": max(100, setting("recall.on_prompt_prompt_cap", int, DEFAULT_PROMPT_CAP, settings=settings)),
    }


def auto_threshold(live_chunks: int) -> float:
    import math
    return min(AUTO_MIN, AUTO_B - AUTO_A * math.log(max(2, int(live_chunks or 0))))


def live_chunk_count() -> int:
    """Rows in the live (non-archive) index; 0 when there is no index."""
    try:
        import sqlite3
        from _home import index_db  # type: ignore
        p = index_db()
        if not p.is_file():
            return 0
        db = sqlite3.connect(str(p))
        try:
            db.execute("PRAGMA busy_timeout=1000")
            return int(db.execute("SELECT count(*) FROM chunks WHERE path NOT LIKE '.archive/%'").fetchone()[0])
        finally:
            db.close()
    except Exception:
        return 0


def effective_threshold(settings: dict) -> float:
    thr = cfg(settings)["score_threshold"]
    return thr if thr is not None else auto_threshold(live_chunk_count())


def capped_prompt(prompt: str, settings: dict) -> str:
    return (prompt or "")[: cfg(settings)["prompt_cap"]]


def select(prompt: str, ws: str, settings: dict, injected: set) -> list:
    """Chunks worth injecting for this prompt (pure apart from reading index.db)."""
    c = cfg(settings)
    if c["max_entries"] <= 0:
        return []
    p = capped_prompt(prompt, settings)
    try:
        from _profile import profile  # type: ignore
        from _query import query_ex  # type: ignore
    except Exception as exc:
        log_debug("recall-prompt", f"import failed: {exc}")
        return []
    prof = profile(p)
    terms = {t.lower() for t in (prof.get("keywords") or []) if t}
    if len(terms) < c["min_terms"]:
        return []
    subject = (prof.get("subject") or "").lower()
    need = max(c["min_terms"], math.ceil(c["min_coverage"] * len(terms)))
    res = query_ex(ws, "", p, limit=FETCH_LIMIT, exclude=EXCLUDES)
    if res.get("error"):
        return []
    threshold = effective_threshold(settings)
    out: list = []
    for h in res.get("hits") or []:
        if h.get("id") in injected:
            continue
        text = ((h.get("heading") or "") + " " + (h.get("content") or "")).lower()
        matched = {t for t in terms if t in text}
        if len(matched) < need:
            continue
        if subject and subject not in matched:
            continue
        try:
            score = float(h.get("bm25_score") or 0.0)
        except (TypeError, ValueError):
            continue
        if score > threshold:
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


def _bump_daily(state: dict, hits: list) -> None:
    """Per-day totals (review M13): every profiled prompt counts, injecting or
    not, so the injection RATE can be read from /mem-cost. 14 days kept."""
    daily = state.get("recall_daily")
    if not isinstance(daily, dict):
        daily = state["recall_daily"] = {}
    today = date.today().isoformat()
    day = daily.get(today) if isinstance(daily.get(today), dict) else {}
    daily[today] = {
        "prompts": int(day.get("prompts", 0) or 0) + 1,
        "injected": int(day.get("injected", 0) or 0) + (1 if hits else 0),
        "entries": int(day.get("entries", 0) or 0) + len(hits),
    }
    for k in sorted(daily)[:-DAILY_KEEP]:
        daily.pop(k, None)


def _record(sid: str, hits: list, chars: int) -> None:
    """Best-effort telemetry under the shared state lock; skipped on timeout.
    Called for EVERY profiled prompt; session ids/chars only when injecting."""
    try:
        from _lock import file_lock  # type: ignore
        with file_lock("state", timeout=2.0):
            state = _load_state()
            _bump_daily(state, hits)
            if hits:
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
            _record(sid, [], 0)
            return 0
        block = clamp_context(format_block(ws, hits, cfg(settings)["max_chars"]))
        if not block.strip():
            _record(sid, [], 0)
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
