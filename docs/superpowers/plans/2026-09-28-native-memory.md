# Native-first memory (v4.8.0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the vault's working set through Claude Code's auto-memory `MEMORY.md`, add gated per-prompt recall, cap every hook emission under the host's 10,000-char persist rule, and cut the judge relay and command listing — so Claude actually receives its memory at 1/3 of today's follow-on token cost.

**Architecture:** A pure renderer (`_memfile.py`) builds a deterministic managed block from handoff/topics/index into `<ws>/memory/MEMORY.md`; `_native.py` points each project's `autoMemoryDirectory` at that directory; the SessionStart hook shrinks to a header (or a ≤ 8,500-char fallback); a new UserPromptSubmit pair (`recall-on-prompt.sh` → `_recall_prompt.py`) injects ≤ 2,000 chars of BM25 hits only when a calibrated gate passes; the Stop hook regenerates the block, reindexes incrementally, sanitizes Claude-written memory files and dictates the judge prompt verbatim.

**Tech Stack:** Python 3.9+ stdlib only (sqlite3 FTS5), bash hooks, unittest, the real `claude` binary for E2E.

**Spec:** `docs/superpowers/specs/2026-09-28-native-memory-design.md`

## Global Constraints

- Python 3.9+ stdlib in `hooks/scripts/`; no pip. Tests must pass on 3.9 and the latest 3.x (`tests/test_py39_compat.py` guards syntax).
- Every hook entrypoint always exits 0; failures go to `_debug.log_debug`.
- Every hook emission passes `_home.clamp_context` (`HOOK_CONTEXT_MAX = 9000`).
- All vault writes: `_atomic.safe_write`/`atomic_write` + `_lock.file_lock`. Never write the real `~/.gowth-mem` in tests: every test sets `GOWTH_MEM_HOME` to a temp dir; every `claude -p` probe passes `--setting-sources project` and exports a scratch `GOWTH_MEM_HOME`.
- macOS/BSD tools in shell (no GNU-only flags); zsh is the user's shell, hooks run under bash.
- Frontmatter `description:` in commands/skills: ≤ 80 chars, no bare `: `, no ` #`.
- Test counts in CLAUDE.md / SHIPPED-FEATURES / the release commit are filled once, after the final review round.
- New delete/move paths on live data (`_native import`, settings writes into projects) run on copies until a fresh-context review approves, then only with the user's go-ahead.
- Commit after every task; no push until release.

## Review Focus

1. A `MEMORY.md` hand-edited by the user with the begin marker but no end marker → the whole file must be treated as free zone and preserved; the block is prepended (Task 3 test `test_begin_marker_without_end_is_free_zone`).
2. A prompt that is a pasted 40 KB log → `_recall_prompt` must cap the prompt at 2,000 chars before profiling and still finish in linear time (Task 7 growth probe).
3. `settings.local.json` containing a JSON comment or trailing comma (invalid JSON) → `_native.wire` must refuse (`invalid`) and never overwrite the file (Task 4 test `test_invalid_settings_local_is_left_alone`).
4. A workspace whose `docs/handoff.md` is 90 KB with no `##` headings (idol-ai) → the digest returns the first 60 non-blank lines and the block still fits (Task 2 test `test_headerless_handoff`).
5. Two machines regenerate `MEMORY.md` from the same synced sources → identical bytes, so git never conflicts on the block (Task 3 test `test_render_is_deterministic_across_processes`).

---

## Phase A — read side

### Task 1: `_home` limits, typed settings accessor, reserved `memory/`, broader workspace listing

**Files:**
- Modify: `hooks/scripts/_home.py` (add after `read_settings`, ~line 70; `RESERVED_SUBDIRS` line ~196; `list_workspaces` line ~411)
- Modify: `hooks/scripts/auto-journal.py:120-140` (`_coerce_bool` becomes an alias of `_home.coerce_bool`)
- Test: `tests/test_settings_accessor.py`, `tests/test_hook_limits.py`

**Interfaces:**
- Produces: `HOOK_CONTEXT_MAX: int = 9000`; `clamp_context(text: str, limit: int = HOOK_CONTEXT_MAX) -> str`; `coerce_bool(value, default: bool) -> bool`; `setting(path: str, kind: type = str, default=None, settings: dict | None = None)`; `RESERVED_SUBDIRS` now includes `"memory"`; `list_workspaces() -> list[str]` includes directories without `workspace.json` that hold `docs/`, `journal/` or a topic folder.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_settings_accessor.py
class SettingAccessorTest(unittest.TestCase):
    def test_string_false_is_false(self):
        s = {"reflection": {"enabled": "false"}}
        self.assertFalse(setting("reflection.enabled", bool, True, settings=s))
    def test_malformed_sibling_keeps_other_keys(self):
        s = {"reflection": {"enabled": False, "turn_interval": "15m"}}
        self.assertFalse(setting("reflection.enabled", bool, True, settings=s))
        self.assertEqual(setting("reflection.turn_interval", int, 15, settings=s), 15)
    def test_missing_path_returns_default(self):
        self.assertEqual(setting("recall.on_prompt_max_chars", int, 2000, settings={}), 2000)
    def test_null_means_default(self):
        self.assertTrue(setting("native.enabled", bool, True, settings={"native": {"enabled": None}}))

