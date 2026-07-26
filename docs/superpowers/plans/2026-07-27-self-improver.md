# Self-Improver (v4.2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the review→rule loop: a deterministic miner clusters recurring `[reflection]` entries + `_scores.md` trends per workspace and, via a hook-injected instruction block, gets in-session Claude to draft rule promotions that the user approves per-proposal before any write to `GUILD.md` / `AGENTS.md` / the self-review template.

**Architecture:** New stdlib library `hooks/scripts/_evolve.py` (collect → cluster → trend-parse → candidates JSON + state ledger), wired into `auto-journal.py` right after the self-review reason fires, plus a bootstrap nudge, a `/mem-evolve` command, and two templates (GUILD.md scaffold, self-improve instruction block). Spec: `docs/superpowers/specs/2026-07-27-self-improver-design.md`.

**Tech Stack:** Pure Python 3.9+ stdlib (repo rule), unittest, existing helpers `_home` / `_atomic` / `_debug` / `_tags` / `_lexical`.

## Global Constraints

- Pure stdlib in `hooks/scripts/*.py` — zero pip deps (repo rule).
- Every hook path: **always exit 0, no traceback, silent when nothing to do**; failures go to `_debug.log_debug`.
- All writes under the vault go through `_atomic.atomic_write`.
- Never hardcode `~/.gowth-mem/` — resolve via `_home` (`gowth_home()`, `workspace_dir(ws)`, `journal_dir(ws)`); tests isolate with `GOWTH_MEM_HOME` temp dir.
- Command frontmatter `description:` must not contain a bare `: ` (breaks YAML parse — v3.6 lesson).
- Settings read with `.get(...)` fallbacks — no settings-file migration. New section: `self_improve: {enabled: true, min_cluster: 3, score_stuck_blocks: 3}`.
- GUILD.md is a reserved workspace-root doc (like AGENTS.md), NOT a topic file, NOT indexed into the 9-type schema.
- Deviation from spec noted: reflection collection is **filesystem-scan only** (no FTS5) — files are ground truth, index may be stale/absent; FS scan is complete, deterministic, and removes the index dependency.
- Before any release: run every new code path once on the real vault (repo pre-tag rule).
- Verification before completion: `python3 -m unittest discover -s tests` (402 + new) AND `python3 -m py_compile hooks/scripts/*.py` AND `bin/test-install.sh` (hook path touched).

---

### Task 1: `_evolve.py` — state ledger + reflection collection

**Files:**
- Create: `hooks/scripts/_evolve.py`
- Test: `tests/test_evolve.py`

**Interfaces:**
- Consumes: `_home.gowth_home()`, `_home.workspace_dir(ws)`, `_home.journal_dir(ws)`, `_home.docs_dir(ws)`, `_atomic.atomic_write(path, content)`, `_lexical._normalize(text)`, `_debug.log_debug(component, msg)`
- Produces (used by Tasks 2/4/5):
  - `load_state() -> dict` — `{"processed": {hash: iso-date}, "rejected": {cluster_key: {"date":…, "keywords": […]}}, "pending": [candidate…]}`
  - `save_state(state: dict) -> None`
  - `reflection_hash(text: str) -> str` — sha1 hex of normalized text
  - `collect_reflections(ws: str) -> list[dict]` — `[{"text": str, "source": str(rel path), "hash": str}]`, deduped by hash, sorted by hash (deterministic)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_evolve.py
"""Tests for _evolve.py — self-improver miner (v4.2)."""
import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "hooks" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _reload():
    """(Re)import _home then _evolve so GOWTH_MEM_HOME is honored."""
    for name in ("_home", "_evolve"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])
    import _evolve  # noqa: F401
    return sys.modules["_evolve"]


class EvolveBase(unittest.TestCase):
    WS = "default"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["GOWTH_MEM_HOME"] = self.tmp.name
        self.home = Path(self.tmp.name)
        self.ws_dir = self.home / "workspaces" / self.WS
        (self.ws_dir / "journal").mkdir(parents=True)
        (self.ws_dir / "docs").mkdir(parents=True)
        self.evolve = _reload()

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)
        self.tmp.cleanup()

    def write_topic_reflection(self, topic, fname, lines):
        d = self.ws_dir / topic
        d.mkdir(exist_ok=True)
        body = "\n".join(f"[reflection] {ln}" for ln in lines)
        (d / fname).write_text(
            f"---\ntype: aspect\n---\n\n{body}\n", encoding="utf-8")


class TestStateLedger(EvolveBase):
    def test_fresh_state_shape(self):
        st = self.evolve.load_state()
        self.assertEqual(st["processed"], {})
        self.assertEqual(st["rejected"], {})
        self.assertEqual(st["pending"], [])

    def test_state_round_trip(self):
        st = self.evolve.load_state()
        st["processed"]["abc123"] = "2026-07-27"
        st["rejected"]["k1+k2"] = {"date": "2026-07-27", "keywords": ["k1", "k2"]}
        self.evolve.save_state(st)
        again = self.evolve.load_state()
        self.assertEqual(again["processed"]["abc123"], "2026-07-27")
        self.assertIn("k1+k2", again["rejected"])

    def test_corrupt_state_starts_fresh(self):
        (self.home / "evolve-state.json").write_text("{not json", encoding="utf-8")
        st = self.evolve.load_state()
        self.assertEqual(st["processed"], {})


