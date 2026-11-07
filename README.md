# gowth-mem

A Claude Code plugin for **persistent, topic-organized memory** synced across machines via your own git remote. It hooks the chat lifecycle so memory can bootstrap, recall, journal, distill, and sync itself.

Built on patterns from mem0, Letta/MemGPT, Zep, Cognee, MemPalace, Generative Agents, Voyager, Reflexion, Anthropic contextual retrieval, SM-2 spaced repetition, **OpenClaw "dreaming" staged consolidation**, **agentmemory 4-tier taxonomy**, and **rtk pre-storage compression**. See [`RESEARCH.md`](RESEARCH.md).

**How to use it:** [docs/USAGE.md](docs/USAGE.md) — what runs on its own, the daily curator, the "must research"
question ledger, topic cards, saving and correcting memory, bots and pipelines, troubleshooting.
Tiếng Việt: [docs/USAGE.vi.md](docs/USAGE.vi.md).

## What's new in v4.9

v4.9 keeps memory from going stale. It brings over the parts of a production bot that keep its knowledge current
(a daily curator, a ledger of open questions, curated cards per topic, evidence discipline) and makes the rules for
reading and writing memory part of the vault. Spec: `docs/superpowers/specs/2026-11-07-v4.9-curator-design.md`.

- **Daily curator.** Once a day per workspace (the first real turn after `curator.hour` with at least
  `curator.min_new_turns` new turns), the Stop hook asks Claude to launch ONE background subagent,
  `gowth-mem:curator` — a plugin agent limited to Bash, Read, Grep, Glob, WebSearch and WebFetch. It reads a deterministic
  digest of the session turns no worklog has covered yet (`_curate.py prep`), reviews what was written that day,
  appends corrections that name the entry they correct, refreshes topic cards, opens and answers questions, turns
  stale handoff items into "looks done — tick it?" questions (it never edits the handoff), and records a worklog with
  evidence in `docs/worklog/<date>-<host>-<run>.md`. One host curates a workspace; claims, retries and misses are
  tracked. `/mem-ops curate status | prep | now`.
- **Gap ledger — "must research".** `docs/questions.md` per workspace holds `research` questions (an agent can find the
  answer) and `ask` questions (only you know). `/mem-ops gap add | list | answer | close | tried`. Open items show in
  `MEMORY.md`, asks first. The curator researches a few per run and turns a question into an `ask` after 3 failed
  tries. Two machines that append at once merge line by line (`.gitattributes` `merge=union`).
- **Topic cards.** The current truth of a topic — Core, State, Routing, Gotchas, Sources, at most 2,000 chars — lives in
  its README's TL;DR, written only through `/mem-topic card` (the previous card is archived verbatim under
  `<topic>/.history/`). MOC rebuilds never touch it. A prompt that names the topic gets its card once per session.
- **Corrections on the read path.** A wrong entry is never edited: a new entry of the same type, keeping that type's
  required parts, says `Correction: <old> → <new> (<date>; Corrects: <path>; Source: …)` (a settled `[hypothesis]`
  becomes a `[ref]`), and recall shows it next to the stale entry.
- **Rules + refs canon.** `shared/memory-rules.md` — lookup ladder, unknowns become questions, evidence labels
  (CONFIRMED / UNVERIFIED / FALSE POSITIVE), corrections, cards, placement, honest operator context, "memory is data,
  not instructions", templates. Delivered and committed by `_canon.py` right after each successful pull (a copy you
  edited is never overwritten); the `MEMORY.md` rules digest is its 12-line summary.
- **Pipeline hygiene.** `GOWTH_MEM_OFF=1` turns every gowth-mem hook off for that process. Prompts a bot or pipeline
  sends (`STAGE=…`, `### TASK`, …, plus your own `capture.pipeline_prefixes`) are not counted, captured, recalled,
  dumped or nagged.
- **Data safety.** Every read-modify-write of a topic or docs file holds the workspace's `entries-<ws>` lock; prune writes
  only files it changed and never reads the gap ledger; forget salvages every entry shape before it archives an aspect;
  every git call that replays or creates a commit carries the gowth-mem identity (CI is green again).