class ListWorkspacesTest(unittest.TestCase):   # GOWTH_MEM_HOME = temp dir
    def test_dir_without_workspace_json_but_with_docs_is_listed(self): ...
    def test_underscore_dirs_excluded(self): ...
    def test_memory_is_reserved(self):
        self.assertTrue(is_reserved("memory"))

# tests/test_hook_limits.py
class ClampContextTest(unittest.TestCase):
    def test_short_text_unchanged(self): self.assertEqual(clamp_context("x" * 8999), "x" * 8999)
    def test_cuts_at_line_boundary_with_marker(self):
        text = "\n".join(["L" * 80] * 200)          # 16,199 chars
        out = clamp_context(text)
        self.assertLess(len(out), 9000)
        self.assertTrue(out.endswith("[gowth-mem: truncated to fit the host limit]"))
        self.assertTrue(out.split("\n")[-2] == "L" * 80)  # no half line
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m unittest tests.test_settings_accessor tests.test_hook_limits -v`
Expected: FAIL with ImportError / AttributeError on `setting`, `clamp_context`.

- [ ] **Step 3: Implement in `_home.py`**

`setting()` walks the dotted path through nested dicts; a missing key, `None`, or a value of the wrong kind returns `default` for THAT key only. `bool` → `coerce_bool` (accepts bool, `"true"/"false"/"1"/"0"/"yes"/"no"` case-insensitive; anything else → default). `int` → `int()` on int/str, `ValueError` → default. `clamp_context` cuts at the last `\n` at or before `limit - 60`, then appends `\n[gowth-mem: truncated to fit the host limit]`. `list_workspaces`: a dir qualifies when it has `workspace.json`, or any of `docs/`, `journal/`, or a child for which `is_topic_folder` is true. Keep the `_`-prefix exclusion.

- [ ] **Step 4: Point `auto-journal._coerce_bool` at `_home.coerce_bool`, run the full suite**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -3`
Expected: OK (existing `TestV471BoolCoercion` still green).

- [ ] **Step 5: Write the failing knob tests** — one test per bare boolean read (holistic review M1): `sync.auto_sync_on_stop` (`_sync.py:318`), `topic_layout.auto_archive_enabled` and `journal.auto_forget_enabled` (`_forget.py:434, 454-455`), `reflection.capture_thinking` (`_capture.py:618`), `gate.enabled` / `gate.strict` / `gate.english_only` (`_gate.py:153, 158`), `tags.enabled` (`_tags.py:564`), `retrieval.index_archive` (`_index.py:759`), `retrieval.use_budget_planner` / `context_budget.enabled` (`bootstrap-load.py:183, 186`), `workspace.auto_detect_from_cwd` (`_home.py:133`), plus the section reads in `_topic.py:677` and `_lesson.py:113`. Each test writes `settings.json` with the knob as the string `"false"` into the temp vault and asserts the feature is OFF through its public function (e.g. `maybe_autosync(dry_run=True)["skipped"]`, `_gate.check(...)` returning accepted, `_tags.extract(...)` returning `[]`).

- [ ] **Step 6: Run** → FAIL on every knob (string `"false"` is truthy today). **Step 7: Route each read through `setting(path, bool, default, settings=...)`** (or `coerce_bool`) at the listed lines. **Step 8: Run** the new tests and the full suite → PASS.

- [ ] **Step 9: Commit**

```bash
git add hooks/scripts/*.py tests/test_settings_accessor.py tests/test_hook_limits.py
git commit -m "feat(home): typed setting accessor, clamp_context, memory/ reserved, list_workspaces without workspace.json; string false is false everywhere"
```

### Task 2: Handoff digest

**Files:**
- Modify: `hooks/scripts/_handoff.py` (add `digest` after `_rotate_stale_bullets`)
- Test: `tests/test_handoff_digest.py`

**Interfaces:**
- Consumes: `_split_sections`, `_section_date_key`, `_split_bullet_items`, `LIVE_STATUS_RE` (existing).
- Produces: `digest(ws: str, max_lines: int = 60, max_line_chars: int = 160) -> list[str]` — lines without trailing newlines, newest dated section first, non-dated sections after in file order, blank lines dropped, each line cut to `max_line_chars - 1` + `…`; in the newest section, `- host:` bullets matching `LIVE_STATUS_RE` come before the others; a file with no `##` yields its first `max_lines` non-blank lines.

- [ ] **Step 1: Write the failing tests** — fixtures: (a) newest-first file with three dated sections; (b) mixed order (2026-09-11 section at top, 2026-09-13 appended at the end) → first emitted header is the 09-13 one; (c) headerless 52 KB body → 60 lines, none blank; (d) a 400-char bullet → 160 chars ending in `…`; (e) `[done]` bullet before `[blocker]` in the source → `[blocker]` emitted first.

