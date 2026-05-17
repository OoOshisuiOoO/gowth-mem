# gowth-mem

A Claude Code plugin for **persistent, topic-organized memory** synced across machines via your own git remote. It hooks the chat lifecycle so memory can bootstrap, recall, journal, distill, and sync itself.

Built on patterns from mem0, Letta/MemGPT, Zep, Cognee, MemPalace, Generative Agents, Voyager, Reflexion, Anthropic contextual retrieval, and SM-2 spaced repetition. See [`RESEARCH.md`](RESEARCH.md).

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
│   └── skills/<slug>.md
├── workspaces/<ws>/                           active workspace-scoped knowledge
│   ├── AGENTS.md                              workspace rules
│   ├── workspace.json
│   ├── _MAP.md
│   ├── docs/{handoff,exp,ref,tools,files}.md
│   ├── journal/<date>.md
│   ├── skills/<slug>.md
│   ├── research/<topic>/                      deep-research workspace (raw/ + distilled.md)
│   └── <slug>/                                v3 topic folder
│       ├── 00-README.md                       MOC: TL;DR + Aspects (auto-rebuilt) + Cross-links
│       ├── YYYY-MM-DD-<aspect>.md             dated aspect note (append-only, written by route())
│       └── lessons.md                         per-topic 5-field bug/lesson ledger
├── settings.json                              synced behavior settings (layout_version: 3)
├── config.json                                remote/branch/token config; gitignored
├── state.json                                 SRS data; gitignored
├── index.db                                   FTS5 + optional sqlite-vec index; gitignored
├── .locks/                                    fcntl lock files; gitignored
├── .backup/v2-pre-v3-<utc>/                   migration snapshots (rolling-2); gitignored
└── .git/                                      sync repository
```

Topic slugs are unique inside a workspace. v3 wikilink resolution falls back through six layers: `<ws>/<slug>/00-README.md` (v3), `<ws>/<slug>/<slug>.md` (v2.4 fallback), `<ws>/<slug>.md` (v2.3 flat), `<ws>/lessons.md`, `shared/<key>.md`, and cross-workspace `[[ws:slug]]`. New writes always land in the v3 dated-aspect layout.

### Upgrading from v2.x

`/mem-install` detects `layout_version < 3` and offers `/mem-migrate-v3`:

```text
/mem-migrate-v3              # dry-run is default — preview the move plan
/mem-migrate-v3 --force      # execute: snapshot → classify → execute → verify
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
/mem-migrate-global   import any older per-workspace .gowth-mem folders
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
| SessionStart | `bootstrap-load.py` | Inject shared rules, workspace rules, docs, recent topics, and journal snippets |
| SessionStart | `auto-sync.py --pull-only --quiet` | Rebase remote into local without pushing |
| SessionStart | `system-augment.py` | Inject cwd, git, OS, host, and datetime |
| PreCompact | `precompact-flush.py` | Hard-block with distill instructions before compact |
| PreCompact | `auto-sync.py --commit-only --quiet` | Commit local memory changes without network |
| PostCompact | `auto-sync.py --pull-rebase-push --quiet` | Pull, rebase, push; conflict writes `SYNC-CONFLICT.md` |
| UserPromptSubmit | `conflict-detect.py` | Remind when sync conflict resolution is pending |
| UserPromptSubmit | `recall-active.py` | Hybrid FTS5/vector/grep recall, MMR diversity, SRS resurfacing, wikilink follow |
| UserPromptSubmit | `user-augment.py` | Inject rules and shortcut intent context |
| Stop | `auto-journal.py` | Periodic journal/distill reminder |

## Slash commands & shortcuts