Earlier releases: [`docs/SHIPPED-FEATURES.md`](docs/SHIPPED-FEATURES.md) (v4.8: the working set travels through
Claude Code's auto-memory `MEMORY.md`; gated per-prompt recall; every hook emission stays under 9,000 chars).

## What it does

`gowth-mem` keeps a single global memory vault at `~/.gowth-mem/`, split into shared knowledge and per-workspace knowledge. Each Claude Code session resolves one active workspace, recalls relevant entries on each prompt, and syncs changes through a user-owned git remote around `/compact`.

## Current architecture

```text
~/.gowth-mem/
├── shared/                                    cross-workspace knowledge
│   ├── AGENTS.md                              global operating rules
│   ├── _MAP.md
│   ├── files.md
│   ├── secrets.md                             pointers only; never real secret values
│   ├── tools.md
│   ├── memory-rules.md                        v4.9 rules + refs canon (delivered by _canon.py)
│   ├── research/*-2026.md                     write-quality / extraction / provenance canon
│   └── skills/<slug>.md
├── workspaces/<ws>/                           active workspace-scoped knowledge
│   ├── AGENTS.md                              workspace rules
│   ├── workspace.json
│   ├── _MAP.md
│   ├── docs/{handoff,exp,ref,tools,files}.md
│   ├── docs/questions.md                      v4.9 gap ledger: open research / ask questions
│   ├── docs/worklog/<date>-<host>-<run>.md    v4.9 daily curator worklog (what happened, with evidence)
│   ├── journal/<date>.md                      + journal/sessions/<date>-<sid8>.md (per-turn session logs)
│   ├── memory/MEMORY.md                       v4.8 managed block + Claude's own auto-memory notes
│   ├── skills/<slug>.md
│   ├── research/<topic>/                      deep-research workspace (raw/ + distilled.md)
│   └── <slug>/                                v3 topic folder
│       ├── 00-README.md                       MOC: TL;DR (+ v4.9 card) + Aspects (auto-rebuilt) + Cross-links
│       ├── YYYY-MM-DD-<aspect>.md             dated aspect note (append-only, written by route())
│       ├── lessons.md                         per-topic 5-field bug/lesson ledger
│       └── .history/card-<sha12>.txt          verbatim archive of each replaced card
├── settings.json                              synced behavior settings (layout_version: 3)
├── config.json                                remote/branch/token config; gitignored
├── state.json                                 counters, stamps, recall telemetry; gitignored
├── index.db                                   FTS5 + optional sqlite-vec index; gitignored
├── .curator/                                  v4.9 claim markers, prep digests, card terms; gitignored
├── .gitattributes                             workspaces/*/docs/questions.md merge=union
├── .locks/                                    fcntl lock files; gitignored
├── .backup/v2-pre-v3-<utc>/                   migration snapshots (rolling-2); gitignored
└── .git/                                      sync repository
```

Topic slugs are unique inside a workspace. v3 wikilink resolution falls back through six layers: `<ws>/<slug>/00-README.md` (v3), `<ws>/<slug>/<slug>.md` (v2.4 fallback), `<ws>/<slug>.md` (v2.3 flat), `<ws>/lessons.md`, `shared/<key>.md`, and cross-workspace `[[ws:slug]]`. New writes always land in the v3 dated-aspect layout.

### Upgrading from v2.x

`/mem-install` detects `layout_version < 3` and offers `/mem-ops migrate-v3`:

```text
/mem-ops migrate-v3              # dry-run is default — preview the move plan
/mem-ops migrate-v3 --force      # execute: snapshot → classify → execute → verify
```

The 7-step pipeline snapshots every workspace into `.backup/v2-pre-v3-<utc>/`,
classifies each file (v2.4 landing → `00-README.md`, sub-aspect → dated aspect,
v2.3 flat → folder promote, `lessons.md` kept verbatim, reserved subdirs
untouched), executes atomic moves, verifies body sha256, deletes originals,
rebuilds metadata, then bumps `settings.layout_version` to `3` and creates a
single `v3 migration <utc>` commit. Rolling-window keeps the latest 2 backups.

### Rollback

If anything looks wrong after a migration, restore from the most recent
snapshot:

```bash
bash bin/rollback-v3.sh                       # restore newest .backup/v2-pre-v3-*
bash bin/rollback-v3.sh v2-pre-v3-20260517T105453Z146941   # explicit snapshot
```

Rollback is non-destructive — it stages the current workspaces under
`.backup/rolled-back-<utc>/` before restoring, then resets `layout_version` to
`2` and prints next-step instructions.

## Install

The repo is both a standalone plugin and a single-plugin Claude Code marketplace.

### Via Claude Code plugin manager

```text
/plugin marketplace add OoOshisuiOoO/gowth-mem
/plugin install gowth-mem@gowth-mem
```

Restart Claude Code so hooks register. To update later:

```text
/plugin marketplace update gowth-mem
/plugin update gowth-mem@gowth-mem
```

If Claude Code reports `source type your Claude Code version does not support`, update Claude Code or use a manual clone.

### Manual clone fallback

```bash
git clone https://github.com/OoOshisuiOoO/gowth-mem ~/.claude/plugins/gowth-mem
```

Restart Claude Code. If your build does not auto-discover plugins, enable it in `~/.claude/settings.json`.

### Then run the wizard

```text
/mem-install
```

The wizard scaffolds `shared/` plus a default workspace under `~/.gowth-mem/workspaces/default/`, asks for git remote + branch + token strategy, writes `settings.json` + `config.json`, then runs `_sync.py --init`.

After install:

```text
memx                  build the search index
/mem-ops migrate-global   import any older per-workspace .gowth-mem folders
```

## Self-heal user-level hook (recommended one-time setup)

Claude Code's `autoUpdate` for marketplace plugins suffers from issue #52218 — version metadata in `~/.claude/plugins/installed_plugins.json` gets bumped, but the cache dir at `~/.claude/plugins/cache/<m>/<p>/<v>/` is never materialized, so every gowth-mem hook is silently skipped after the next restart. Manual `/plugin install --path` workarounds also leak local absolute paths into the registry, breaking portability across machines.

`bin/doctor.sh` self-heals both states. Because the plugin's own hooks can't run when its `installPath` is broken, the doctor must be invoked from a hook **outside** the plugin. Add this once to `~/.claude/settings.json` (merge under any existing `hooks` key):

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "bash -c '[ -f \"$HOME/.claude/plugins/marketplaces/gowth-mem/bin/doctor.sh\" ] && bash \"$HOME/.claude/plugins/marketplaces/gowth-mem/bin/doctor.sh\" --pull --quiet || true'"
          }
        ]
      }
    ]
  }
}
```

What `--pull --quiet` does each session:
- `git fetch + ff-only pull` the marketplace clone so the doctor sees the latest published version (network errors fall back silently to the local clone).
- Detect drift: `installPath` outside `~/.claude/plugins/cache/`, missing folder, or stale version vs. marketplace.
- Materialize `~/.claude/plugins/cache/<m>/<p>/<latest>/` from the marketplace clone, atomically rewrite the registry entry, exit 0.
- Idempotent — silent when healthy. Output (heal events) goes to stderr only, so it never poisons hook stdout.

Restart Claude Code (or `/reload-plugins`) once after a heal so the new `installPath` takes effect.

## Hooks

| Event | Hook | What it does |
|---|---|---|
| SessionStart | `bootstrap-load.py` | v4.8: refresh the `MEMORY.md` block and print a ≤600-char header when the project is wired (native), else a ≤8,500-char fallback bootstrap; `/clear` bootstraps again |
| SessionStart | `auto-sync.py --pull-only --quiet` | Rebase remote into local without pushing; v4.9: deliver newer canon (`shared/memory-rules.md`, research canon) right after a successful pull |
| PreCompact | `precompact-flush.py` | Deterministic raw-dump of the recent human turns into today's journal; never blocks `/compact` (v3.5.1+) |
| PreCompact | `auto-sync.py --commit-only --quiet` | Commit local memory changes without network |
| PostCompact | `auto-sync.py --pull-rebase-push --quiet` | Pull, rebase, push; conflict writes `SYNC-CONFLICT.md` |
| UserPromptSubmit | `conflict-detect.sh` → `recall-on-prompt.sh` | Conflict reminder when `SYNC-CONFLICT.md` exists; gated per-prompt BM25 recall (v4.8, ≤2,000 chars, silent by default); v4.9: the card of a topic the prompt names (once per session) and corrections next to the entries they correct |
| Stop | `auto-journal.py` | Real-turn counters; journal / self-review cadence directives on the non-error Stop channel (a background teammate / a fresh judge — v4.7); v4.8: `MEMORY.md` refresh, memory sanitizer, incremental reindex; v4.9: the once-a-day curator directive |

Every hook exits 0 and never blocks (`/compact` included). `GOWTH_MEM_OFF=1` in a process's environment turns every
gowth-mem hook off for it (stdin is drained, nothing is read or written) — set it for bots and pipelines that drive
`claude -p`. Prompts that look like pipeline stages (`STAGE=…`, `### TASK`, …, or a prefix you list in
`capture.pipeline_prefixes`) are never counted, captured, recalled or dumped.