- [ ] **Step 2: Run to verify failure** — `python3 -m unittest tests.test_handoff_digest -v` → ImportError on `digest`.

- [ ] **Step 3: Implement `digest`** in `_handoff.py` using the existing splitters; sort dated sections by `_section_date_key` descending (stable sort, so equal dates keep file order).

- [ ] **Step 4: Run tests** → PASS. **Step 5: Commit** `feat(handoff): newest-first digest for the memory block`.

### Task 3: `_memfile.py` — the managed block

**Files:**
- Create: `hooks/scripts/_memfile.py`
- Test: `tests/test_memfile.py`

**Interfaces:**
- Consumes: `_home` (`workspace_dir`, `iter_topic_landings`, `slug_for_path`, `secrets_md`, `docs_dir`, `setting`), `_frontmatter.parse_file`, `_handoff.digest`, `_query.query_by_type(ws, "decision", "", limit=10, days=7)` (empty query = most recent first), `_version.version_tag/drift_nudge`, `_atomic.safe_write`, `_lock.file_lock`.
- Produces:
  - `BEGIN_FMT = "<!-- gowth-mem:begin ws={ws} -->"`, `END = "<!-- gowth-mem:end -->"`, `MAX_LINES = 130`, `MAX_CHARS = 12000`, `FLOOR_LINES = 28`, `HOST_LINE_LIMIT = 200`
  - `memory_dir(ws: str) -> Path` (= `workspace_dir(ws) / "memory"`), `memfile_path(ws) -> Path`
  - `rules_digest() -> list[str]` (≤ 25 fixed lines, module constant)
  - `topic_index(ws: str, max_lines: int) -> list[str]`
  - `recent_decisions(ws: str, days: int = 7, max_lines: int = 10) -> list[str]`
  - `secret_pointers(max_lines: int = 8) -> list[str]`
  - `render(ws: str, *, max_lines: int = MAX_LINES, max_chars: int = MAX_CHARS, free_zone_lines: int = 0) -> str` (block including markers, trailing newline)
  - `split(text: str) -> tuple[str, str]` → `(block, free_zone)`; no end marker ⇒ `("", text)`
  - `write(ws: str) -> bool` (True when the file changed)
  - `sources_changed(ws: str) -> bool`
  - `render_hook_bootstrap(ws: str, max_chars: int = 8500) -> str` (no markers; header line, Handoff, Rules, Topics, Recent decisions, Secrets, Using memory, in that order)

- [ ] **Step 1: Write the failing tests** (temp vault with 3 topics, a handoff, `shared/secrets.md` holding `` `FAKE_API_KEY` `` and a line `FAKE_TOKEN=abc123` that must never appear):

```python
def test_block_within_budget(self): block = render("demo"); assert block.count("\n") <= 130 and len(block) <= 12000
def test_sections_present_in_order(self): for h in ["## Rules", "## Handoff", "## Topics", "## Recent decisions", "## Secrets (pointers only)", "## Using memory"]: ...
def test_secret_values_never_emitted(self): self.assertIn("FAKE_API_KEY", block); self.assertNotIn("abc123", block)
def test_shrink_order(self): render(..., free_zone_lines=100) drops Recent decisions before Topics before Handoff
def test_floor_is_28_lines(self): render(..., free_zone_lines=185) → block lines == 28 (Rules + Using memory + markers/header)
def test_free_zone_preserved_byte_for_byte(self): write(); append "- my note\n" below END; write() → note intact
def test_begin_marker_without_end_is_free_zone(self): file = BEGIN + "\nstale\n" → after write(), "stale" still present below the new END
def test_hash_gate_no_rewrite(self): write() twice → second returns False and mtime unchanged
def test_render_is_deterministic_across_processes(self): subprocess renders twice → identical bytes, no timestamps/hostname
def test_sources_changed_tracks_handoff_mtime(self)
def test_hook_bootstrap_under_8500_and_handoff_first(self)
```

- [ ] **Step 2: Run** `python3 -m unittest tests.test_memfile -v` → ImportError.

- [ ] **Step 3: Implement `_memfile.py`** — shrink algorithm: build sections at full size; while total lines > budget: cut Recent decisions to 0, then Topics down to 10, then Handoff down to 20; then, while total chars > `max_chars`, drop the last line of the largest shrinkable section. Budget = `min(max_lines, HOST_LINE_LIMIT - 10 - free_zone_lines)`, never below `FLOOR_LINES`. `topic_index` summary = frontmatter `summary`/`title` or the first body line that is not a heading, ≤ 100 chars. `secret_pointers` extracts backticked tokens matching `^[A-Z][A-Z0-9_]{3,}$`, unique, grouped 6 per line. `write` locks `file_lock(f"memfile-{ws}")`, reads, `split`, compares `hashlib.sha1` of the old vs new block, writes `block + free_zone` via `safe_write`.