| Command | Shortcut | Purpose |
|---|---|---|
| `/mem-install` | `memI` | First-time setup wizard |
| `/mem-config` | `memg` | Change git remote, branch, token strategy, or workspace map |
| `/mem-sync` | `memy` | Manual sync |
| `/mem-sync-resolve` | `memC` | AI-mediated conflict resolution |
| `/mem-migrate-global` | `memm` | Import older per-workspace `.gowth-mem/` data |
| `/mem-migrate-v3` | — | Promote `~/.gowth-mem/` from v2.x to v3 topic-folder layout (7-step pipeline, dry-run default, rolling-2 backup) |
| `/mem-topic` | `memT` | List, inspect, or route topics |
| `/mem-save` | `mems` | Save entry to a topic |
| `/mem-distill` | `memd` | Journal to topics |
| `/mem-reflect` | `memr` | Generate reflections |
| `/mem-skillify` | `memk` | Extract reusable workflows |
| `/mem-bootstrap` | `memb` | Print workspace / doing / next / blocker |
| `/mem-hyde-recall` | `memh` | HyDE retrieval for conceptual queries |
| `/mem-journal` | `memj` | Open today's journal |
| `/mem-reindex` | `memx` | Rebuild SQLite FTS5 + optional vector index |
| `/mem-cost` | `memc` | Estimate bootstrap token footprint |
| `/mem-prune` | `memp` | Remove outdated, superseded, or duplicate entries |
| `/mem-lesson` | `memL` | Append a 5-field bug/lesson entry |
| `/mem-doctor` | — | Self-heal plugin install path drift (issue #52218); pulls marketplace, materializes cache, patches registry |
| `/mem-research-start <topic>` | — | Scaffold deep-research topic (`research/<topic>/raw/_locate.md` source-code map template) |
| `/mem-research-distill <topic>` | — | Scaffold `distilled.md` (TL;DR / Architecture / Key facts / Code anchors / Delta / Open questions) + run quality gate (<800 words, every raw note has source ref) |
| `/mem-research-status` | — | List research topics + state (pending / in-progress / distilled) |
| `/mem-workspace` | — | Show or switch active workspace |
| `/mem-workspace-create` | — | Scaffold new workspace |
| `/mem-workspace-list` | — | List all workspaces |
| `/mem-workspace-archive` | — | Archive workspace to `_archive/` |
| `/mem-workspace-map` | — | Add/remove cwd-glob → workspace mapping |
| `/mem-promote` | — | Promote topic to Obsidian wiki (requires claude-obsidian) |
| `/mem-restructure` | — | Reorganize topics (move slugs, rebuild MOCs) |
| `/mem-flush` | — | Manual pre-compact flush reminder |

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

If a pull/rebase conflicts, `_conflict.py` writes `~/.gowth-mem/SYNC-CONFLICT.md` instead of leaving raw conflict markers in markdown files. The next prompt reminds you to run `/mem-sync-resolve`.

## Recall

Each user prompt triggers `recall-active.py`:

1. Scope defaults to active workspace + `shared/` unless cross-workspace recall is enabled.
2. If `index.db` exists, use hybrid FTS5 BM25 + optional sqlite-vec.
3. Otherwise grep workspace/shared markdown files.
4. Skip `(superseded)` lines and expired `valid_until:` lines.
5. Apply MMR diversity, wikilink follow, and SRS resurfacing.

## Token security

- Best: set `GOWTH_MEM_GIT_TOKEN` in your shell environment.
- Fallback: `config.json["token"]` is supported but plaintext on disk and gitignored.
- Sync keeps the git remote URL public and passes HTTPS tokens through a per-command git HTTP header.
- Never commit real token/API-key/password values into synced memory files.
- `shared/secrets.md` stores pointers only: env var names and where to obtain credentials.

## Debugging hooks

Set `GOWTH_MEM_DEBUG=1` to write hook diagnostics to `~/.gowth-mem/logs/hooks.log`. Hooks still avoid spamming normal Claude prompt output.

## Settings

`~/.gowth-mem/settings.json` controls auto-sync, active workspace behavior, topic routing, recall limits, embedding provider, and conflict resolution mode. v3.0 adds `layout_version: 3`, `topic_layout.mode: folder`, `topic_layout.reserved_subdirs` (including `research`), `recall.layer_scores` for per-tier tuning, and `migration.v3_backup_keep: 2`. See `templates/dot-gowth-mem/settings.example.v3.json` for the current schema.

## What this is not

- Not a sandbox.
- Not a general knowledge graph engine.
- Not a Windows-first multi-session system.
- Not a replacement for project-local docs or tests.

---

## 🇻🇳 Tiếng Việt

Plugin Claude Code cho **bộ nhớ bền vững, tổ chức theo topic**, đồng bộ qua git remote của bạn giữa nhiều máy. State nằm ở `~/.gowth-mem/` — chia thành `shared/` (kiến thức chung) và `workspaces/<ws>/` (kiến thức theo workspace).

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
/mem-config             # set remote+token (config.json gitignore nên không có trong clone)
memx                    # build local index
```

### Hook (chạy tự động — không cần gõ command)

| Event | Hook | Làm gì |
|---|---|---|
| SessionStart | `bootstrap-load.py` | Inject shared+workspace rules, docs/handoff, top topic gần đây, journal hôm nay (cap 15k tổng) |
| SessionStart | `auto-sync.py --pull-only` | Rebase remote → local, không push |
| SessionStart | `system-augment.py` | cwd, git, OS, host, datetime |
| PreCompact | `precompact-flush.py` | **HARD-BLOCK** distill journal → topics trước khi compact |
| PreCompact | `auto-sync.py --commit-only` | Commit local không network |
| PostCompact | `auto-sync.py --pull-rebase-push` | Sync đầy đủ; conflict → `SYNC-CONFLICT.md` |
| UserPromptSubmit | `conflict-detect.py` | Nhắc chạy `/mem-sync-resolve` khi có conflict |
| UserPromptSubmit | `recall-active.py` | Hybrid FTS5/vector/grep recall, MMR, SRS, wikilink follow |
| UserPromptSubmit | `user-augment.py` | Inject rules + shortcut intent |
| Stop | `auto-journal.py` | Mỗi 10 turn: BLOCK với hướng dẫn auto-distill + active prune |

### Slash command & shortcut

| Command | Shortcut | Mục đích |
|---|---|---|
| `/mem-install` | `memI` | Wizard cài lần đầu |
| `/mem-config` | `memg` | Đổi remote / branch / token |
| `/mem-sync` | `memy` | Sync thủ công |
| `/mem-sync-resolve` | `memC` | AI giải conflict |
| `/mem-migrate-global` | `memm` | Import v1.0 per-workspace → v2.x global |
| `/mem-topic` | `memT` | List / inspect / route topic |
| `/mem-save` | `mems` | Lưu entry vào topic |
| `/mem-distill` | `memd` | Journal → topics |
| `/mem-reflect` | `memr` | Sinh reflection |
| `/mem-skillify` | `memk` | Extract workflow tái dùng |
| `/mem-bootstrap` | `memb` | 3 dòng: doing / next / blocker |
| `/mem-hyde-recall` | `memh` | HyDE retrieval cho conceptual query |
| `/mem-journal` | `memj` | Mở journal hôm nay |
| `/mem-reindex` | `memx` | Rebuild SQLite FTS5+vec |
| `/mem-cost` | `memc` | Estimate token footprint của bootstrap |
| `/mem-prune` | `memp` | Active DELETE outdated/superseded/duplicate |
| `/mem-lesson` | `memL` | Append 5-field bug/lesson |
| `/mem-doctor` | — | Self-heal install path drift (issue #52218) |

Shortcut match ở **đầu prompt**:

```
mems quyết định dùng EMA cross 9/21
memb
memh làm sao plugin install hooks?
```

### 7-type schema (line-level prefix trong topic file)

```
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

Mỗi UserPrompt → `recall-active.py`:

1. Scope default: active workspace + `shared/`.
2. Có `index.db` → hybrid FTS5 BM25 + sqlite-vec (optional).
3. Không có → grep workspace + shared markdown.
4. Skip `(superseded)` + `valid_until:` đã hết hạn.
5. MMR diversity, wikilink follow 1 hop, SRS resurfacing (~25% prob, ≥7 days unseen).

### Token security

- **Tốt nhất**: `export GOWTH_MEM_GIT_TOKEN=ghp_xxxx` trong shell rc.
- OK: `config.json["token"]` (gitignore, plaintext on disk).
- KHÔNG: commit token vào file synced.
- `shared/secrets.md` chỉ POINTER (env-var name + cách lấy) — không bao giờ ghi giá trị thật.

### Troubleshooting

| Triệu chứng | Cách fix |
|---|---|
| `/mem-install` báo "already initialized" | Đã cài rồi. Dùng `/mem-config`, `/mem-sync`, `/mem-migrate-global` |
| Recall không tìm thấy entry vừa lưu | Chạy `memx` rebuild index. Vẫn không thấy → `_topic.py --list` xem entry vào file nào |
| `SYNC-CONFLICT.md` xuất hiện hoài | Chạy `/mem-sync-resolve` |
| Push bị reject | Token sai scope (cần `repo`). Check `~/.gowth-mem/config.json` + `echo $GOWTH_MEM_GIT_TOKEN` |
| Plugin im lặng sau update | Issue Claude Code #52218 — chạy `/mem-doctor` (hoặc setup self-heal hook bên trên) |

## License

MIT
