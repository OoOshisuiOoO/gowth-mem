# gowth-mem (tiếng Việt)

Plugin Claude Code cho **bộ nhớ bền vững, tổ chức theo topic**, đồng bộ qua git remote của bạn giữa nhiều máy. Mọi state nằm ở `~/.gowth-mem/` (toàn cục, không chia theo workspace).

## Vì sao có v2.0

Nếu bạn đã từng dùng v1.0: state nằm ở `<workspace>/.gowth-mem/` — mỗi project một folder. Ba vấn đề:

1. **Kiến thức là cross-project**: fact học ở `AI-trade/` cũng cần ở `bot/`. Silo theo workspace bắt bạn re-discover.
2. **Bạn nghĩ theo topic, không theo folder**: "EMA strategy", "Claude hooks", "Bash gotchas" — pool toàn cục có index theo topic > N pool theo project.
3. **Multi-session là thực tế**: bạn chạy 2-3 session Claude song song. Pool toàn cục lộ race condition; cần lock.

v2.0 trả lời ba vấn đề: 1 thư mục `~/.gowth-mem/`, sắp xếp theo topic, an toàn khi chạy song song, tự động pull/push quanh `/compact`, AI giúp resolve conflict.

## Kiến trúc

```
~/.gowth-mem/                   1 chỗ duy nhất, dùng chung mọi project
├── AGENTS.md                   operating rules (sync)
├── settings.json               cấu hình plugin (sync)
├── config.json                 remote+token (gitignore, theo máy)
├── state.json                  SRS data (gitignore, theo máy)
├── index.db                    SQLite FTS5+vec (gitignore, theo máy)
├── .git/                       sync target
├── .locks/                     fcntl lock files (gitignore)
├── topics/                     ★ kiến thức tổ chức theo topic
│   ├── _index.md               registry các topic
│   └── <slug>.md               1 file / topic (claude-hooks, ema-strategy, …)
├── docs/                       registry cross-topic
│   ├── handoff.md              session state (mỗi dòng prefix host:<máy>)
│   ├── secrets.md              POINTER (env-var name; KHÔNG bao giờ value)
│   └── tools.md                tool quirks dùng chung nhiều topic
├── journal/<date>.md           log thô hằng ngày (sync)
└── skills/<slug>.md            workflow tái dùng kiểu Voyager (sync)
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

### Topic routing

Helper `_topic.py` quyết định 1 entry vào file nào:

1. Trích keywords ≥4 ký tự (bỏ stopword).
2. Đếm overlap với từng `topics/*.md` đang có.
3. Overlap max ≥ 3 → slug đó.
4. Không match → tạo slug mới từ top-2 keyword distinct (kebab-case ≤40 chars).
5. Cuối cùng → fallback `misc` (cấu hình trong settings.json).

### Cross-references

Trong topic file dùng `[[other-slug]]`. Hook recall sẽ follow 1 hop khi top hit là 1 topic file.

## Cài đặt

Repo này vừa là plugin chuẩn vừa là marketplace 1-plugin của Claude Code, nên có 2 cách cài.

### Cách A — Qua plugin manager của Claude Code (KHUYẾN NGHỊ)

Trong Claude Code:

```
/plugin marketplace add OoOshisuiOoO/gowth-mem
/plugin install gowth-mem@gowth-mem
```

Restart Claude Code để hook đăng ký. Update về sau:

```
/plugin marketplace update gowth-mem
/plugin update gowth-mem@gowth-mem
```

Gỡ:

```
/plugin uninstall gowth-mem@gowth-mem
/plugin marketplace remove gowth-mem
```

### Cách B — Clone thủ công

```bash
git clone https://github.com/OoOshisuiOoO/gowth-mem ~/.claude/plugins/gowth-mem
```

Restart Claude Code. Nếu build của bạn không tự discover plugin, thêm vào `~/.claude/settings.json`:

```json
{
  "plugins": {
    "gowth-mem": { "enabled": true }
  }
}
```

### Sau đó chạy wizard cài đặt

```
/mem-install
```

Wizard sẽ:

1. Tạo `~/.gowth-mem/{topics,docs,journal,skills}/`.
2. Copy templates: `AGENTS.md`, `settings.json`, `topics/_index.md`, `topics/misc.md`, `docs/{handoff,secrets,tools}.md`.
3. Hỏi bạn 3 câu:
   - **Git remote URL?** Ví dụ `https://github.com/USER/gowth-mem-data.git`. Khuyến nghị HTTPS để dùng token.
   - **Branch?** Default `main`.
   - **Token?** Có 2 lựa chọn:
     - `env`: tự `export GOWTH_MEM_GIT_TOKEN=ghp_xxx` trong shell rc (KHUYẾN NGHỊ — token không nằm trên disk).
     - `config`: paste token; lưu vào `~/.gowth-mem/config.json` (cảnh báo: plaintext on disk).
4. Ghi `~/.gowth-mem/config.json`.
5. Chạy `_sync.py --init` để tạo `.git`, push initial state.

Wizard idempotent — chạy lại trên máy đã cài không phá gì.

Sau install:

```
memx                    build search index (FTS5 + sqlite-vec optional)
/mem-migrate-global     import từ v1.0 per-workspace .gowth-mem/ nếu có
```

### Setup máy thứ 2 (sau khi máy 1 đã push)

```bash
git clone <REMOTE-URL> ~/.gowth-mem
/mem-config             # set remote+token (config.json gitignore nên không có trong clone)
memx                    # build local index
```

## Hooks (chạy tự động — không cần gõ command)

| Event | Hook | Làm gì |
|---|---|---|
| SessionStart | `bootstrap-load.py` | Inject AGENTS + topics/_index + docs/handoff + top-3 topic gần đây + journal hôm nay/qua (cap 12k/file, 60k tổng) |
| SessionStart | `auto-sync.py --pull-only --quiet` | Rebase remote vào local, không push |
| SessionStart | `system-augment.py` | cwd, git, OS, datetime |
| PreCompact | `precompact-flush.py` | **HARD-BLOCK**: Claude phải distill journal → topics trước khi compact summarize |
| PreCompact | `auto-sync.py --commit-only` | Commit local không network |
| PostCompact | `auto-sync.py --pull-rebase-push` | Sync đầy đủ; conflict → AI flow |
| UserPromptSubmit | `conflict-detect.py` | Nếu có `SYNC-CONFLICT.md` → nhắc chạy `/mem-sync-resolve` |
| UserPromptSubmit | `recall-active.py` | Hybrid recall (FTS5 + vector + grep), MMR diversity, SRS resurfacing, wikilink follow |
| UserPromptSubmit | `user-augment.py` | **Inject `<Rules>...AGENTS.md...</Rules>` mỗi prompt** + shortcut keywords + intent match |
| Stop | `auto-journal.py` | Mỗi 10 turn: BLOCK với hướng dẫn auto-distill + chạy active prune |

Tắt hook nào quá ồn: edit `~/.claude/plugins/gowth-mem/hooks/hooks.json`.

## Slash commands & shortcut keywords

| Command | Shortcut | Mục đích |
|---|---|---|
| `/mem-install` | `memI` | Wizard cài đặt lần đầu |
| `/mem-config` | `memg` | Đổi remote / branch / token |
| `/mem-sync` | `memy` | Sync thủ công (PostCompact đã tự chạy rồi) |
| `/mem-sync-resolve` | `memC` | AI giải conflict |
| `/mem-migrate-global` | `memm` | v1.0 per-workspace → v2.0 global |
| `/mem-migrate` | — | (legacy) v0.9 → v1.0 |
| `/mem-init` | — | (deprecated; redirects sang `/mem-install`) |
| `/mem-topic` | `memT` | List / inspect / route topic |
| (`mem-save` skill) | `mems` | Save entry vào topic + apply mem0 op |
| `/mem-distill` | `memd` | Journal → topics |
| `/mem-reflect` | `memr` | Sinh reflection từ entries gần đây |
| `/mem-skillify` | `memk` | Extract workflow tái dùng |
| `/mem-bootstrap` | `memb` | 3 dòng: doing / next / blocker |
| `/mem-hyde-recall` | `memh` | HyDE retrieval cho conceptual query |
| `/mem-journal` | `memj` | Mở journal hôm nay |
| `/mem-reindex` | `memx` | Rebuild SQLite FTS5+vec index |
| `/mem-cost` | `memc` | Estimate token footprint của bootstrap |
| `/mem-prune` | `memp` | Active DELETE outdated/superseded/duplicate |
| `/mem-flush` | — | Manual PreCompact reminder |
| `/mem-promote` | — | Topic > 1500 dòng → split thành subdir |

Shortcut match ở **đầu prompt**, ví dụ:

```
mems quyết định dùng EMA cross 9/21
memb
memh làm sao plugin install hooks?
memk vòng build-test-commit này
```

## Workflow hằng ngày

### 1. Bắt đầu session

Không cần làm gì. Hook SessionStart tự load AGENTS.md, topic gần đây, journal hôm nay/qua. Bạn thấy 3 dòng `doing/next/blocker` (nếu chạy `memb`).

### 2. Đang làm việc

Mỗi UserPrompt sẽ:

- Tự động có `<Rules>...AGENTS.md...</Rules>` đính kèm (Claude luôn nhớ rule).
- Recall hook tự tìm các entry liên quan trong topics/docs và inject (vector + BM25 + grep fallback).
- Nếu prompt bắt đầu bằng shortcut (ví dụ `mems`) → inject body skill ngay tại chỗ.

### 3. Lưu kiến thức

```
mems quyết định dùng EMA cross 9/21 vì backtest 2026-04 ổn — Source: backtest_001.ipynb
```

Hệ thống sẽ:

1. Match shortcut `mems`.
2. `_topic.py route()` chọn topic phù hợp (hoặc tạo mới).
3. Append `- [decision] ...` với Source.
4. Apply mem0 ADD/UPDATE/DELETE/NOOP.

### 4. Hết session / trước `/compact`

PreCompact hook tự BLOCK Claude với hướng dẫn distill. Sau đó `/compact` chạy. PostCompact hook tự `pull-rebase-push` sync remote.

### 5. Kiểm tra

```
memb         # đang ở đâu / next / blocker
memT         # list các topic
memc         # token cost của bootstrap
memp         # prune outdated
```

## Multi-session (chạy song song)

Khi bạn chạy 2 session Claude song song trên cùng máy, plugin bảo vệ shared state bằng:

1. **`fcntl.flock` advisory locks** trong `~/.gowth-mem/.locks/`:
   - `sync.lock` — serialize git operations (timeout 30s).
   - `state.lock` — serialize ghi `state.json` SRS (timeout 5s).
2. **Atomic write** qua `_atomic.atomic_write` (tempfile + `os.replace`) — không bao giờ có file ghi dở.
3. **SQLite WAL mode** + `busy_timeout=5000` cho `index.db` — concurrent reader không block writer.

Hook variant của sync sẽ skip im lặng nếu lock đang được giữ (không fail hook). CLI `/mem-sync` đợi tối đa 30s.

Windows: `fcntl` không có → lock thành no-op. Khuyến nghị single-session trên Windows.

## Auto pull/push quanh `/compact`

```
SessionStart  → auto-sync.py --pull-only         (rebase remote → local; quiet)
PreCompact    → auto-sync.py --commit-only       (snapshot trước khi compact summarize)
PostCompact   → auto-sync.py --pull-rebase-push  (full sync; conflict → AI flow)
```

Flow điển hình:

1. Bạn làm việc; `mems` hoặc auto-distill (Stop hook) lưu vào `topics/<slug>.md`.
2. Bạn chạy `/compact`. PreCompact commit local. Compact summarize hội thoại.
3. Sau compact, PostCompact pull remote → rebase commit của bạn → push. Máy khác sẽ thấy thay đổi ở SessionStart kế tiếp.
4. Nếu 2 máy commit cùng dòng → PostCompact ghi `SYNC-CONFLICT.md`, prompt kế tiếp sẽ nhắc bạn chạy `/mem-sync-resolve`.

Có thể tắt từng step trong `settings.json` ở `auto_sync`.

## Conflict resolution (AI hỏi user)

Khi `git pull --rebase` đụng conflict, `_conflict.py` **KHÔNG** để raw `<<<<<<<` markers trong topic files (sẽ phá FTS5 indexing). Thay vào đó:

1. Reset working copy về local side để file vẫn parseable.
2. Ghi `~/.gowth-mem/SYNC-CONFLICT.md` với cấu trúc per-file: local / remote / common ancestor.
3. Exit code 2.

Hook `conflict-detect.py` sẽ nhắc bạn ở mỗi prompt: "chạy `/mem-sync-resolve`". Skill walk từng file:

- Hiện diff local vs remote (max 40 dòng mỗi bên).
- Hỏi: **keep-local** | **keep-remote** | **merge** | **skip** | **abort**.
- Nếu chọn merge: AI đề xuất merged version, bạn confirm hoặc edit lại.
- Apply qua `atomic_write`.
- Sau khi resolve xong: `git rebase --continue` + push, dưới `file_lock("sync")`.
- Xóa `SYNC-CONFLICT.md`.

Bạn nắm quyết định mọi keep/merge; AI chỉ làm phần diff + merge proposal + git mechanic.

## Migration

### v1.0 per-workspace → v2.0 global

```
/mem-migrate-global
```

Skill scan `~/Git/**` (hoặc paths bạn cung cấp) tìm `<ws>/.gowth-mem/AGENTS.md`. Mỗi cái:

- `docs/{exp,ref,tools}.md` lines → routed vào `~/.gowth-mem/topics/<slug>.md` (Source: ws/file).
- `docs/handoff.md` lines → `~/.gowth-mem/docs/handoff.md` prefix `host:<ws>`.
- `docs/secrets.md` → dedup theo env-var name.
- `journal/`, `skills/` → copy (đụng tên thì rename `-from-<ws>`).

`<ws>/.gowth-mem/` được giữ nguyên — bạn xóa thủ công sau khi verify.

### v0.9 → v1.0 (cũ)

`/mem-migrate` vẫn còn nhưng hiếm khi cần.

## Recall (tìm lại knowledge cũ)

Mỗi UserPrompt → `recall-active.py`:

1. Trích keyword ≥5 ký tự (max 8).
2. **Nếu có `index.db`**: hybrid FTS5 BM25 + (optional) sqlite-vec, RRF-merge tại k=60.
3. **Không có**: grep `topics/**/*.md` và `docs/*.md` (skip `journal/`).
4. Skip dòng có `(superseded)` hoặc `valid_until:` đã hết hạn.
5. Tier-score: `journal/today (100)` > `topics/* (80)` > `journal/yesterday (70)` > `docs/* (60)` > `skills/ (40)` > khác.
6. Anthropic contextual prefix: `§ <heading> | <line>` cho mỗi match.
7. MMR diversity: skip file mà top match có Jaccard >0.6 với cái đã chọn.
8. **Wikilink follow**: top hit có `[[other-slug]]` → cũng surface match top của other-slug.
9. **Spaced resurfacing**: ~25% prob/prompt, surface 1 file chưa thấy ≥7 ngày.
10. Update `state.json.last_seen` (dưới `file_lock("state")`).

## `<Rules>` injection mỗi prompt

Hook `user-augment.py` luôn inject content `~/.gowth-mem/AGENTS.md` dạng `<Rules>...</Rules>` ở đầu mỗi UserPrompt. Lý do:

- Claude luôn thấy operating rules ngay cạnh prompt mới.
- Cache-friendly: rules ít thay đổi → Anthropic prompt cache hit 75-90% discount.
- Cap 12k chars để không vượt budget cache.

Nếu bạn không muốn injection này, edit `RULES_MAX_CHARS = 0` trong `user-augment.py` hoặc xóa `AGENTS.md`.

## Token efficiency (prompt caching)

- **Stable prefix** (ít đổi): AGENTS.md, docs/secrets.md, docs/tools.md, topics/_index.md → cached bởi Anthropic 75-90% discount.
- **Volatile suffix**: docs/handoff.md, journal/today.md, retrieved snippets → low cost dù không cache.
- Caps bootstrap: 12k chars/file, 60k tổng.

Mẹo: nếu bạn sửa AGENTS.md mỗi session → cache miss thường xuyên → tốn token. Batch thay đổi vào 1 lần.

## Token security

- **Tốt nhất**: `export GOWTH_MEM_GIT_TOKEN=ghp_xxxx` trong shell rc.
- OK: `config.json["token"]` (gitignore, plaintext on disk; dùng GitHub PAT scope hẹp `repo`).
- KHÔNG: commit token vào file synced. KHÔNG: paste token vào topic.
- Secrets trong topics/docs: POINTER only (env-var name + cách lấy). `secrets.md` được sync — tuyệt đối không ghi giá trị thật.

## Settings (`~/.gowth-mem/settings.json`)

Synced — đổi 1 lần, áp dụng mọi máy:

```json
{
  "version": "2.0",
  "auto_sync": {
    "on_session_start": true,
    "on_pre_compact": true,
    "on_post_compact": true,
    "on_stop_every_n_turns": 10
  },
  "topic_routing": {
    "min_keyword_overlap": 3,
    "default_topic": "misc"
  },
  "embedding": {
    "provider": "openai",
    "model": "text-embedding-3-small"
  },
  "recall": {
    "max_chars_per_file": 12000,
    "max_total_chars": 60000,
    "wikilink_follow": true,
    "srs_resurface_days": 7,
    "srs_resurface_prob": 4
  },
  "conflict_resolution": { "mode": "ai-mediated" }
}
```

| Key | Mô tả |
|---|---|
| `auto_sync.on_session_start` | Pull-only ở SessionStart |
| `auto_sync.on_pre_compact` | Commit-only trước `/compact` |
| `auto_sync.on_post_compact` | Full pull-rebase-push sau `/compact` |
| `auto_sync.on_stop_every_n_turns` | Auto-journal cycle (0 = tắt) |
| `topic_routing.min_keyword_overlap` | Số keyword chung tối thiểu để routing vào topic có sẵn |
| `topic_routing.default_topic` | Slug fallback khi không match |
| `embedding.provider` | `openai` / `voyage` / `gemini` / `none` (auto-detect theo env var) |
| `recall.wikilink_follow` | Follow `[[other-slug]]` 1 hop khi top hit là topic |
| `recall.srs_resurface_days` | File chưa thấy ≥ N ngày mới eligible resurface |
| `recall.srs_resurface_prob` | 1-trong-N prob/prompt để resurface |
| `conflict_resolution.mode` | `ai-mediated` (recommended) \| `manual` \| `abort` |

## Cooperation với claude-obsidian

Nếu bạn dùng [claude-obsidian](https://github.com/AgriciDaniel/claude-obsidian), nó own `<workspace>/wiki/`. Hai layer không xung đột:

- gowth-mem own `~/.gowth-mem/` (toàn cục, theo user).
- claude-obsidian own `<workspace>/wiki/` (theo project, knowledge graph).
- Cả hai inject SessionStart context; additionalContext compose không đụng độ.

Cho long-term, project-bound knowledge: `/save` (claude-obsidian) → `wiki/concepts/`. Cho cross-project: ở `~/.gowth-mem/topics/`.

## Troubleshooting

**`/mem-install` báo "already initialized"**

Bạn đã cài rồi. Dùng `/mem-config` để đổi remote, `/mem-sync` để đồng bộ, `/mem-migrate-global` để import data v1.0.

**Recall không tìm được entry vừa lưu**

Index chưa rebuild. Chạy `memx`. Nếu đã rebuild mà vẫn không thấy: kiểm tra `_topic.py --list` xem entry vào file nào.

**`SYNC-CONFLICT.md` xuất hiện hoài**

Bạn quên chạy `/mem-sync-resolve`. Conflict-detect hook sẽ nhắc ở mỗi prompt cho đến khi bạn resolve xong.

**Stop hook block hoài (boulder loop)**

State `ultrawork-state.json` còn sót. Chạy:
```bash
rm -rf ~/.claude/plugins/gowth-mem/.omc 2>/dev/null
```
Hoặc set `OMC_SKIP_HOOKS=ultrawork` trong shell rc.

**Multi-session deadlock**

Lock có timeout (30s/5s); không có deadlock thực sự. Nếu thấy `lock 'sync' held >30s`: 1 session khác đang push lâu — đợi hoặc xóa `~/.gowth-mem/.locks/sync.lock` (chỉ khi chắc không có session khác đang chạy).

**Push bị reject**

Token sai scope (cần `repo`), hoặc remote URL sai. Check `~/.gowth-mem/config.json` và `echo $GOWTH_MEM_GIT_TOKEN`.

## Files quan trọng (cho ai muốn đọc code)

| File | Mục đích |
|---|---|
| `hooks/scripts/_home.py` | Resolver `~/.gowth-mem/` (env override + v1.0 fallback) |
| `hooks/scripts/_lock.py` | fcntl.flock context manager |
| `hooks/scripts/_atomic.py` | tempfile + os.replace atomic write |
| `hooks/scripts/_topic.py` | Topic router (route/list/ensure/regen-index) |
| `hooks/scripts/_conflict.py` | Đóng gói SYNC-CONFLICT.md + reset working copy |
| `hooks/scripts/auto-sync.py` | Hook variant: pull-only / commit-only / full-sync |
| `hooks/scripts/_sync.py` | CLI variant cho `/mem-sync` |
| `hooks/scripts/conflict-detect.py` | UserPromptSubmit injector cho SYNC-CONFLICT.md |
| `hooks/scripts/bootstrap-load.py` | SessionStart loader |
| `hooks/scripts/recall-active.py` | UserPromptSubmit hybrid recall |
| `hooks/scripts/user-augment.py` | `<Rules>` injection + shortcut + intent |
| `hooks/scripts/precompact-flush.py` | PreCompact HARD-BLOCK distill instructions |
| `hooks/scripts/auto-journal.py` | Stop hook auto-distill mỗi 10 turn |
| `hooks/scripts/_index.py` | SQLite FTS5 + sqlite-vec indexer |
| `hooks/scripts/_prune.py` | Active DELETE outdated/superseded/duplicate |
| `hooks/scripts/_embed.py` | OpenAI/Voyage/Gemini embedding client |

## Out of scope (chưa làm trong v2.0)

- Per-topic embedding namespaces — index hiện tại là single chunks_vec table.
- Auto topic split detector — manual qua `/mem-promote`.
- Encrypted secrets.md — vẫn pointer-only.
- Cross-machine SRS state merge — `state.json` per-machine.
- Windows fcntl — POSIX-only.
- Real-time sync — chỉ trigger ở compact + SessionStart.

## License

MIT