- [ ] **Step 4: Run tests** → PASS. **Step 5: Commit** `feat(memfile): deterministic MEMORY.md managed block`.

### Task 3b: `MEMORY.md` merges itself on a sync conflict

**Files:**
- Modify: `hooks/scripts/_conflict.py:39-60` (`package_conflict`), `hooks/scripts/_sync.py:186-197, 235-243`
- Test: `tests/test_memfile_sync_merge.py`

**Interfaces:**
- Consumes: `_memfile.split/render`, `_git` helper in `_conflict`.
- Produces: `merge_memfile(gh: Path, rel: str) -> bool` in `_conflict.py`: for a conflicted path matching `workspaces/<ws>/memory/MEMORY.md`, reads `:2` and `:3` (`_show`), takes the union of both free zones (line-exact dedupe, local lines first), regenerates the block with `_memfile.render(ws)`, writes the file, `git add`s it, returns True. `package_conflict()` calls it for each such path first; when no conflicted files remain it runs `git -c core.editor=true rebase --continue` and returns `None` instead of the `SYNC-CONFLICT.md` path. `_sync.py` treats `None` as "resolved" and proceeds to push instead of returning 2.

- [ ] **Step 1: Write the failing tests** — two temp clones of a bare repo; each appends a different free-zone line to `workspaces/demo/memory/MEMORY.md`; clone B pulls with rebase after A pushed → `package_conflict()` returns `None`, the file holds both lines below one block, no `SYNC-CONFLICT.md`; a second conflict on `docs/handoff.md` at the same time → `SYNC-CONFLICT.md` lists only `handoff.md`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS; `tests/test_regressions.AutoSyncRebaseTests` still green. **Step 5: Commit** `feat(sync): MEMORY.md free zones merge on conflict, block regenerated`.

### Task 4: `_native.py` — project wiring, import, status

**Files:**
- Create: `hooks/scripts/_native.py`
- Test: `tests/test_native.py`

**Interfaces:**
- Consumes: `_home.read_config` (`workspace_map`, `active_workspace`), `_memfile.memory_dir`, `_privacy.sanitize`, `_atomic.atomic_write`.
- Produces:
  - `settings_local_path(project_dir: Path) -> Path` (`<project>/.claude/settings.local.json`)
  - `memory_dir_value(ws: str) -> str` (`~/`-prefixed when under `$HOME`, else absolute)
  - `wire(project_dir: Path, ws: str, *, force: bool = False) -> str` ∈ `{"wired","already","conflict","tracked","invalid"}`
  - `is_wired(project_dir: Path, ws: str, env=None) -> bool` (reads `settings.local.json` then `settings.json`; expands `~`; compares resolved paths; false when `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` or `autoMemoryEnabled` is false in `<CLAUDE_CONFIG_DIR or ~/.claude>/settings.json`)
  - `projects_for_workspaces(config: dict, cwd: Path | None = None) -> list[tuple[Path, str]]`
  - `import_native(claude_dir: Path, *, apply: bool = False) -> dict` with keys `imported`, `renamed`, `skipped_unmapped`, `index_lines_added`
  - `status(cwd: Path | None = None) -> dict`
  - CLI: `python3 _native.py wire [--dry-run] [--force] [--project DIR --ws WS]`, `import [--apply]`, `status`; exit 0 always, human-readable lines.

- [ ] **Step 1: Write the failing tests** — temp project dirs; a temp git repo where `.claude/settings.local.json` is committed (`tracked`); an existing file with other keys preserved; `conflict` vs `--force`; `test_invalid_settings_local_is_left_alone` (file content `{bad json,}` → `"invalid"`, bytes unchanged); `is_wired` true/false cases incl. `CLAUDE_CONFIG_DIR` settings with `autoMemoryEnabled: false`; import dry-run creates nothing, apply copies + sanitizes (`AKIA…` fixture redacted), clash renamed `<name>.from-<host>.md`, unmapped slug listed, `MEMORY.md` index lines appended once (idempotent second run adds 0).

