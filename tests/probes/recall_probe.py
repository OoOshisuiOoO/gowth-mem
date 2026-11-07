#!/usr/bin/env python3
"""Recall probe + per-prompt gate calibration on a COPY of a real vault.

Not a unit test (no test_ prefix). Usage:
    GOWTH_MEM_HOME=<vault-copy> python3 tests/probes/recall_probe.py [--seed2 43] [--n2 20]

Samples [decision]/[ref] entries from topic trees (seed 42 → 10, like the
v4.8 audit; seed 43 → 20 more), paraphrases each title into 3-6 content
words, and reports:
  * /mem-recall ranking: hit@1, hit@3, MRR@20 (source file = hit);
  * the per-prompt gate (`_recall_prompt.select`) at a sweep of bm25
    thresholds: precision of injected entries (same file or same topic
    folder as the source), injections on 22 generic prompts;
  * the recommended `recall.on_prompt_score_threshold`: the least negative
    value with 0 generic injections and precision >= 0.8.
  * v4.9 card sweep (`--cards-only` skips the rest): for every carded topic of
    every workspace, naming prompts (a long one per name, a short 8-39-byte
    one per name) → hit / miss / wrong-topic counts from `select_card`, and
    whether the short prompt passes the bash `grep -wiF` gate (emulated on the
    names `refresh_card_terms` would write); card injections on the 22
    generic prompts, the 20 realistic generic ones
    (tests/fixtures/recall_prompts.json) and the paraphrased sample above.
Never writes the vault (cards are set beforehand with `_card.py --set`).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "hooks" / "scripts"))

import _home  # type: ignore  # noqa: E402
import _profile  # type: ignore  # noqa: E402
import _query  # type: ignore  # noqa: E402
import _recall_prompt  # type: ignore  # noqa: E402

GENERIC = ["continue", "ok làm đi", "fix the tests", "tiếp đi", "release", "commit and push",
           "what's next?", "run it again", "yes", "no, revert that", "explain", "làm lại",
           "check again", "why?", "thanks", "show me the diff", "format the file",
           "add a comment", "rename it", "ok",
           "please continue with the next step of the plan and keep going until done",
           "can you run the whole test suite one more time and show me the summary"]
ENTRY_RE = re.compile(r"^(?:- |## )\[(decision|ref)\] (.+?)\s*$")
RESERVED = _home.RESERVED_SUBDIRS


def population(gh: Path) -> list:
    pop = []
    root = gh / "workspaces"
    for ws in _home.list_workspaces():
        wsd = root / ws
        for p in wsd.rglob("*.md"):
            rel = p.relative_to(wsd)
            if not rel.parts or rel.parts[0] in RESERVED or rel.parts[0].startswith(".") or p.name == "00-README.md":
                continue
            try:
                lines = p.read_text(errors="ignore").splitlines()
            except OSError:
                continue
            for i, ln in enumerate(lines):
                m = ENTRY_RE.match(ln)
                if m and len(m.group(2)) > 12:
                    pop.append({"ws": ws, "path": str(p.relative_to(gh)), "line": i + 1,
                                "tag": m.group(1), "title": m.group(2)})
    return pop


def paraphrase(title: str) -> str:
    words = [w for w in _profile.keywords_of(title) if len(w) > 2]
    if len(words) < 3:
        words = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9_-]+", title)][:6]
    return " ".join(words[:6])


def rank_of(hits: list, path: str) -> int:
    for i, h in enumerate(hits):
        if h.get("path") == path:
            return i + 1
    return 0


def _grep_wi(term: str, text: str) -> bool:
    """`grep -wiF` on one line: `term` occurs case-insensitively with no word
    character ([A-Za-z0-9_], or any alphanumeric) on either side."""
    t, low = term.lower(), text.lower()
    i = low.find(t)
    while i >= 0:
        a = low[i - 1] if i > 0 else ""
        b = low[i + len(t)] if i + len(t) < len(low) else ""
        if not (a and (a.isalnum() or a == "_")) and not (b and (b.isalnum() or b == "_")):
            return True
        i = low.find(t, i + 1)
    return False


def card_sweep(sample: list, settings: dict) -> dict:
    """Hit/miss counts of subject-keyed card injection (spec §6.1)."""
    fixtures = json.loads((ROOT / "tests" / "fixtures" / "recall_prompts.json").read_text(encoding="utf-8"))
    generic = list(fixtures["generic"]) + list(fixtures["realistic"])
    out: dict = {"workspaces": {}}
    tot = {"carded": 0, "names": 0, "long_hit": 0, "long_miss": 0, "long_wrong": 0,
           "short_hit": 0, "short_miss": 0, "short_gate_blocked": 0, "generic_cards": 0,
           "generic_prompts": 0, "sample_cards": 0, "sample_prompts": 0}
    for ws in _home.list_workspaces():
        cands = _recall_prompt.card_candidates(ws, settings)
        if not cands:
            continue
        terms = _recall_prompt.card_terms_text(cands).splitlines()
        row = {"carded": len(cands), "names": sum(len(c["names"]) for c in cands),
               "long_hit": 0, "long_miss": 0, "long_wrong": 0, "short_hit": 0, "short_miss": 0,
               "short_gate_blocked": 0, "generic_cards": 0, "misses": []}
        for cand in cands:
            for name in cand["names"]:
                for kind, prompt in (("long", f"what is the current state of {name}, and what should I check first?"),
                                     ("short", f"{name} status?")):
                    if kind == "short" and not 8 <= len(prompt.encode()) < 40:
                        continue
                    if kind == "short" and not any(_grep_wi(t, prompt) for t in terms):
                        row["short_gate_blocked"] += 1
                    got = _recall_prompt.select_card(prompt, ws, settings, set(), cands)
                    if got is None:
                        row[f"{kind}_miss"] += 1
                        row["misses"].append(f"{kind}:{cand['rel']}:{name}")
                    elif got["rel"] == cand["rel"]:
                        row[f"{kind}_hit"] += 1
                    else:
                        row["long_wrong" if kind == "long" else f"{kind}_miss"] += 1
                        row["misses"].append(f"{kind}:{cand['rel']}:{name}->{got['rel']}")
        for g in generic:
            if _recall_prompt.select_card(g, ws, settings, set(), cands) is not None:
                row["generic_cards"] += 1
        tot["generic_prompts"] += len(generic)
        for e in sample:
            if e["ws"] != ws:
                continue
            tot["sample_prompts"] += 1
            q = f"what did we decide or learn about {paraphrase(e['title'])} last time, and why?"
            if _recall_prompt.select_card(q, ws, settings, set(), cands) is not None:
                tot["sample_cards"] += 1
        for k in ("carded", "names", "long_hit", "long_miss", "long_wrong", "short_hit", "short_miss",
                  "short_gate_blocked", "generic_cards"):
            tot[k] += row[k]
        out["workspaces"][ws] = row
    out["total"] = tot
    return out


def print_card_sweep(res: dict) -> None:
    tot = res["total"]
    print("\ncard sweep (v4.9 subject-keyed cards):")
    if not tot["carded"]:
        print("  no carded topics in any workspace — set some with _card.py --set first")
        return
    for ws, row in res["workspaces"].items():
        print(f"  {ws:<10} carded {row['carded']:>3}  names {row['names']:>3}  long hit {row['long_hit']:>3} "
              f"miss {row['long_miss']:>2} wrong {row['long_wrong']:>2}  short hit {row['short_hit']:>3} "
              f"miss {row['short_miss']:>2} gate-blocked {row['short_gate_blocked']:>2}  "
              f"generic cards {row['generic_cards']}")
        for m in row["misses"][:5]:
            print(f"      miss {m}")
    print(f"  total: carded {tot['carded']}, names {tot['names']}; long {tot['long_hit']} hit / "
          f"{tot['long_miss']} miss / {tot['long_wrong']} wrong; short {tot['short_hit']} hit / "
          f"{tot['short_miss']} miss, {tot['short_gate_blocked']} blocked by the bash gate; generic prompts "
          f"with a card {tot['generic_cards']} of {tot['generic_prompts']}; paraphrased sample with a card "
          f"{tot['sample_cards']} of {tot['sample_prompts']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed2", type=int, default=43)
    ap.add_argument("--n2", type=int, default=20)
    ap.add_argument("--json", default="")
    ap.add_argument("--cards-only", action="store_true", help="only the v4.9 card sweep")
    args = ap.parse_args()
    gh = _home.gowth_home()
    pop = population(gh)
    random.seed(42)
    s1 = random.sample(pop, min(10, len(pop)))
    rest = [e for e in pop if e not in s1]
    random.seed(args.seed2)
    s2 = random.sample(rest, min(args.n2, len(rest)))
    sample = s1 + s2
    settings = _home.read_settings()
    if args.cards_only:
        res = card_sweep(sample, settings)
        print_card_sweep(res)
        if args.json:
            Path(args.json).write_text(json.dumps({"cards": res}, indent=1, ensure_ascii=False))
        return 0

    # --- ranking ---
    ranks = []
    rows = []
    for e in sample:
        q = paraphrase(e["title"])
        res = _query.query_ex(e["ws"], "", q, limit=20)
        r = rank_of(res.get("hits") or [], e["path"])
        ranks.append(r)
        top = (res.get("hits") or [{}])[0].get("path", "") if res.get("hits") else ""
        rows.append({"ws": e["ws"], "path": e["path"], "query": q, "rank": r, "rank1": top,
                     "score": (res.get("hits") or [{}])[0].get("bm25_score") if res.get("hits") else None})
    n = len(ranks)
    hit1 = sum(1 for r in ranks if r == 1)
    hit3 = sum(1 for r in ranks if 1 <= r <= 3)
    mrr = sum(1.0 / r for r in ranks if r) / n if n else 0.0
    print(f"population: {len(pop)} entries; sample: {n} (10 @seed42 + {len(s2)} @seed{args.seed2})")
    print(f"/mem-recall: hit@1 {hit1}/{n}  hit@3 {hit3}/{n}  MRR@20 {mrr:.3f}")
    for row in rows[:10]:
        print(f"  rank {row['rank']:>2}  {row['query'][:48]:<48}  {row['path'][-60:]}")

    # --- gate sweep ---
    def prompt_for(q: str) -> str:
        return f"what did we decide or learn about {q} last time, and why?"

    def topic_of(path: str) -> str:
        parts = path.split("/")
        return "/".join(parts[:3]) if len(parts) >= 3 else path

    print("\nper-prompt gate sweep (recall.on_prompt_score_threshold):")
    print(f"{'thr':>6} {'inject%':>8} {'prec':>6} {'generic':>8}")
    best = None
    sweep = [0.0, -1.0, -2.0, -3.0, -4.0, -5.0, -6.0, -8.0, -10.0, -12.0, -15.0]
    results = {}
    for thr in sweep:
        s = json.loads(json.dumps(settings))
        s.setdefault("recall", {})["on_prompt_score_threshold"] = thr
        injected = 0
        relevant = 0
        prompts_with = 0
        for e in sample:
            hits = _recall_prompt.select(prompt_for(paraphrase(e["title"])), e["ws"], s, set())
            if hits:
                prompts_with += 1
            for h in hits:
                injected += 1
                if h.get("path") == e["path"] or topic_of(h.get("path", "")) == topic_of(e["path"]):
                    relevant += 1
        generic = 0
        for g in GENERIC:
            for e in sample[:3]:
                generic += len(_recall_prompt.select(g, e["ws"], s, set()))
        prec = (relevant / injected) if injected else 0.0
        results[thr] = {"injected": injected, "precision": prec, "generic": generic,
                        "prompts_with_memory": prompts_with}
        print(f"{thr:>6} {100.0 * prompts_with / n:>7.0f}% {prec:>6.2f} {generic:>8}")
        if generic == 0 and prec >= 0.8 and injected > 0 and best is None:
            best = thr
    print(f"\nrecommended on_prompt_score_threshold: {best if best is not None else 'none met the bar'}")
    cards = card_sweep(sample, settings)
    print_card_sweep(cards)
    if args.json:
        Path(args.json).write_text(json.dumps({"n": n, "hit1": hit1, "hit3": hit3, "mrr": mrr,
                                               "sweep": results, "recommended": best, "rows": rows,
                                               "cards": cards},
                                              indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