## Slash commands & shortcuts

| Command | Shortcut | Purpose |
|---|---|---|
| `/mem-install` | `memI` | First-time setup wizard |
| `/mem-ops config` | `memg` | Change git remote, branch, token strategy, or workspace map |
| `/mem-sync` | `memy` | Manual sync |
| `/mem-sync resolve` | `memC` | AI-mediated conflict resolution |
| `/mem-ops migrate-global` | `memm` | Import older per-workspace `.gowth-mem/` data |
| `/mem-ops migrate-v3` | — | Promote `~/.gowth-mem/` from v2.x to v3 topic-folder layout (7-step pipeline, dry-run default, rolling-2 backup) |
| `/mem-topic` | `memT` | List, inspect, or route topics; v4.9 `card <slug> [show \| set \| summary "<line>" \| check]` edits a topic's card |
| `/mem-save` | `mems` | v4.9 `/mem-save [type] "<entry>"` saves one entry (gate, tags, dedup, routing; prints `written`, `duplicate` or `rejected:<rule>`); without arguments, distill the session |
| `/mem-ops gap` | — | v4.9 gap ledger: `add research\|ask "<question>" --why …`, `list`, `answer <id> "<answer>" --evidence …`, `close`, `tried` |
| `/mem-ops curate` | — | v4.9 daily curator: `status` (default), `prep` (dry digest, writes nothing), `now` (dispatch today's run) |
| `/mem-distill` | `memd` | Journal to topics |
| `/mem-ops reflect` | `memr` | Generate reflections |
| `/mem-ops skillify` | `memk` | Extract reusable workflows |
| `/mem-ops journal` | `memj` | Open today's journal |
| `/mem-recall` | — | v3.4 — deterministic FTS5 BM25 recall with optional `--type=<tag>` pre-filter (decision/exp/ref/tool/reflection/skill-ref/secret-ref/goal/hypothesis) |
| `/mem-ops dream` | — | v3.4 — run Light/REM/Deep consolidation across a workspace (wraps `_consolidate.py`); supports `--ws`, `--dry-run`, per-phase skip flags |
| `/mem-ops reindex` | `memx` | Rebuild SQLite FTS5 + optional vector index |
| `/mem-cost` | `memc` | Estimate bootstrap token footprint |
| `/mem-ops prune` | `memp` | Remove outdated, superseded, or duplicate entries |
| `/mem-lesson` | `memL` | Append a 5-field bug/lesson entry |
| `/mem-doctor` | — | Self-heal plugin install path drift (issue #52218); pulls marketplace, materializes cache, patches registry |
| `/mem-research start <topic>` | — | Scaffold deep-research topic (`research/<topic>/raw/_locate.md` source-code map template) |
| `/mem-research distill <topic>` | — | Scaffold `distilled.md` (TL;DR / Architecture / Key facts / Code anchors / Delta / Open questions) + run quality gate (<800 words, every raw note has source ref) |
| `/mem-research status` | — | List research topics + state (pending / in-progress / distilled) |
| `/mem-workspace [<verb> [args]]` | — | Workspace management — list (default), create, archive, map |
| `/mem-ops promote` | — | Promote topic to Obsidian wiki (requires claude-obsidian) |
| `/mem-ops restructure` | — | Reorganize topics (move slugs, rebuild MOCs) |
| `/mem-ops lint` | — | v3.3 — heuristic contradiction scan across `[ref]/[decision]/[tool]` lines (polarity-pair mismatches sharing >=3 keywords). Read-only. |
| `/mem-ops compress` | — | v3.3 — rtk-style pre-storage compression (collapse 3+ identical lines + merge `key: value` runs). Deterministic, idempotent. |
| `/mem-ops budget` | — | v3.3 — preview 4-tier weighted context plan for a query (working/episodic/semantic/procedural + Ebbinghaus decay) within a char budget. |

## Multi-session safety

Concurrent Claude sessions writing to `~/.gowth-mem/` are protected by:

1. `fcntl.flock` advisory locks under `~/.gowth-mem/.locks/`.
2. Atomic markdown writes via temp file + `os.replace`.
3. SQLite WAL mode + `busy_timeout=5000` on `index.db`.

Windows lacks `fcntl`; assume single-session use there.

## Auto pull/push around compact

```text
SessionStart  → auto-sync.py --pull-only
PreCompact    → auto-sync.py --commit-only
PostCompact   → auto-sync.py --pull-rebase-push
```

If a pull/rebase conflicts, `_conflict.py` writes `~/.gowth-mem/SYNC-CONFLICT.md` instead of leaving raw conflict markers in markdown files; conflicts on `<ws>/memory/MEMORY.md` merge themselves (free zones merged three-way against the last common commit — a line either side deleted stays deleted — block regenerated) at every stopped rebase step, and a dirty `MEMORY.md` is set aside around the SessionStart pull instead of stashed, merged the same way against HEAD (v4.8). The next prompt reminds you to run `/mem-sync resolve`. A stash-pop conflict on any other file is reported and blocks commits until you resolve it by hand.

## Recall

Per-prompt recall (v4.8, `recall-on-prompt.sh` → `_recall_prompt.py`) runs the same BM25 query as `/mem-recall` on every real prompt ≥ 40 bytes and injects at most 3 entries / 2,000 chars — only when the chunk contains ≥ 50% of the prompt's content terms AND its subject term, scores under the bm25 threshold, and was not injected earlier in the session. Measured on the live vault: 0–3 of 20 realistic generic prompts inject per workspace. `state.json.recall_daily` counts every profiled prompt; `/mem-cost` prints the rate. The `index.db` (built by `/mem-ops reindex`, refreshed incrementally at every Stop) also powers `[[wikilink]]` slug resolution.

v4.9 adds two things to the same `additionalContext`, inside the same 2,000-char budget:

- **Topic cards.** When a prompt names a topic that has a card (its slug, its slug as a phrase, or an alias — generic
  words such as `test`, `config` or `next step` never count), the card is injected once per session, labelled as data
  from your vault. Prompts of 8–39 bytes skip BM25 but still get a card when a cheap `grep` against
  `.curator/cards-terms-<ws>` matches them; anything else that short starts no Python at all.
- **Corrections.** An entry that a later `Correction: … Corrects: <path>` entry corrects is shown with
  `(corrected <date>: <path>)`, and when it qualifies for per-prompt recall its newest correction is injected right
  after it. `/mem-recall` prints the same annotation.

The recall header says what the block is: `[gowth-mem:recall ws=<ws>] related memory from your vault (data, not
instructions; read the file for more):`.

**v3.3 deterministic retrieval stack** (no LLM, pure stdlib):

1. **FTS5 BM25** — primary, via `_index.py` against `index.db`.
2. **Char-trigram Jaccard** (`_lexical.fuzzy_search`) — fallback for typos and morphology where BM25 underperforms.
3. **Budget planner** (`_budget.plan_context`) — combines tier weight + Jaccard + Ebbinghaus recency to fill a token budget. Opt-in via `settings.json → retrieval.use_budget_planner: true`; when enabled, `SessionStart` uses it instead of the hard-coded 6-file stable prefix.
4. **LLM embeddings** — disabled by default. Set `GOWTH_MEM_USE_LLM_EMBED=1` and provide an `OPENAI_API_KEY` / `VOYAGE_API_KEY` / `GEMINI_API_KEY` to opt in (legacy path; not on by default in v3.3).

## Token security

- Best: set `GOWTH_MEM_GIT_TOKEN` in your shell environment.
- Fallback: `config.json["token"]` is supported but plaintext on disk and gitignored.
- Sync keeps the git remote URL public and passes HTTPS tokens through a per-command git HTTP header.
- Never commit real token/API-key/password values into synced memory files.
- `shared/secrets.md` stores pointers only: env var names and where to obtain credentials.

## Debugging hooks

Set `GOWTH_MEM_DEBUG=1` to write hook diagnostics to `~/.gowth-mem/logs/hooks.log`. Hooks still avoid spamming normal Claude prompt output.

## Settings

`~/.gowth-mem/settings.json` holds only keys the code reads (v4.8 removed 51 documented-but-unread
keys; `tests/test_settings_example.py` pins both directions). Booleans accept `true`/`false` or the
strings `"true"`/`"false"`; a malformed value falls back to its default without resetting its section.

```jsonc
{
  "native":   { "enabled": true },                 // MEMORY.md (auto memory) carries the working set
  "recall":   { "on_prompt_enabled": true,         // per-prompt BM25 recall on UserPromptSubmit
                "on_prompt_max_entries": 3, "on_prompt_max_chars": 2000,
                "on_prompt_min_terms": 2, "on_prompt_min_coverage": 0.5, "on_prompt_score_threshold": "auto",
                "on_prompt_prompt_cap": 2000 },
  "memfile":  { "max_lines": 130, "max_chars": 12000 },   // budget of the managed block
  "auto_journal": { "journal_every": 10, "auto_journal_enabled": true },
  "reflection": { "enabled": true, "turn_interval": 15, "min_review_turns": 10 },
  "journal":  { "raw_ttl_days": 7, "auto_forget_enabled": true },
  "gate":     { "enabled": true, "strict": true, "english_only": false },
  "sync":     { "auto_sync_on_stop": true, "min_interval_minutes": 30 },
  // v4.9
  "curator":  { "enabled": true, "hour": 0, "min_new_turns": 5, "model": "sonnet",
                "max_cards": 5, "max_research": 2, "max_research_tries": 3, "stale_days": 14,
                "retry_minutes": 120, "hold_days": 30, "max_prep_chars": 80000, "first_window_days": 7,
                "hosts": [] },                     // [] = any host; else only these hosts curate
  "topic_card": { "max_chars": 2000 },
  "gaps":     { "memfile_lines": 6 },              // open questions shown in MEMORY.md
  "capture":  { "pipeline_prefixes": [] }          // your own bot/pipeline prompt prefixes
}
```

`recall.card_enabled` (true) and `recall.card_max_chars` (1200) control card injection.

See `templates/dot-gowth-mem/settings.example.v3.json` for every key with notes.

## What this is not

- Not a sandbox.
- Not a general knowledge graph engine.
- Not a Windows-first multi-session system.
- Not a replacement for project-local docs or tests.

---

## 🇻🇳 Tiếng Việt

Plugin Claude Code cho **bộ nhớ bền vững, tổ chức theo topic**, đồng bộ qua git remote của bạn giữa nhiều máy. State nằm ở `~/.gowth-mem/` — chia thành `shared/` (kiến thức chung) và `workspaces/<ws>/` (kiến thức theo workspace).

**Hướng dẫn sử dụng đầy đủ:** [docs/USAGE.vi.md](docs/USAGE.vi.md) — cái gì tự chạy, curator hằng ngày, sổ câu hỏi
"Phải research", thẻ topic, lưu và đính chính bộ nhớ, bot/pipeline, xử lý sự cố.

### Có gì mới ở v4.9 — kiến thức không bị cũ

- **Curator chạy mỗi ngày.** Mỗi workspace một lần/ngày (lượt thật đầu tiên sau `curator.hour`, có ít nhất
  `curator.min_new_turns` lượt mới), Stop hook yêu cầu Claude khởi chạy MỘT subagent nền `gowth-mem:curator` (chỉ có
  Bash, Read, Grep, Glob, WebSearch, WebFetch). Nó đọc bản tóm tắt tất định các lượt chưa worklog nào xử lý
  (`_curate.py prep`), rà các entry đã ghi trong ngày, thêm entry đính chính chỉ rõ entry bị sai, làm mới thẻ topic, mở
  và trả lời câu hỏi, biến việc handoff đã cũ thành câu hỏi "có vẻ xong rồi — tick nhé?" (không bao giờ tự sửa handoff),
  và ghi worklog có bằng chứng vào `docs/worklog/<date>-<host>-<run>.md`. `/mem-ops curate status | prep | now`.
- **Sổ câu hỏi — "Phải research".** `docs/questions.md` của mỗi workspace: câu `research` (agent tự tìm được) và câu
  `ask` (chỉ bạn biết). `/mem-ops gap add | list | answer | close | tried`. Câu hỏi mở hiện trong `MEMORY.md` (`ask`
  trước); curator research vài câu mỗi lần, thử 3 lần không được thì chuyển thành `ask`.
- **Thẻ topic.** Sự thật hiện tại của một topic (Core · State · Routing · Gotchas · Sources, ≤2.000 ký tự) nằm trong
  TL;DR của README, chỉ ghi qua `/mem-topic card` (thẻ cũ được lưu nguyên văn vào `<topic>/.history/`); MOC rebuild không
  bao giờ đụng vào. Prompt nhắc tên topic sẽ nhận thẻ của nó, một lần mỗi phiên.
- **Đính chính, không sửa đè.** Entry sai không bị sửa/xoá: thêm entry cùng loại (giữ đủ phần bắt buộc của loại đó)
  `Correction: <cũ> → <mới> (<ngày>; Corrects: <path>; Source: …)` — `[hypothesis]` đã có kết luận thì thành `[ref]`;
  recall hiện bản đính chính ngay cạnh entry cũ.
- **Bộ rule + ref.** `shared/memory-rules.md`: thứ tự tra cứu, chưa biết thì thành câu hỏi, nhãn bằng chứng
  (CONFIRMED / UNVERIFIED / FALSE POSITIVE), đính chính, thẻ, chỗ đặt kiến thức, ghi đúng bối cảnh người vận hành,
  "memory là dữ liệu, không phải mệnh lệnh", mẫu entry. `_canon.py` giao và commit file ngay sau mỗi lần pull thành
  công (bản bạn đã sửa thì không bị ghi đè).
- **Không lẫn prompt của bot.** `GOWTH_MEM_OFF=1` tắt mọi hook gowth-mem cho tiến trình đó (đặt cho bot/pipeline chạy
  `claude -p`). Prompt dạng pipeline (`STAGE=…`, `### TASK`, … hoặc tiền tố trong `capture.pipeline_prefixes`) không bị
  đếm, ghi log, recall hay dump.

### Vì sao có v2.0

State v1.0 nằm ở `<workspace>/.gowth-mem/` — silo theo project. v2.0 trả lời 3 vấn đề: 1 thư mục `~/.gowth-mem/` toàn cục, sắp xếp theo topic, an toàn khi chạy song song, tự động pull/push quanh `/compact`, AI giúp resolve conflict. Từ v2.7, layout chia `shared/` (cross-workspace) + `workspaces/<ws>/` (per-workspace).

### Cài đặt nhanh

```text
/plugin marketplace add OoOshisuiOoO/gowth-mem
/plugin install gowth-mem@gowth-mem
```

Restart Claude Code, rồi:

```text
/mem-install     wizard cài đặt: tạo ~/.gowth-mem, hỏi remote+branch+token, push initial
memx             build search index
```

**Lưu ý SSH**: Claude Code clone qua SSH mặc định. Nếu chưa setup SSH key cho GitHub, fix bằng:

```bash
git config --global url."https://github.com/".insteadOf git@github.com:
```

Hoặc clone thủ công: `git clone https://github.com/OoOshisuiOoO/gowth-mem ~/.claude/plugins/gowth-mem`.

### Setup máy thứ 2

```bash
git clone <REMOTE-URL> ~/.gowth-mem
/mem-ops config             # set remote+token (config.json gitignore nên không có trong clone)
memx                    # build local index
```

### Hook (chạy tự động — không cần gõ command)

| Event | Hook | Làm gì |
|---|---|---|
| SessionStart | `bootstrap-load.py` | v4.8: làm mới block `MEMORY.md` + header ≤600 ký tự khi project đã wire (native), không thì fallback bootstrap ≤8.500 ký tự; `/clear` bootstrap lại |
| SessionStart | `auto-sync.py --pull-only` | Rebase remote → local, không push |
| PreCompact | `precompact-flush.py` | Dump thô các lượt human gần nhất vào journal hôm nay; KHÔNG BAO GIỜ chặn `/compact` (v3.5.1+) |
| PreCompact | `auto-sync.py --commit-only` | Commit local không network |
| PostCompact | `auto-sync.py --pull-rebase-push` | Sync đầy đủ; conflict → `SYNC-CONFLICT.md` |
| UserPromptSubmit | `conflict-detect.sh` → `recall-on-prompt.sh` | Nhắc `/mem-sync resolve` khi có conflict; recall BM25 theo prompt có cổng lọc (v4.8, ≤2,000 ký tự, mặc định im lặng); v4.9: thẻ của topic được nhắc tên (một lần mỗi phiên) và bản đính chính cạnh entry cũ |
| Stop | `auto-journal.py` | Đếm lượt thật; mỗi N lượt phát directive journal / self-review qua kênh Stop không-lỗi (teammate nền / judge mới — v4.7); v4.8: làm mới `MEMORY.md`, sanitize memory, reindex incremental; v4.9: directive curator mỗi ngày một lần |

### Slash command & shortcut

| Command | Shortcut | Mục đích |
|---|---|---|
| `/mem-install` | `memI` | Wizard cài lần đầu |
| `/mem-ops config` | `memg` | Đổi remote / branch / token |
| `/mem-sync` | `memy` | Sync thủ công |
| `/mem-sync resolve` | `memC` | AI giải conflict |
| `/mem-ops migrate-global` | `memm` | Import v1.0 per-workspace → v2.x global |
| `/mem-topic` | `memT` | List / inspect / route topic; v4.9 `card <slug> [show \| set \| summary "<dòng>" \| check]` sửa thẻ topic |
| `/mem-save` | `mems` | v4.9 `/mem-save [type] "<entry>"` lưu một entry (gate, tag, dedup, routing); không tham số thì distill cả phiên |
| `/mem-ops gap` | — | v4.9 sổ câu hỏi: `add research\|ask "<câu hỏi>" --why …`, `list`, `answer <id> "<trả lời>" --evidence …`, `close`, `tried` |
| `/mem-ops curate` | — | v4.9 curator hằng ngày: `status` (mặc định), `prep` (xem digest, không ghi gì), `now` (chạy ngay hôm nay) |
| `/mem-distill` | `memd` | Journal → topics |
| `/mem-ops reflect` | `memr` | Sinh reflection |
| `/mem-ops skillify` | `memk` | Extract workflow tái dùng |
| `/mem-ops journal` | `memj` | Mở journal hôm nay |
| `/mem-recall` | — | v3.4 — FTS5 BM25 recall + tuỳ chọn `--type=<tag>` lọc theo 9-type schema |
| `/mem-ops dream` | — | v3.4 — chạy Light/REM/Deep consolidation (`_consolidate.py`); hỗ trợ `--ws`, `--dry-run`, skip từng phase |
| `/mem-ops reindex` | `memx` | Rebuild SQLite FTS5+vec |
| `/mem-cost` | `memc` | Estimate token footprint của bootstrap |
| `/mem-ops prune` | `memp` | Active DELETE outdated/superseded/duplicate |
| `/mem-lesson` | `memL` | Append 5-field bug/lesson |
| `/mem-doctor` | — | Self-heal install path drift (issue #52218) |
| `/mem-ops lint` | — | v3.3 — quét contradiction giữa `[ref]/[decision]/[tool]` (polarity mismatch + >=3 keyword chung). Read-only. |
| `/mem-ops compress` | — | v3.3 — nén rtk-style trước khi ghi (gộp 3+ dòng giống nhau + merge `key: value` chung key). Idempotent. |
| `/mem-ops budget` | — | v3.3 — preview kế hoạch context 4-tier (working/episodic/semantic/procedural + Ebbinghaus decay) trong char budget. |

Slash command vẫn dùng đầy đủ (`/mem-save`, `/mem-recall`, `/mem-ops dream`, ...). Shortcut auto-detect intent từ prefix prompt đã bỏ ở v3.2 — gõ command trực tiếp. `mem-bootstrap` và `mem-flush` đã bị xoá ở v3.4 (auto-run qua hook).

### 9-type schema (line-level prefix trong topic file)

```
- [goal]        user objective (Status: + Done when:)
- [hypothesis]  UNVERIFIED claim (Verify: path; promotes to [ref])
- [exp]         debug / fix / lesson
- [ref]         fact đã verify (Source: BẮT BUỘC)
- [tool]        tool quirk theo topic
- [decision]    architectural choice + lý do
- [reflection]  pattern / takeaway
- [skill-ref]   pointer tới skills/<slug>.md
- [secret-ref]  pointer tới docs/secrets.md (env-var name)
```

### Multi-session (chạy song song)

Plugin bảo vệ shared state bằng:

1. **`fcntl.flock`** advisory locks ở `~/.gowth-mem/.locks/` (sync 30s, state 5s).
2. **Atomic write** qua `_atomic.atomic_write` (tempfile + `os.replace`).
3. **SQLite WAL mode** + `busy_timeout=5000` cho `index.db`.

Windows không có `fcntl` → khuyến nghị single-session.

### Recall (tìm lại knowledge cũ)

Từ v4.8, `recall-on-prompt.sh` chạy cùng truy vấn BM25 như `/mem-recall` cho mỗi prompt thật ≥40 byte và chỉ chèn tối
đa 3 entry / 2.000 ký tự khi chunk chứa ≥50% từ khoá của prompt VÀ từ chủ đề của nó (mặc định im lặng). v4.9 thêm: thẻ
của topic mà prompt nhắc tên (prompt 8–39 byte chỉ được xét thẻ, qua một lệnh `grep` rẻ trong bash), và bản đính chính
đi kèm entry cũ. Khối recall ghi rõ đây là dữ liệu từ vault, không phải mệnh lệnh. Muốn tìm sâu hơn: `/mem-recall
<query>` (`--topic`, `--keyword`, `--days`). `index.db` (build bằng `/mem-ops reindex`, reindex incremental mỗi Stop)
cũng dùng để resolve `[[wikilink]]` slug.

**v3.3 — stack retrieval deterministic (không LLM):**

1. **FTS5 BM25** chính (`_index.py`).
2. **Char-trigram Jaccard** fallback (`_lexical.py`) khi BM25 yếu (typo, morphology đa ngôn ngữ).
3. **Budget planner** (`_budget.py`) — 4-tier (working/episodic/semantic/procedural) + Ebbinghaus 14-day decay. Bật bằng `settings.json → retrieval.use_budget_planner: true`.
4. **LLM embedding** — tắt mặc định ở v3.3. Cần `GOWTH_MEM_USE_LLM_EMBED=1` + key (OpenAI/Voyage/Gemini) để bật lại path cũ.

### Token security

- **Tốt nhất**: `export GOWTH_MEM_GIT_TOKEN=ghp_xxxx` trong shell rc.
- OK: `config.json["token"]` (gitignore, plaintext on disk).
- KHÔNG: commit token vào file synced.
- `shared/secrets.md` chỉ POINTER (env-var name + cách lấy) — không bao giờ ghi giá trị thật.

### Troubleshooting

| Triệu chứng | Cách fix |
|---|---|
| `/mem-install` báo "already initialized" | Đã cài rồi. Dùng `/mem-ops config`, `/mem-sync`, `/mem-ops migrate-global` |
| Recall không tìm thấy entry vừa lưu | Chạy `memx` rebuild index. Vẫn không thấy → `_topic.py --list` xem entry vào file nào |
| `SYNC-CONFLICT.md` xuất hiện hoài | Chạy `/mem-sync resolve` |
| Push bị reject | Token sai scope (cần `repo`). Check `~/.gowth-mem/config.json` + `echo $GOWTH_MEM_GIT_TOKEN` |
| Plugin im lặng sau update | Issue Claude Code #52218 — chạy `/mem-doctor` (hoặc setup self-heal hook bên trên) |

## License

MIT