class TestCollect(EvolveBase):
    def test_collects_from_topic_and_journal(self):
        self.write_topic_reflection(
            "deploys", "2026-07-01-notes.md",
            ["Always name the exact push target remote in the reply after pushing."])
        (self.ws_dir / "journal" / "2026-07-02.md").write_text(
            "# day\n\n## [reflection] Verify live state before re-explaining from memory\n",
            encoding="utf-8")
        items = self.evolve.collect_reflections(self.WS)
        texts = " || ".join(i["text"] for i in items)
        self.assertEqual(len(items), 2)
        self.assertIn("push target", texts)
        self.assertIn("Verify live state", texts)
        for i in items:
            self.assertTrue(i["hash"])
            self.assertTrue(i["source"])

    def test_dedup_and_deterministic_order(self):
        line = "Read the data-flow log before measuring per-stage latency."
        self.write_topic_reflection("t1", "2026-07-01-a.md", [line])
        self.write_topic_reflection("t2", "2026-07-01-b.md", [line])
        items = self.evolve.collect_reflections(self.WS)
        self.assertEqual(len(items), 1)  # same normalized text → same hash → dedup
        twice = self.evolve.collect_reflections(self.WS)
        self.assertEqual([i["hash"] for i in items], [i["hash"] for i in twice])

    def test_skips_archive_and_readme(self):
        self.write_topic_reflection("t1", "00-README.md",
                                    ["MOC noise should never be collected here."])
        arch = self.ws_dir / ".archive"
        arch.mkdir()
        (arch / "old.md").write_text("[reflection] archived noise\n", encoding="utf-8")
        items = self.evolve.collect_reflections(self.WS)
        self.assertEqual(items, [])

    def test_missing_workspace_graceful(self):
        items = self.evolve.collect_reflections("nope")
        self.assertEqual(items, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_evolve -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named '_evolve'`

- [ ] **Step 3: Implement `_evolve.py` (state + collect)**

```python
#!/usr/bin/env python3
"""_evolve.py — self-improver miner (v4.2).

Deterministically mines UNPROCESSED [reflection] entries in one workspace,
clusters recurring patterns by keyword overlap, parses journal/_scores.md
trends, and emits promotion candidates. Never writes rules itself — the
in-session drafter (templates/self-improve-instructions.md) does that, with
per-proposal user approval. State ledger: <vault>/evolve-state.json
(machine-local, gitignored like state.json).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _atomic import atomic_write          # noqa: E402
from _debug import log_debug              # noqa: E402
from _home import gowth_home, workspace_dir, journal_dir, docs_dir  # noqa: E402
from _lexical import _normalize           # noqa: E402

STATE_NAME = "evolve-state.json"
REFLECTION_RE = re.compile(r"\[reflection\]\s*(.+)")
SKIP_DIRS = {".archive", ".git"}
SKIP_FILES = {"00-README.md"}


# ---------------------------------------------------------------- state

def _state_path() -> Path:
    return gowth_home() / STATE_NAME


def _fresh_state() -> dict:
    return {"processed": {}, "rejected": {}, "pending": []}


def load_state() -> dict:
    p = _state_path()
    if not p.is_file():
        return _fresh_state()
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            raise ValueError("state not a dict")
    except Exception as exc:  # corrupt → fresh (log, never crash)
        log_debug("evolve", f"corrupt state, starting fresh: {exc}")
        return _fresh_state()
    for key, default in _fresh_state().items():
        d.setdefault(key, default)
    return d


def save_state(state: dict) -> None:
    atomic_write(_state_path(), json.dumps(state, indent=1, ensure_ascii=False))


# ---------------------------------------------------------------- collect

def reflection_hash(text: str) -> str:
    return hashlib.sha1(_normalize(text).encode("utf-8")).hexdigest()


def _iter_md_files(ws: str) -> list[Path]:
    root = workspace_dir(ws)
    if not root.is_dir():
        return []
    out: list[Path] = []
    for p in sorted(root.rglob("*.md")):
        rel_parts = p.relative_to(root).parts
        if any(part in SKIP_DIRS for part in rel_parts):
            continue
        if p.name in SKIP_FILES:
            continue
        out.append(p)
    return out


def collect_reflections(ws: str) -> list[dict]:
    """All [reflection] lines in the workspace, deduped by normalized hash."""
    root = workspace_dir(ws)
    seen: dict[str, dict] = {}
    for p in _iter_md_files(ws):
        try:
            content = p.read_text(encoding="utf-8")
        except Exception:
            continue
        for m in REFLECTION_RE.finditer(content):
            text = m.group(1).strip().rstrip("#").strip()
            if len(text) < 20:  # junk guard, same floor as _gate
                continue
            h = reflection_hash(text)
            if h not in seen:
                seen[h] = {"text": text,
                           "source": str(p.relative_to(root)),
                           "hash": h}
    return sorted(seen.values(), key=lambda d: d["hash"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_evolve -v`
Expected: 7 tests PASS

- [ ] **Step 5: Confirm gitignore covers the state file**

Run: `grep -n "evolve-state\|^state.json\|\*.json" ~/.gowth-mem/.gitignore 2>/dev/null || true`
The vault `.gitignore` is scaffolded by `/mem-install`; check `commands/mem-install.md` for the gitignore block and add `evolve-state.json` beside `state.json` if listed explicitly. (If the block lists `state.json`, add a sibling line.)

- [ ] **Step 6: Commit**

```bash
git add hooks/scripts/_evolve.py tests/test_evolve.py commands/mem-install.md
git commit -m "feat(evolve): state ledger + deterministic reflection collection"
```

---

### Task 2: `_evolve.py` — clustering, score trends, scan(), CLI

**Files:**
- Modify: `hooks/scripts/_evolve.py` (append)
- Test: `tests/test_evolve.py` (append)

**Interfaces:**
- Consumes: Task 1 functions; `_tags.extract_tags(text, max_tags)` ; `_home.read_settings()`
- Produces (used by Tasks 3-5):
  - `cluster_reflections(items: list[dict], min_cluster: int) -> list[dict]` — each `{"cluster_key": str, "keywords": [str], "count": int, "members": [{"text","source","hash"}]}`
  - `parse_score_trends(ws: str, window: int) -> list[dict]` — each `{"dimension": "prompting"|"reasoning"|"collab", "kind": "stuck"|"declining", "values": [int], "window": int}`
  - `scan(ws: str) -> dict` — `{"ws": str, "clusters": […], "score_signals": […]}` filtered by state; also persists candidates into `state["pending"]`
  - `pending_count() -> int`
  - `mark(hashes: list[str], status: str, cluster_key: str = "", keywords: list[str] | None = None) -> dict`
  - CLI: `--scan [--ws X] [--json]`, `--mark H… --status processed|rejected [--cluster-key K]`, `--stats`

- [ ] **Step 1: Write the failing tests (append to tests/test_evolve.py)**

```python
SIMILAR = [
    "Verify the deploy target remote before pushing firmware to production repo.",
    "Name the deploy target remote explicitly when pushing to the production repo.",
    "Never push without confirming which deploy target remote the production repo uses.",
]
UNRELATED = "Ask about hidden SSID and 2.4GHz support before rewriting WiFi NVS config."

SCORES_HEADER = (
    "# Session self-review scores\n\n"
    "| date | sid | turn | prompting | reasoning | collab | delta-vs-last |\n"
    "|------|-----|------|-----------|-----------|--------|---------------|\n")


class TestClustering(EvolveBase):
    def test_three_similar_cluster_two_do_not(self):
        items = [{"text": t, "source": "s.md",
                  "hash": self.evolve.reflection_hash(t)}
                 for t in SIMILAR + [UNRELATED]]
        clusters = self.evolve.cluster_reflections(items, min_cluster=3)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["count"], 3)
        two = [items[0], items[1]]
        self.assertEqual(self.evolve.cluster_reflections(two, min_cluster=3), [])

    def test_cluster_key_stable_across_orderings(self):
        items = [{"text": t, "source": "s.md",
                  "hash": self.evolve.reflection_hash(t)} for t in SIMILAR]
        k1 = self.evolve.cluster_reflections(items, 3)[0]["cluster_key"]
        k2 = self.evolve.cluster_reflections(list(reversed(items)), 3)[0]["cluster_key"]
        self.assertEqual(k1, k2)


class TestScoreTrends(EvolveBase):
    def _write_scores(self, rows):
        body = SCORES_HEADER + "".join(
            f"| 2026-07-{10+i:02d} | sid{i} | 15 | {p} | {r} | {c} | d |\n"
            for i, (p, r, c) in enumerate(rows))
        (self.ws_dir / "journal" / "_scores.md").write_text(body, encoding="utf-8")

    def test_stuck_dimension_fires(self):
        self._write_scores([(2, 4, 4), (2, 4, 4), (2, 4, 4)])
        sigs = self.evolve.parse_score_trends(self.WS, window=3)
        self.assertEqual(len(sigs), 1)
        self.assertEqual(sigs[0]["dimension"], "prompting")
        self.assertEqual(sigs[0]["kind"], "stuck")

    def test_declining_dimension_fires(self):
        self._write_scores([(3, 5, 4), (3, 4, 4), (3, 3, 4)])
        sigs = self.evolve.parse_score_trends(self.WS, window=3)
        self.assertEqual([s["dimension"] for s in sigs], ["reasoning"])
        self.assertEqual(sigs[0]["kind"], "declining")

    def test_healthy_and_short_tables_silent(self):
        self._write_scores([(3, 4, 4), (4, 4, 5), (3, 5, 4)])
        self.assertEqual(self.evolve.parse_score_trends(self.WS, 3), [])
        self._write_scores([(1, 1, 1)])  # shorter than window
        self.assertEqual(self.evolve.parse_score_trends(self.WS, 3), [])
        (self.ws_dir / "journal" / "_scores.md").unlink()
        self.assertEqual(self.evolve.parse_score_trends(self.WS, 3), [])


class TestScan(EvolveBase):
    def seed(self):
        self.write_topic_reflection("deploys", "2026-07-01-a.md", [SIMILAR[0]])
        self.write_topic_reflection("deploys", "2026-07-02-b.md", [SIMILAR[1]])
        self.write_topic_reflection("deploys", "2026-07-03-c.md", [SIMILAR[2]])
        self.write_topic_reflection("wifi", "2026-07-03-d.md", [UNRELATED])

    def test_scan_finds_cluster_and_persists_pending(self):
        self.seed()
        res = self.evolve.scan(self.WS)
        self.assertEqual(len(res["clusters"]), 1)
        self.assertEqual(res["clusters"][0]["count"], 3)
        self.assertEqual(self.evolve.pending_count(), 1)

    def test_processed_hashes_excluded(self):
        self.seed()
        res = self.evolve.scan(self.WS)
        hashes = [m["hash"] for m in res["clusters"][0]["members"]]
        self.evolve.mark(hashes, "processed")
        self.assertEqual(self.evolve.scan(self.WS)["clusters"], [])
        self.assertEqual(self.evolve.pending_count(), 0)

    def test_rejected_cluster_never_reproposed(self):
        self.seed()
        res = self.evolve.scan(self.WS)
        c = res["clusters"][0]
        self.evolve.mark([], "rejected", cluster_key=c["cluster_key"],
                         keywords=c["keywords"])
        self.assertEqual(self.evolve.scan(self.WS)["clusters"], [])

    def test_cli_scan_json(self):
        import subprocess
        self.seed()
        out = subprocess.run(
            [sys.executable, str(SCRIPTS / "_evolve.py"),
             "--scan", "--ws", self.WS, "--json"],
            capture_output=True, text=True,
            env={**os.environ, "GOWTH_MEM_HOME": self.tmp.name})
        self.assertEqual(out.returncode, 0, out.stderr)
        data = json.loads(out.stdout)
        self.assertEqual(len(data["clusters"]), 1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_evolve -v`
Expected: new tests ERROR with `AttributeError: module '_evolve' has no attribute 'cluster_reflections'`

- [ ] **Step 3: Implement clustering + trends + scan + CLI (append to `_evolve.py`)**

```python
from _home import read_settings           # noqa: E402  (top of file, with other imports)
from _tags import extract_tags            # noqa: E402

DIMENSIONS = ("prompting", "reasoning", "collab")
SCORE_ROW_RE = re.compile(
    r"^\|\s*[^|]+\|\s*[^|]+\|\s*[^|]+\|\s*(\d)\s*\|\s*(\d)\s*\|\s*(\d)\s*\|")


def _si_settings() -> dict:
    si = read_settings().get("self_improve", {})
    if not isinstance(si, dict):
        si = {}
    return {
        "enabled": bool(si.get("enabled", True)),
        "min_cluster": int(si.get("min_cluster", 3)),
        "score_stuck_blocks": int(si.get("score_stuck_blocks", 3)),
    }


def _keywords(text: str) -> set[str]:
    return set(extract_tags(text, max_tags=8))


def cluster_reflections(items: list[dict], min_cluster: int) -> list[dict]:
    """Union-find over pairs sharing >=2 keywords. Deterministic on input order
    (items arrive hash-sorted from collect_reflections)."""
    kw = [_keywords(i["text"]) for i in items]
    parent = list(range(len(items)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a in range(len(items)):
        for b in range(a + 1, len(items)):
            if len(kw[a] & kw[b]) >= 2:
                parent[find(a)] = find(b)

    groups: dict[int, list[int]] = {}
    for i in range(len(items)):
        groups.setdefault(find(i), []).append(i)

    out: list[dict] = []
    for members in groups.values():
        if len(members) < min_cluster:
            continue
        counts: dict[str, int] = {}
        for m in members:
            for k in kw[m]:
                counts[k] = counts.get(k, 0) + 1
        top = sorted(counts, key=lambda k: (-counts[k], k))[:3]
        out.append({
            "cluster_key": "+".join(sorted(top)),
            "keywords": top,
            "count": len(members),
            "members": [items[m] for m in sorted(members)],
        })
    return sorted(out, key=lambda c: c["cluster_key"])


def parse_score_trends(ws: str, window: int) -> list[dict]:
    path = journal_dir(ws) / "_scores.md"
    if not path.is_file():
        return []
    rows: list[tuple[int, int, int]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = SCORE_ROW_RE.match(line.strip())
        if m:
            rows.append(tuple(int(g) for g in m.groups()))
    if len(rows) < window:
        return []
    tail = rows[-window:]
    signals: list[dict] = []
    for d_idx, dim in enumerate(DIMENSIONS):
        vals = [r[d_idx] for r in tail]
        if all(v <= 2 for v in vals):
            signals.append({"dimension": dim, "kind": "stuck",
                            "values": vals, "window": window})
        elif all(vals[i] > vals[i + 1] for i in range(len(vals) - 1)):
            signals.append({"dimension": dim, "kind": "declining",
                            "values": vals, "window": window})
    return signals


def scan(ws: str) -> dict:
    cfg = _si_settings()
    state = load_state()
    items = [i for i in collect_reflections(ws)
             if i["hash"] not in state["processed"]]
    clusters = [c for c in cluster_reflections(items, cfg["min_cluster"])
                if c["cluster_key"] not in state["rejected"]]
    signals = parse_score_trends(ws, cfg["score_stuck_blocks"])
    state["pending"] = [{"ws": ws, "cluster_key": c["cluster_key"],
                         "count": c["count"]} for c in clusters]
    save_state(state)
    return {"ws": ws, "clusters": clusters, "score_signals": signals}


def pending_count() -> int:
    return len(load_state()["pending"])


def mark(hashes: list[str], status: str, cluster_key: str = "",
         keywords: list[str] | None = None) -> dict:
    state = load_state()
    today = date.today().isoformat()
    if status == "processed":
        for h in hashes:
            state["processed"][h] = today
        state["pending"] = [p for p in state["pending"]
                            if p.get("cluster_key") != cluster_key] \
            if cluster_key else []
    elif status == "rejected":
        if cluster_key:
            state["rejected"][cluster_key] = {"date": today,
                                              "keywords": keywords or []}
            state["pending"] = [p for p in state["pending"]
                                if p.get("cluster_key") != cluster_key]
    save_state(state)
    return state


def main() -> int:
    ap = argparse.ArgumentParser(description="gowth-mem self-improver miner")
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--ws", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--mark", nargs="*", default=None, metavar="HASH")
    ap.add_argument("--status", choices=["processed", "rejected"],
                    default="processed")
    ap.add_argument("--cluster-key", default="")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()
    try:
        if args.scan:
            from _home import active_workspace
            ws = args.ws or active_workspace()
            res = scan(ws)
            if args.json:
                print(json.dumps(res, ensure_ascii=False, indent=1))
            else:
                print(f"ws={res['ws']} clusters={len(res['clusters'])} "
                      f"score_signals={len(res['score_signals'])}")
        elif args.mark is not None:
            st = mark(args.mark, args.status, cluster_key=args.cluster_key)
            print(f"marked {len(args.mark)} {args.status}; "
                  f"pending={len(st['pending'])}")
        elif args.stats:
            st = load_state()
            print(json.dumps({"processed": len(st["processed"]),
                              "rejected": len(st["rejected"]),
                              "pending": len(st["pending"])}))
        else:
            ap.print_help()
    except Exception as exc:
        log_debug("evolve", f"cli failed: {exc}")
        print(f"evolve error: {exc}", file=sys.stderr)
        return 0  # never a hard failure
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests + compile check**

Run: `python3 -m unittest tests.test_evolve -v && python3 -m py_compile hooks/scripts/_evolve.py`
Expected: all PASS. If the clustering test is flaky on keyword extraction (extract_tags may tag differently than expected), adjust the SIMILAR fixtures to share ≥2 obvious identifiers (e.g. include the literal words "deploy target remote" in all three) — do NOT loosen the ≥2-overlap rule.

- [ ] **Step 5: Commit**

```bash
git add hooks/scripts/_evolve.py tests/test_evolve.py
git commit -m "feat(evolve): keyword-overlap clustering, score-trend signals, scan CLI + state marks"
```

---

### Task 3: GUILD.md — template, reserved name, bootstrap load

**Files:**
- Create: `templates/GUILD.md`
- Modify: `hooks/scripts/_home.py:196` (RESERVED_FILES)
- Modify: `hooks/scripts/bootstrap-load.py:146-152` (stable list)
- Test: `tests/test_evolve.py` (append)

**Interfaces:**
- Produces: `RESERVED_FILES` includes `"GUILD.md"` (topic router can never route into it); bootstrap loads `workspaces/<ws>/GUILD.md` when present.

- [ ] **Step 1: Write the failing tests (append to tests/test_evolve.py)**

```python
class TestGuildIntegration(EvolveBase):
    def test_guild_is_reserved(self):
        import _home
        self.assertTrue(_home.is_reserved("GUILD.md"))

    def test_bootstrap_loads_guild(self):
        import subprocess
        (self.ws_dir / "GUILD.md").write_text(
            "# GUILD\n\n## Rules\n\n- test rule (promoted 2026-07-27)\n",
            encoding="utf-8")
        # minimal shared files so bootstrap doesn't bail
        shared = self.home / "shared"
        shared.mkdir(exist_ok=True)
        (shared / "AGENTS.md").write_text("# AGENTS\n", encoding="utf-8")
        out = subprocess.run(
            [sys.executable, str(SCRIPTS / "bootstrap-load.py")],
            capture_output=True, text=True, input="{}",
            env={**os.environ, "GOWTH_MEM_HOME": self.tmp.name})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("test rule", out.stdout)
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest tests.test_evolve.TestGuildIntegration -v`
Expected: `test_guild_is_reserved` FAILS (GUILD.md not reserved); `test_bootstrap_loads_guild` FAILS (rule text absent). If bootstrap-load requires other preconditions (inspect its main() input handling — it reads the hook JSON on stdin), adapt the test input to the minimal event it accepts, keeping the assertion on "test rule".

- [ ] **Step 3: Implement**

In `hooks/scripts/_home.py` line 196:

```python
RESERVED_FILES = frozenset({"_MAP.md", "AGENTS.md", "GUILD.md", "workspace.json"})
```

In `hooks/scripts/bootstrap-load.py` stable list (after `workspace_agents_md(ws)`):

```python
        stable: list[Path] = [
            agents_md(),
            secrets_md(),
            shared_tools_md(),
            workspace_agents_md(ws),
            workspace_dir(ws) / "GUILD.md",
            docs_dir(ws) / "handoff.md",
        ]
```

(`workspace_dir` is already imported in bootstrap-load.py — verify; if not, add it to the existing `from _home import (...)` block.)

Create `templates/GUILD.md`:

```markdown
# GUILD.md (workspace playbook)

Rules promoted from recurring `[reflection]` evidence by the self-improver
(`/mem-evolve`). Loaded at every bootstrap — each rule costs tokens forever,
so: one bullet per rule, ≤2 lines, evidence-linked. Retire rules via
`/mem-evolve` with rationale, never silent deletion.

## Rules

<!-- format:
- <imperative rule, ≤2 lines> (promoted YYYY-MM-DD, evidence: N reflections, [[source-note]])
-->
```

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest tests.test_evolve -v && python3 -m unittest discover -s tests 2>&1 | tail -2`
Expected: new tests PASS and full suite still green (RESERVED_FILES change can affect topic-routing tests — if any fail, they are asserting the old frozenset contents; update those assertions).

- [ ] **Step 5: Commit**

```bash
git add templates/GUILD.md hooks/scripts/_home.py hooks/scripts/bootstrap-load.py tests/test_evolve.py
git commit -m "feat(evolve): GUILD.md workspace playbook — reserved name + bootstrap load + scaffold"
```

---

### Task 4: Stop-hook wiring + self-improve instruction template

**Files:**
- Create: `templates/self-improve-instructions.md`
- Modify: `hooks/scripts/auto-journal.py` (~line 123 settings helpers, ~line 140 reason builders, ~line 294-296 review cadence block)
- Test: `tests/test_evolve.py` (append)

**Interfaces:**
- Consumes: `_evolve.scan(ws)`, `_evolve._si_settings()`
- Produces: Stop-hook additionalContext gains `[gowth-mem:evolve ws=…]` pointer when candidates exist after a self-review fires.

- [ ] **Step 1: Write the failing test (append to tests/test_evolve.py)**

```python
class TestHookWiring(EvolveBase):
    def test_evolve_reason_builder(self):
        import importlib
        aj = importlib.import_module("auto-journal") if False else None
        # auto-journal.py has a dash — import via runpy-safe helper instead:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "auto_journal", SCRIPTS / "auto-journal.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        reason = mod._build_evolve_reason("default", 2, 1)
        self.assertIn("[gowth-mem:evolve ws=default]", reason)
        self.assertIn("2 recurring", reason)
        self.assertIn("self-improve-instructions.md", reason)
        self.assertIn("approval", reason.lower())
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest tests.test_evolve.TestHookWiring -v`
Expected: `AttributeError: … has no attribute '_build_evolve_reason'`
(If `exec_module` fails because auto-journal.py runs main() at import: it guards with `if __name__ == "__main__"` — verify; it does, since tests elsewhere import hook scripts. If not, exec via subprocess with a crafted stdin instead and assert on stdout.)

- [ ] **Step 3: Implement in `auto-journal.py`**

Beside `_read_reflection_settings()` (~line 123):

```python
def _build_evolve_reason(ws: str, n_clusters: int, n_signals: int) -> str:
    """Short pointer to the self-improve contract (full text stays on disk)."""
    instructions = Path(__file__).parent.parent.parent / "templates" / "self-improve-instructions.md"
    scanner = Path(__file__).parent / "_evolve.py"
    return (
        f"[gowth-mem:evolve ws={ws}] {n_clusters} recurring reflection cluster(s), "
        f"{n_signals} score-trend signal(s) qualify for rule promotion. "
        f"Run `python3 {scanner} --scan --ws {ws} --json` for the evidence, then follow "
        f"{instructions} EXACTLY: draft each rule (<=2 lines, counterfactual-gated), ask the "
        f"user for approval PER PROPOSAL before writing anything, then mark hashes processed/rejected."
    )
```

In the review-cadence block (~line 294-296), directly after `reasons.append(_build_review_reason(ws, review_count, session_id))`:

```python
        try:
            import _evolve
            if _evolve._si_settings()["enabled"]:
                res = _evolve.scan(ws)
                if res["clusters"] or res["score_signals"]:
                    reasons.append(_build_evolve_reason(
                        ws, len(res["clusters"]), len(res["score_signals"])))
        except Exception as exc:
            log_debug("auto-journal", f"evolve scan skipped: {exc}")
```

Create `templates/self-improve-instructions.md`:

```markdown
# Self-improve contract (v4.2)

You were pointed here by a `[gowth-mem:evolve]` hook line or `/mem-evolve`. The miner
found recurring failure patterns. Your job: turn each into ONE crisp rule the user
approves — or drop it. No approval, no write. Ever.

## 1. Get the evidence

    python3 <plugin>/hooks/scripts/_evolve.py --scan --ws <ws> --json

Each cluster: `cluster_key`, `keywords`, `count`, `members[].{text,source,hash}`.
Score signals: `{dimension, kind: stuck|declining, values}`.

## 2. Draft (per cluster)

- **Counterfactual gate first**: name the concrete failure in the members that this rule
  would have prevented. Can't name one → skip the cluster, mark processed, move on.
- Rule text: imperative, ≤2 lines, ENGLISH, self-contained (readable without the
  evidence). Synthesize the pattern — do NOT paste a member verbatim.
- For a score signal: draft ONE rule targeting that dimension's recurring weakness,
  citing 1-2 rows from `journal/_scores.md`.

## 3. Ask the user — one AskUserQuestion per proposal

Options: **Approve** / **Edit then approve** (user words win) / **Reject** (never ask again).
Show: rule text, evidence count, member sources. In the user's language; rule stays English.

## 4. Apply on approval — pick ONE target

| Target | When |
|---|---|
| `workspaces/<ws>/GUILD.md` under `## Rules` | DEFAULT. Format: `- <rule> (promoted YYYY-MM-DD, evidence: N reflections, [[source]])` |
| `workspaces/<ws>/AGENTS.md` (or `shared/AGENTS.md` if user says cross-workspace) | ONLY hard MUST/NEVER constraints — keep AGENTS slim |
| plugin `templates/self-review-instructions.md` | ONLY if cwd is the gowth-mem plugin repo: edit + run tests + commit like code |
| `[hypothesis]` entry routed to the gowth-mem topic | template-change idea while OUTSIDE the plugin repo (`Verify: apply in plugin repo`) |

If GUILD.md is missing, create it from `templates/GUILD.md` first.

## 5. Close the ledger (mandatory, prevents re-nagging)

Approved or skipped-at-gate:
    python3 …/_evolve.py --mark <member hashes> --status processed --cluster-key <key>
Rejected by user:
    python3 …/_evolve.py --mark --status rejected --cluster-key <key>

## 6. Reply

≤3 lines in the user's language: what was proposed, what they decided, where it landed.
```

- [ ] **Step 4: Run tests + full suite + compile**

Run: `python3 -m unittest tests.test_evolve -v && python3 -m py_compile hooks/scripts/*.py && python3 -m unittest discover -s tests 2>&1 | tail -2`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add templates/self-improve-instructions.md hooks/scripts/auto-journal.py tests/test_evolve.py
git commit -m "feat(evolve): Stop-hook wiring — scan after self-review, inject drafting contract pointer"
```

---

### Task 5: `/mem-evolve` command, backlog wrap-up, SessionStart nudge

**Files:**
- Create: `commands/mem-evolve.md`
- Modify: `commands/mem-review-backlog.md` (Wrap-up section)
- Modify: `hooks/scripts/bootstrap-load.py` (pending nudge, after stable-files loop)
- Test: `tests/test_evolve.py` (append)

**Interfaces:**
- Consumes: `_evolve.pending_count()`
- Produces: bootstrap output line `[gowth-mem:evolve] N pending rule proposal(s) — run /mem-evolve`

- [ ] **Step 1: Write the failing test (append)**

```python
class TestBootstrapNudge(EvolveBase):
    def test_pending_nudge_surfaces(self):
        import subprocess
        st = self.evolve.load_state()
        st["pending"] = [{"ws": "default", "cluster_key": "a+b", "count": 3}]
        self.evolve.save_state(st)
        shared = self.home / "shared"
        shared.mkdir(exist_ok=True)
        (shared / "AGENTS.md").write_text("# AGENTS\n", encoding="utf-8")
        out = subprocess.run(
            [sys.executable, str(SCRIPTS / "bootstrap-load.py")],
            capture_output=True, text=True, input="{}",
            env={**os.environ, "GOWTH_MEM_HOME": self.tmp.name})
        self.assertIn("pending rule proposal", out.stdout)

    def test_no_pending_no_nudge(self):
        import subprocess
        shared = self.home / "shared"
        shared.mkdir(exist_ok=True)
        (shared / "AGENTS.md").write_text("# AGENTS\n", encoding="utf-8")
        out = subprocess.run(
            [sys.executable, str(SCRIPTS / "bootstrap-load.py")],
            capture_output=True, text=True, input="{}",
            env={**os.environ, "GOWTH_MEM_HOME": self.tmp.name})
        self.assertNotIn("pending rule proposal", out.stdout)
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest tests.test_evolve.TestBootstrapNudge -v`
Expected: first test FAILS (no nudge line in output).

- [ ] **Step 3: Implement**

In `bootstrap-load.py`, after the stable/parts loading loop (before final `print`/output assembly — find where `parts` is joined):

```python
        try:
            import _evolve
            pend = _evolve.pending_count()
            if pend:
                parts.append(
                    f"[gowth-mem:evolve] {pend} pending rule proposal(s) "
                    f"from the self-improver — run /mem-evolve to review & approve")
        except Exception as exc:
            log_debug("bootstrap-load", f"evolve nudge skipped: {exc}")
```

Create `commands/mem-evolve.md` (frontmatter description — NO bare `: ` inside):

```markdown
---
description: Self-improver — mine recurring [reflection] patterns + score trends in the active workspace and promote them into GUILD.md/AGENTS.md rules with per-proposal user approval. Deterministic miner, LLM drafts, user decides. Also runs automatically after each self-review.
---

Run the self-improve pass for the active workspace (or `$ARGUMENTS` may name one, e.g. `/mem-evolve devops`; `--stats` shows the ledger).

## 1. Mine

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/_evolve.py" --scan --ws <ws> --json
```

Empty `clusters` + empty `score_signals` → reply "nothing recurring yet" with the `--stats` counts. Done.

## 2. Draft → approve → apply → mark

Follow `${CLAUDE_PLUGIN_ROOT}/templates/self-improve-instructions.md` EXACTLY —
counterfactual gate, ≤2-line English rule, one AskUserQuestion per proposal,
write only after approval, then `--mark` every member hash (processed) or the
cluster_key (rejected). The ledger close is mandatory — it is what stops re-nagging.

## 3. Wrap up

`--stats` again; reply ≤3 lines: proposed / decided / landed-where.
```

In `commands/mem-review-backlog.md`, add to the Wrap-up section:

```markdown
6. **Self-improve pass**: the batch just added reflections — run the `/mem-evolve`
   flow (mine → draft → per-proposal approval → mark). Skip silently if the miner
   returns no candidates.
```

- [ ] **Step 4: Run full verification**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -2 && python3 -m py_compile hooks/scripts/*.py && bin/test-install.sh 2>&1 | tail -5`
Expected: full suite green (402 + ~18 new), compile clean, test-install green.

- [ ] **Step 5: Commit**

```bash
git add commands/mem-evolve.md commands/mem-review-backlog.md hooks/scripts/bootstrap-load.py tests/test_evolve.py
git commit -m "feat(evolve): /mem-evolve command, backlog wrap-up step, SessionStart pending nudge"
```

---

### Task 6: Live smoke test on the real vault + docs

**Files:**
- Modify: `CLAUDE.md` (Shipped Features + counts), `README.md` (only if it has a feature list section touched by convention), `docs/SHIPPED-FEATURES.md`

**Interfaces:** none — verification + documentation.

- [ ] **Step 1: Live smoke — miner on the real vault (pre-tag rule)**

```bash
python3 hooks/scripts/_evolve.py --scan --ws default --json | python3 -m json.tool | head -60
python3 hooks/scripts/_evolve.py --stats
```

Expected: real clusters from the 15+ live reflections (deploy-target / act-before-verify patterns visible in the earlier grep), no traceback, stats JSON sane. **Do not mark anything** — leave the live ledger untouched for the user's first real `/mem-evolve` run.

- [ ] **Step 2: Live smoke — hook path end-to-end**

```bash
echo '{"session_id":"smoke-test","hook_event_name":"Stop"}' | python3 hooks/scripts/auto-journal.py; echo "exit=$?"
```

Expected: exit=0, no traceback (evolve block only appears when review cadence fires — the point is the hook survives with the new code in place).

- [ ] **Step 3: Update docs**

- `CLAUDE.md`: commands count 39 → 40; tests count 402 → new total; add one Shipped Features entry:

```markdown
- **v4.2 (self-improver)**: closes the review→rule loop — `_evolve.py` deterministic miner (FS-scan `[reflection]` entries, ≥2-keyword-overlap union-find clustering, `_scores.md` stuck/declining trend signals, machine-local `evolve-state.json` ledger with permanent rejection memory); Stop-hook scan after each self-review + SessionStart pending nudge + `/mem-evolve` manual + `/mem-review-backlog` wrap-up step; per-workspace `GUILD.md` playbook (reserved name, bootstrap-loaded, ≤2-line evidence-linked rules); mandatory per-proposal user approval via `templates/self-improve-instructions.md` (counterfactual gate, target matrix GUILD/AGENTS/template-in-repo). Spec: `docs/superpowers/specs/2026-07-27-self-improver-design.md`.
```

- `docs/SHIPPED-FEATURES.md`: append the same entry in that file's format.

- [ ] **Step 4: Final full verification**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -2 && python3 -m py_compile hooks/scripts/*.py`
Expected: green; note the final test count for the docs.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md docs/SHIPPED-FEATURES.md README.md
git commit -m "feat(v4.2): self-improver — docs + shipped-features entry"
```

(Release via `bin/release.sh minor` is the user's call — pre-tag smoke already done in Steps 1-2.)

---

## Self-Review Notes

- Spec coverage: targets (T3/T4 template matrix), trigger (T4 hook + T5 command/backlog/nudge), threshold (T2 settings), approval (T4 instructions), anti-junk (gate in instructions + junk floor in collect + rejection ledger), error handling (every integration wrapped, CLI returns 0), FS-scan deviation declared in Global Constraints.
- Type consistency: `scan()` → `{"ws","clusters","score_signals"}` used identically in T4 wiring and T5 command; `mark(hashes, status, cluster_key=, keywords=)` matches instruction template CLI usage; state shape identical across T1 tests / T2 impl / T5 nudge.
- Known risk called out in-plan: T2 Step 4 fixture/keyword sensitivity; T3/T4 import-preconditions each carry a fallback instruction.
