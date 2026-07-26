# Self-Improver (v4.2) — Design Spec

Date: 2026-07-27 · Status: approved by user · Workspace scope: per-workspace

## Problem

The v4.0/v4.1 review loop produces `[reflection]` entries (live 15-turn reviews +
`/mem-review-backlog`) and score trends in `journal/_scores.md`, but nothing closes the
loop: a reflection repeated 3× stays 3 lines in lessons — it never becomes a rule that
future sessions must follow. Score trends are tracked but never acted on. The user must
manually notice recurring patterns and promote them.

## Goal

Automatically re-read accumulated reflections + score trends in the active workspace,
detect recurring patterns, and **propose** rule promotions — with mandatory user approval
before any write ("trước khi thay đổi thì hỏi lại tao").

## Decisions (user-confirmed)

| Question | Decision |
|---|---|
| Evolution targets | (1) `workspaces/<ws>/GUILD.md` (new, default), (2) `AGENTS.md` (ws/shared — hard MUST/NEVER only), (3) `templates/self-review-instructions.md` (meta, plugin-repo-cwd only) |
| GUILD.md | Per-workspace playbook of promoted rules (rule + promote date + `[[evidence]]` links), loaded at bootstrap. AGENTS.md stays slim. |
| Trigger | Auto after every self-review event (live cadence AND backlog batch) + ask immediately in-chat; unhandled proposals re-surfaced at SessionStart; manual `/mem-evolve` anytime |
| Threshold | Cluster ≥3 reflections (keyword overlap) OR score-trend signal (a dimension stuck ≤2, or monotonically declining, across 3 consecutive review blocks) |
| Approval | Per-proposal AskUserQuestion: Approve / Edit-then-approve / Reject (rejection remembered permanently — never re-asked) |
| Architecture | **Approach A**: deterministic stdlib miner + in-session LLM drafter via hook-injected instruction block (same pattern as auto-journal/self-review). Optional fresh-context subagent drafting when main context is heavy. |

## Architecture

```
[reflection] entries (both review paths already route to vault)
        │
_evolve.py --scan            Stop hook, stdlib, deterministic
  ├─ collect UNPROCESSED reflections (FTS5 query_by_type('reflection') + grep fallback)
  ├─ cluster by keyword-set Jaccard (reuse _tags extraction; rem_phase-style grouping)
  ├─ parse _scores.md → trend signals (stuck ≤2 / declining, 3 blocks)
  └─ emit candidates JSON when cluster ≥ min_cluster OR trend signal fires
        │
Hook injects short pointer to templates/self-improve-instructions.md (~400 chars)
        │
In-session Claude: draft rule (≤2 lines, evidence links, counterfactual gate)
  → AskUserQuestion per proposal → on approve, write to target
        │
        ├─ workspaces/<ws>/GUILD.md          default (bootstrap-loaded playbook)
        ├─ AGENTS.md (ws or shared)          only hard MUST/NEVER constraints
        └─ plugin template                    only when cwd == plugin repo (edit+test+commit);
                                              otherwise queued as [hypothesis] in the
                                              gowth-mem topic (existing verify machinery)
        │
        └─ _evolve.py --mark <ids> → evolve-state.json (vault root, gitignored,
           like state.json); auto-sync commits GUILD.md normally
```

## Components

### New

| File | Role |
|---|---|
| `hooks/scripts/_evolve.py` | Miner + state ledger. CLI: `--scan [--ws X] [--json]`, `--mark <hash>… [--status processed\|rejected]`, `--stats` |
| `templates/GUILD.md` | Scaffold: header + `## Rules` (one bullet per rule: text, promote date, `[[evidence]]`) |
| `templates/self-improve-instructions.md` | Drafting rules, approval flow, target selection, counterfactual gate |
| `commands/mem-evolve.md` | Manual run; also the wrap-up step of `/mem-review-backlog` |
| `tests/test_evolve.py` | Clustering, thresholds, trend parsing, state round-trip, graceful degradation |

### Modified

- `auto-journal.py` — after injecting a self-review block, also run `_evolve.py --scan`;
  if candidates exist, append the evolve pointer block. Gated by `settings.self_improve.enabled`.
- `session-start.sh` — load `GUILD.md` in workspace bootstrap file list; if evolve-state
  has pending (proposed-but-unhandled) candidates, print a one-line nudge.
- `commands/mem-review-backlog.md` — add final step: run the `/mem-evolve` flow.
- `settings.json` schema — `self_improve: { enabled: true, min_cluster: 3, score_stuck_blocks: 3 }`.

## Data & state

- **evolve-state.json** (vault root, gitignored): `{ processed: {hash: date}, rejected: {cluster_key: {date, keywords}}, pending: [candidate…] }`.
  Hashes reuse `_dedup`-style normalization of reflection text. `cluster_key` = sorted top
  keywords joined — stable across re-scans.
- **GUILD.md** is NOT a topic file and NOT indexed into the 9-type schema — it is
  bootstrap-loaded documentation like AGENTS.md. No schema change. Topic router must
  never route into it (add to reserved names).
- Applied promotions are self-documenting inside GUILD.md (synced); rejected/processed
  stay machine-local (re-proposing on another machine is rare and harmless).

## Anti-junk safeguards (v3.6 lessons)

1. Counterfactual gate on every draft: the rule must name the concrete failure in its
   evidence cluster that it would have prevented — else no proposal.
2. Rejected cluster-keys are never re-proposed.
3. Promoted reflections are marked processed — never double-counted in later clusters.
4. Rule text ≤2 lines; GUILD.md is curated playbook, not a dump.

## Error handling

Hook invariants preserved: **always exit 0, no traceback, silent when nothing to do.**
Missing `_scores.md` / no reflections / no FTS5 → grep fallback → silent no-op.
Corrupt evolve-state.json → start fresh (log via `_debug`). Missing vault → exit 0.

## Testing

- Unit: 3 same-pattern reflections cluster, 2 do not; threshold respects settings;
  trend parser on stuck / declining / healthy / short tables; processed & rejected
  skipped on re-scan; cluster_key stability; CLI JSON shape; missing-files graceful.
- Integration: `bin/test-install.sh` still green (hook path touched).
- Live smoke (pre-tag rule): run `--scan` against the real vault (default ws has 15+
  reflections with visible repeats — act-before-verify, guess-instead-of-ask) and walk
  one real proposal end-to-end before any release.

## Out of scope (YAGNI)

- No new entry type (`[rule]`) — 9-type schema untouched.
- No auto-apply without approval, ever.
- No cross-workspace mining in v1 (per-workspace only; shared/AGENTS.md promotion happens
  when the user picks that target during approval).
- No LLM in the hook path.