- [ ] **Step 2: Run** → ImportError. **Step 3: Implement.** Slug for a project path = `re.sub(r"[^A-Za-z0-9]", "-", str(path))` (matches Claude Code's `~/.claude/projects/<slug>`); mapping = the `workspace_map` glob whose base dir equals the slug's path.

- [ ] **Step 4: Run tests** → PASS. **Step 5: Commit** `feat(native): wire autoMemoryDirectory per project, import native memory, status`.

### Task 5: SessionStart — header / fallback / compact / clear

**Files:**
- Modify: `hooks/scripts/bootstrap-load.py` (replace `_plan/_allocate/_format_block` path with `_memfile`; keep `--report`)
- Modify: `hooks/scripts/session-start.sh:41-44` (add `clear`; pass stdin through)
- Modify: `hooks/scripts/_workspace.py` (add `ensure_workspace_json(name: str) -> bool`)
- Modify: `tests/test_bootstrap_budget.py` (retarget assertions to the fallback: handoff marker present, output < 9,000, handoff before rules)
- Test: `tests/test_bootstrap_native.py`

**Interfaces:**
- Consumes: `_native.is_wired`, `_memfile.write/render_hook_bootstrap/memfile_path/topic_index`, `_handoff.digest`, `_home.clamp_context/setting`, `_version.version_tag/drift_nudge`.
- Produces: `bootstrap-load.py` reads stdin JSON (`source`, `cwd`); `mode(ws, cwd, settings) -> "native" | "fallback"`; `emit(ws, source, mode) -> str`; `--report` prints the old table plus `memfile: <bytes> chars / <lines> lines, free zone <n> lines, delivered mode=<mode>`; `ensure_workspace_json(name) -> bool` writes only `workspace.json` when the workspace dir exists without it.

- [ ] **Step 1: Write the failing tests** (`tests/test_bootstrap_native.py`, subprocess like `test_bootstrap_budget`, stdin `{"source": ..., "cwd": <temp project>}`):

```python
def test_native_mode_prints_header_only(self): wire temp project → output < 600 chars, contains "[gowth-mem:bootstrap workspace=demo", contains "MEMORY.md"
def test_fallback_when_not_wired(self): output contains "## Handoff" before "## Rules", len < 9000
def test_compact_delta_under_1500(self)
def test_clear_emits_output(self)          # session-start.sh with source=clear
def test_resume_emits_nothing(self)
def test_oversized_vault_never_exceeds_9000(self)   # 200 KB handoff + 60 topics
def test_startup_creates_missing_workspace_json(self)
def test_startup_writes_memfile_when_missing(self)
def test_report_lists_memfile_line(self)
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** `session-start.sh`: condition becomes `startup|compact|clear|empty`; keep the detached doctor and the pull. In `bootstrap-load.main`: parse stdin; `ws = active_workspace(Path(cwd))`; on `startup`/`clear`: `ensure_workspace_json(ws)`, `_memfile.write(ws)` if missing, `subprocess.Popen([python3, _index.py, "--incremental"], start_new_session=True, stdout/stderr DEVNULL)`; then emit per mode/source, always through `clamp_context`, still as the `hookSpecificOutput` JSON envelope.

- [ ] **Step 4: Run** `python3 -m unittest tests.test_bootstrap_native tests.test_bootstrap_budget tests.test_hook_wrappers -v` → PASS; full suite OK. **Step 5: Commit** `feat(session-start): native header, fallback bootstrap ≤8.5k, compact delta, clear`.

### Task 6: Query excludes and incremental index

**Files:**
- Modify: `hooks/scripts/_query.py` (`query_ex`, `_run_query`, CLI flag `--include-research`)
- Modify: `hooks/scripts/_index.py` (`incremental()`, CLI `--incremental`)
- Modify: `commands/mem-recall.md` (defaults, weights, `--include-research`)
- Test: `tests/test_query_excludes.py`, `tests/test_index_incremental.py`

**Interfaces:**
- Produces: `query_ex(..., exclude: tuple[str, ...] = (), include_research: bool = False)`; `DEFAULT_EXCLUDES = ("research/", "docs/handoff-archive.md")` applied unless `include_research`; each exclude becomes `AND c.path NOT LIKE ?` with `%/<prefix>%`. `_index.incremental(max_files: int = 200) -> dict(files=int, dropped=int)` reindexes sources whose mtime differs from the stored row (or have none), also drops rows for paths no longer on disk (bounded by the same cap); CLI `--incremental` prints `incremental: <files> files, <dropped> dropped`.

- [ ] **Step 1: Write the failing tests** — index a temp vault with a topic aspect and a `research/x.md` sharing a term: default query returns only the aspect; `include_research=True` returns both; `exclude=("journal/",)` hides a journal hit. Incremental: new file indexed, modified file re-indexed (row count changes), 201 changed files → 200 indexed, deleted file's rows dropped, workspace without `workspace.json` indexed (uses Task 1 listing).

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** (reuse `_index_one`, `_drop_path_rows`, `_collect_sources`). **Step 4: Run** → PASS; full suite OK. **Step 5: Commit** `feat(index,query): incremental reindex, research excluded from default recall`.

### Task 7: Per-prompt recall hook

**Files:**
- Create: `hooks/scripts/recall-on-prompt.sh`, `hooks/scripts/_recall_prompt.py`
- Modify: `hooks/hooks.json` (UserPromptSubmit: second entry `bash "${CLAUDE_PLUGIN_ROOT}/hooks/scripts/recall-on-prompt.sh"`)
- Modify: `bin/test-install.sh:117-122` (HOOK_LIST += `_recall_prompt.py`)
- Test: `tests/test_recall_prompt.py`

**Interfaces:**
- Consumes: `_profile.keywords_of/profile/fts_match`, `_query.query_ex`, `_home.setting/clamp_context/active_workspace/state_path`, `_lock.file_lock`.
- Modifies: `_query._run_query` result dicts gain `"id": <chunks.id>` (both branches, lines ~299 and ~312); `hooks/scripts/conflict-detect.py` passes its message through `clamp_context` (a 30-line preview of long lines could cross 10,000 chars).
- Produces:
  - Settings (flat under `recall`, greppable): `on_prompt_enabled` (bool, true), `on_prompt_max_entries` (3), `on_prompt_max_chars` (2000), `on_prompt_min_terms` (2), `on_prompt_score_threshold` (float; default set by Task 12 calibration, initial `-4.0`), `on_prompt_prompt_cap` (2000 chars).
  - `select(prompt: str, ws: str, settings: dict, injected: set[int]) -> list[dict]` (pure; hits carry `id`, `path`, `tag`, `heading`, `content`, `bm25_score`)
  - `format_block(ws: str, hits: list[dict], max_chars: int) -> str` → `[gowth-mem:recall ws=<ws>] related memory (read the file for more):` + `- <path> [<tag>] <heading> — <snippet ≤ 700>` lines
  - `main() -> int`: stdin JSON (`prompt`, `cwd`, `session_id`); empty stdout when nothing qualifies; else the UserPromptSubmit `hookSpecificOutput` envelope; records ids in `state.json.session[<sid>].recall = {"prompts", "injected", "chars", "ids": [...≤300]}` under `file_lock("state", timeout=2)` best-effort.
  - `recall-on-prompt.sh` exits 0 silently when: settings has `"on_prompt_enabled"[[:space:]]*:[[:space:]]*false`; the prompt (sed-extracted first 200 bytes of `"prompt":"…"`) starts with `/` or `!` or is < 40 bytes; stdin has `"agent_type"[[:space:]]*:[[:space:]]*"subagent"` or `"in_loop"[[:space:]]*:[[:space:]]*true`; or `CLAUDE_SUBAGENT` is set. Otherwise `exec python3 _recall_prompt.py <<<"$INPUT"`.

- [ ] **Step 1: Write the failing tests** — seeded temp vault + index (10 entries with distinctive terms). Bash gate cases via subprocess. `select`: a seeded query returns the source path first; a prompt sharing one term only → `[]`; `injected` containing the hit id → `[]`; caps (entries 3, chars 2000, snippet 700); `test_generic_prompts_inject_nothing` over the 20 prompts `["continue", "ok làm đi", "fix the tests", "tiếp đi", "release", "commit and push", "what's next?", "run it again", "yes", "no, revert that", "explain", "làm lại", "check again", "why?", "thanks", "show me the diff", "format the file", "add a comment", "rename it", "ok"]`; `test_prompt_is_capped_before_profiling` (40 KB prompt → `keywords_of` sees ≤ 2,000 chars); growth probe: `select` on 10k/20k/40k prompts of the four shapes (unclosed openers `((((`, whitespace runs, repeated `---`, `token-` floods) each ≤ 2× the previous time; `test_empty_stdin_exits_0_silently`; telemetry written.

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** Term match = count of `keywords_of(prompt[:cap])` terms found (case-insensitive substring) in `heading + " " + content`. Gate = terms ≥ min_terms AND `bm25_score <= threshold` AND id not injected. Query = `fts_match(profile(prompt[:cap]))`, `query_ex(ws, "", query, limit=8, exclude=("journal/", "memory/MEMORY.md"))`.

- [ ] **Step 4: Run** → PASS; `bash bin/test-install.sh` green. **Step 5: Commit** `feat(recall): gated per-prompt recall on UserPromptSubmit`.

## Phase B — Stop hook

### Task 8: Judge directive, settings snapshot, post-turn maintenance

**Files:**
- Modify: `hooks/scripts/auto-journal.py` (`_build_review_reason` ~line 368; `_read_journal_settings`/`_read_reflection_settings`/`_capture_enabled` take `settings`; `main` after `_autosync()`; new `_post_turn(ws, settings)`)
- Modify: `templates/self-review-instructions.md` (3-line contract after line 1 and in §6; §0 step 1 quotes the verbatim prompt)
- Modify: `tests/test_dispatch_contract.py`, `tests/test_review_trigger.py`
- Test: `tests/test_stop_post_turn.py`

**Interfaces:**
- Produces: `_stop_output(reason)` passes `reason` through `clamp_context` in both envelopes. `_run_forget_daily()` additionally starts a detached `python3 _index.py --full` (Popen, `start_new_session=True`, output to DEVNULL) once per calendar day, stamped in `state.json.index_last_full`. Review directive text = `[gowth-mem:self-review ws=<ws>] <n> turns logged. DISPATCH a fresh-context background subagent (NOT a fork; do NOT load review skills in the main context) whose prompt is: "You are the dispatched gowth-mem judge — never dispatch further subagents. Read <instructions_path> and judge <log_ref>; scores go to <scores_path>. Write the full report with python3 <_capture.py path> --append-review (use the session-insights format there if that skill is available to you). Your FINAL message is exactly 3 lines: scores / top friction / one rule." Relay those 3 lines and nothing more.` + backlog stat; ≤ 1,500 chars with two log paths. `_post_turn(ws: str, settings: dict) -> None`: `_memfile.write(ws)` when `setting("native.enabled", bool, True)` and `sources_changed(ws)`; `_index.py --incremental` subprocess (timeout 8) when `sources_changed` or `state.index_last_incremental` older than 600 s (stamp after); sanitize each `<ws>/memory/*.md` with mtime newer than `state.memory_sanitized_at` via `safe_write` (only when `_privacy.sanitize` count > 0), then stamp.

- [ ] **Step 1: Write the failing tests** — contract: hook source contains the judge sentinel and "exactly 3 lines"; template contains the contract in its first 5 lines and in §6; directive with two paths < 1,500 chars; `_stop_output("x" * 12000)["hookSpecificOutput"]["additionalContext"]` is < 9,000 chars and ends with the clamp marker; the daily slot starts `_index.py --full` at most once per day (patch `subprocess.Popen`, two Stops on the same day → one call). Post-turn (subprocess Stop with a synthetic transcript, as `test_review_trigger` does): handoff modified → `MEMORY.md` block changes; unchanged → mtime stable; `memory/note.md` containing `glpat-` + 20 chars → redacted after Stop; new aspect file → appears in `index.db` after Stop; `read_settings` called once per Stop (patch counter in-process via `importlib` load of the module).

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `python3 -m unittest tests.test_dispatch_contract tests.test_review_trigger tests.test_stop_channel tests.test_stop_post_turn -v` → PASS; full suite OK. **Step 5: Commit** `feat(stop): verbatim judge prompt, one settings snapshot, memfile/reindex/sanitize after each turn`.

### Task 9: precompact-flush uses the capture classification

**Files:**
- Modify: `hooks/scripts/precompact-flush.py:67-160` (`user_turn_count`, `extract_recent_turns`)
- Modify: `tests/test_precompact_raw_dump.py`

**Interfaces:**
- Consumes: `_capture._classify_record`, `_capture.prompt_text`.
- Produces: `user_turn_count` counts records classified `"human"`; `extract_recent_turns` emits `### [user]` only for human records (text via `prompt_text`) and caps each `### [assistant]` chunk at 500 chars (`… [+N chars]`); the 20 KB cap unchanged.

- [ ] **Step 1: Write the failing tests** — transcript fixture with a human record (`origin.kind = "human"`), a `<task-notification>` user record, a `queued_command` attachment, and a 3 KB assistant record → count = 2, dump has no `<task-notification`, assistant chunk ≤ 520 chars.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS. **Step 5: Commit** `fix(precompact): dump human turns only, cap assistant chunks`.

## Phase C — surface, settings, docs

### Task 10: Command surface 39 → 15, skills 13 → 6

**Files:**
- Create: `commands/mem-ops.md` (dispatcher), `commands/mem-research.md`, `templates/ops/<sub>.md` × 20 (moved bodies), `skills/mem-recall/SKILL.md`
- Delete: 20 `commands/mem-*.md` (budget changelog compress config dream forget gate journal lint migrate-global migrate-v3 promote prune reflect reindex restructure retag review-backlog skillify validate verify), `commands/mem-research-{start,distill,status}.md`, `commands/mem-sync-resolve.md` (folded into `commands/mem-sync.md` § `resolve`), skills `mem-config mem-cost mem-migrate-global mem-promote mem-prune mem-reflect mem-reindex mem-skillify`
- Modify: every reference (`grep -rn "/mem-" commands skills templates hooks README.md CLAUDE.md docs/SHIPPED-FEATURES.md`), `hooks/scripts/conflict-detect.py` text → `/mem-sync resolve`
- Test: `tests/test_command_surface.py`

**Interfaces:**
- Produces: exactly these `commands/*.md`: `mem-cost mem-distill mem-doctor mem-goal mem-handoff mem-install mem-lesson mem-ops mem-recall mem-research mem-review mem-save mem-setup mem-sync mem-topic mem-workspace` (16 — `mem-ops` is the 16th; the spec's "15" counts user-facing commands, `mem-ops` is the umbrella). `mem-ops.md` frontmatter description ≤ 80 chars; body: `$ARGUMENTS` first word selects `templates/ops/<sub>.md`, which the model reads and follows; unknown sub → list subs.

- [ ] **Step 1: Write the failing test** — `test_command_surface.py`: `len(glob("commands/*.md")) <= 16`; every description ≤ 80 chars, no `: ` after the first colon-space of the key, no ` #`; `len(glob("skills/*/SKILL.md")) <= 7`; every `/mem-<name>` token in the scanned files is either an existing command or `/mem-ops <sub>` with `templates/ops/<sub>.md` present; `templates/ops/` has one file per sub named in `mem-ops.md`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Move/merge with `git mv`, write dispatcher, fix references, shorten descriptions.** **Step 4: Run** → PASS; full suite OK (`test_version_drift` reference guard still green). **Step 5: Commit** `refactor(commands): 39 → 16 commands, 13 → 6 skills, ops umbrella`.

### Task 11: Settings example, cost/doctor, docs

**Files:**
- Modify: `templates/dot-gowth-mem/settings.example.v3.json` (only keys the code reads + new keys; `archive_threshold_days: 90`; `_notes` trimmed to those keys)
- Modify: `commands/mem-cost.md` (memfile + recall telemetry lines), `commands/mem-doctor.md` (`python3 _native.py status`), `commands/mem-setup.md` (`native` subcommand: `wire --dry-run` → `wire`; `import`)
- Modify: `templates/AGENTS.md` §3 (what is loaded: MEMORY.md block + header + gated recall; how to reach topics) and §10 (remove the formula), `README.md:255-290`, `CLAUDE.md` (Key Decisions: MEMORY.md channel + the 10,000-char host rule; Anti-patterns: any hook emission ≥ 9,000 chars, `claude -p` in a test/probe without `--setting-sources project` + scratch `GOWTH_MEM_HOME`; Commands: 16 commands / 6 skills, `tests/e2e/native_e2e.sh`; hook table: UserPromptSubmit runs `conflict-detect.sh` then `recall-on-prompt.sh`; note that `.claude/research/` is gitignored and local-only; remove SRS/MMR/"≤60k"), `hooks/scripts/precompact.sh` comments, `docs/SHIPPED-FEATURES.md` (v4.8.0 section, counts left as `TBD-COUNT` until Task 12)
- Test: extend `tests/test_settings_accessor.py` with `test_example_keys_are_all_read` (every leaf key path in the example appears as a string literal in `hooks/scripts/*.py`, or as `on_prompt_*`/`native`/`memfile` keys) and `test_code_keys_are_all_documented` (the 36 + new keys appear in the example).

- [ ] **Step 1: Write the failing tests.** **Step 2: Run** → FAIL (51 dead keys). **Step 3: Rewrite the example and the docs.** **Step 4: Run** → PASS; `python3 -m py_compile hooks/scripts/*.py`; full suite OK. **Step 5: Commit** `docs(settings): truthful settings example, AGENTS §3, CLAUDE.md, cost/doctor/setup`.

### Task 12: Verification, calibration, review, release

**Files:**
- Create: `tests/e2e/native_e2e.sh` (skips with exit 0 when `claude` is not on PATH; referenced from CLAUDE.md Commands), `tests/probes/recall_probe.py`, `tests/probes/tokens_per_100.py`
- Modify: `templates/dot-gowth-mem/settings.example.v3.json` (`on_prompt_score_threshold` calibrated value + date), `docs/SHIPPED-FEATURES.md`, `CLAUDE.md` (counts)

- [ ] **Step 1: Write `tests/probes/recall_probe.py`** — args: `--vault <copy>`; samples the audit's 10 (seed 42) + 20 (seed 43) `[decision]/[ref]` entries, paraphrases titles by dropping stopwords and keeping 3–6 content words, runs `_recall_prompt.select` and `_query.query_ex`, prints hit@1/hit@3/MRR, precision of injected, injections on the 20 generic prompts, and the largest threshold with 0 generic injections and precision ≥ 0.8.
- [ ] **Step 2: Run it on an rsync copy of the live vault** (`GOWTH_MEM_HOME=<scratch>/vault-copy`, `.git` renamed aside, `remote`/`token` removed from `config.json`) → record numbers; set `on_prompt_score_threshold` in the example and in `_recall_prompt` default.
- [ ] **Step 3: Write and run `tests/e2e/native_e2e.sh`** — scratch project + scratch vault; steps §7.2 of the spec; prints PASS/FAIL per step; all PASS.
- [ ] **Step 4: Write and run `tests/probes/tokens_per_100.py`** — replays the audit's method (A3 table) against the copy with v4.8 hooks; prints hook-injected and follow-on chars per 100 turns; follow-on ≤ 60k.
- [ ] **Step 5: `bin/test-install.sh`; full suite on 3.9 and 3.14; `py_compile`** → all green.
- [ ] **Step 6: Fresh-context review** of `git diff v4.7.6..HEAD` (Agent, read-only, `GOWTH_MEM_HOME` scratch) against the spec; fix every HIGH/MEDIUM; re-run steps 3–5.
- [ ] **Step 7: Fill counts** (tests, commands, skills) in CLAUDE.md, SHIPPED-FEATURES, then `bin/release.sh minor`; `claude plugin update gowth-mem -y`; end the turn with the `/reload-plugins` request.
- [ ] **Step 8 (after reload, user go-ahead each):** `/mem-setup native --dry-run` → `/mem-setup native`; `/mem-setup native --import --dry-run` → `--import`; verify a new session's `MEMORY.md` attachment and `/mem-cost`.
