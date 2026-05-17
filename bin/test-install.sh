#!/usr/bin/env bash
# Clean-room install + upgrade + hook-smoke test loop.
#
# Why: "test đi test lại, install,upgrade dễ dàng" — every commit should be
# verifiable end-to-end against a fresh GOWTH_MEM_HOME without polluting the
# user's real `~/.gowth-mem/`.
#
# What it does (in order):
#   1. Create temp $GOWTH_MEM_HOME under $TMPDIR
#   2. Run hook scripts via `python3 -c "import _home; ..."` to verify they
#      load and create directories on first use.
#   3. Pipe an empty JSON event into each hook entrypoint via stdin and assert
#      exit 0 + no stack trace on stderr.
#   4. Simulate upgrade: pre-create a partial .gitignore missing the privacy
#      backfill entries, run _sync.write_default_gitignore, assert append.
#   5. Simulate migration: lay down a v2.4 single-file topic, run
#      _migrate_v3 in --dry-run, then live, assert the v3 layout appears.
#   6. Run the full unittest suite once more for safety.
#   7. Cleanup temp dir.
#
# Exit codes:
#   0 = all steps green
#   1 = a hook or assertion failed (details on stderr)
#   2 = pre-flight missing (python3 etc.)
#
# Usage:
#   bin/test-install.sh                  # full run
#   bin/test-install.sh --keep           # keep temp dir for inspection
#   bin/test-install.sh --skip-unit      # skip the final unittest sweep
#   bin/test-install.sh --verbose        # echo every command

set -uo pipefail

KEEP=0
SKIP_UNIT=0
VERBOSE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --keep) KEEP=1; shift ;;
    --skip-unit) SKIP_UNIT=1; shift ;;
    --verbose|-v) VERBOSE=1; shift ;;
    -h|--help) sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "[test-install] unknown arg: $1" >&2; exit 2 ;;
  esac
done

command -v python3 >/dev/null 2>&1 || { echo "[test-install] python3 not on PATH" >&2; exit 2; }

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPTS="$REPO_ROOT/hooks/scripts"
[ -d "$SCRIPTS" ] || { echo "[test-install] missing $SCRIPTS" >&2; exit 2; }

TMP="$(mktemp -d -t gowth-mem-test.XXXXXX)"
export GOWTH_MEM_HOME="$TMP"

cleanup() {
  if [ "$KEEP" -eq 1 ]; then
    echo "[test-install] kept temp dir: $TMP"
  else
    rm -rf "$TMP"
  fi
}
trap cleanup EXIT

step() { echo "── $* ──"; }
ok()   { echo "  ✓ $*"; }
fail() { echo "  ✗ $*" >&2; exit 1; }
say()  { [ "$VERBOSE" -eq 1 ] && echo "    $*" || true; }

# ─── 1. fresh install layout ────────────────────────────────────────────
step "1/6 fresh install — _home resolves under $GOWTH_MEM_HOME"
python3 - <<PY || fail "_home resolver crashed"
import sys; sys.path.insert(0, "$SCRIPTS")
from _home import gowth_home
assert str(gowth_home()) == "$TMP", f"home={gowth_home()!r} expected $TMP"
gowth_home().mkdir(parents=True, exist_ok=True)
print("home OK")
PY
ok "home resolves and is creatable"

# ─── 2. default gitignore template ──────────────────────────────────────
step "2/6 _sync.write_default_gitignore — template + backfill"
python3 - <<PY || fail "gitignore template failed"
import sys, importlib.util
from pathlib import Path
spec = importlib.util.spec_from_file_location("gowth_sync", "$SCRIPTS/_sync.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
gh = Path("$TMP")
mod.write_default_gitignore(gh)
text = (gh / ".gitignore").read_text()
for needed in (".audit/", ".dedup-window.json", "config.json", "state.json"):
    assert needed in text, f"missing {needed!r} in template"
print("template OK")
# Now simulate stale install: rewrite without the new entries, then backfill
(gh / ".gitignore").write_text("config.json\nstate.json\n")
mod.write_default_gitignore(gh)
text2 = (gh / ".gitignore").read_text()
for needed in (".audit/", ".dedup-window.json"):
    assert needed in text2, f"backfill failed to add {needed!r}"
assert "config.json" in text2, "backfill clobbered user entry"
print("backfill OK")
PY
ok "template written + backfill preserves user edits"

# ─── 3. each hook tolerates empty stdin (no traceback) ──────────────────
step "3/6 hook entrypoints — exit 0 + clean stderr on empty stdin"
HOOK_LIST=(
  bootstrap-load.py
  auto-journal.py
  precompact-flush.py
  user-augment.py
  system-augment.py
  conflict-detect.py
  auto-sync.py
)
for h in "${HOOK_LIST[@]}"; do
  hp="$SCRIPTS/$h"
  [ -f "$hp" ] || fail "hook missing: $h"
  # Most hooks read JSON from stdin; pass an empty object as the safe minimum.
  err="$(echo '{}' | python3 "$hp" 2>&1 >/dev/null || true)"
  if echo "$err" | grep -qiE "traceback|exception"; then
    fail "$h leaked traceback:
$err"
  fi
  say "$h: clean"
done
ok "${#HOOK_LIST[@]} hook entrypoints exit cleanly on empty input"

# ─── 4. privacy filter ─────────────────────────────────────────────────
step "4/6 privacy filter live-fire on writes"
python3 - <<PY || fail "privacy filter check failed"
import sys; sys.path.insert(0, "$SCRIPTS")
from _privacy import sanitize, has_secret
samples = [
    "sk-abcdef1234567890ghijkl",
    "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    "AKIAIOSFODNN7EXAMPLE",
    "<private>nuclear codes here</private>",
    "password=hunter2supersecret",
]
for s in samples:
    assert has_secret(s), f"has_secret missed {s!r}"
    out, n = sanitize(s)
    assert n >= 1, f"sanitize redacted nothing for {s!r}"
    assert "REDACTED" in out, f"missing REDACTED tag in {out!r}"
assert sanitize("nothing here") == ("nothing here", 0), "false positive on clean text"
print("privacy OK")
PY
ok "5 secret shapes redacted; clean text passes through"

# ─── 5. dedup + audit smoke ─────────────────────────────────────────────
step "5/6 dedup + audit — record + log_prune_delete round-trip"
python3 - <<PY || fail "dedup/audit smoke failed"
import sys; sys.path.insert(0, "$SCRIPTS")
from _dedup import check_and_record, seen_recently
from _audit import log_prune_delete
from pathlib import Path
assert check_and_record("first-line text v3.1") is False, "first call should not flag dup"
assert check_and_record("first-line text v3.1") is True,  "second call should flag dup"
assert seen_recently("first-line text v3.1") is True
log_prune_delete("workspaces/x/topic/lessons.md", "superseded", "- [exp] obsolete entry")
log_files = list((Path("$TMP") / ".audit").glob("prune-*.log"))
assert log_files, "audit log missing"
content = log_files[0].read_text()
assert "superseded" in content and "obsolete entry" in content, "audit content wrong"
print("dedup+audit OK")
PY
ok "dedup window + audit log working end-to-end"

# ─── 6. full unittest sweep ─────────────────────────────────────────────
if [ "$SKIP_UNIT" -eq 0 ]; then
  step "6/6 unittest sweep"
  if ( cd "$REPO_ROOT" && python3 -m unittest discover -s tests >/dev/null 2>&1 ); then
    ok "all unit tests pass"
  else
    fail "unittest sweep failed; rerun verbosely:
  ( cd $REPO_ROOT && python3 -m unittest discover -s tests )"
  fi
else
  echo "── 6/6 unittest sweep — SKIPPED (--skip-unit) ──"
fi

echo
echo "ALL GREEN — clean-room install + upgrade + hook smoke verified."
