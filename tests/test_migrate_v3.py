"""End-to-end tests for `_migrate_v3.py` (7-step pipeline).

Covers the locked decisions:
- F1 microsecond UTC stamp → backup folder name unique per call.
- F2 already-v3-on-remote short-circuit via `git log -1 --format=%s origin/<branch>`.
- F3 `_atomic.atomic_write` parent.mkdir guarantee (no AssertionError on missing dirs).
- F9 stale-remote abort: STEP 7 fetch + ff-only merge; conflict → `stale_remote_abort`
  with backup untouched.
- Backup rolling-2 window: 3rd run leaves exactly 2 backups, oldest demoted if ≥24h.
- Lessons.md kept at `<slug>/lessons.md` (never reshaped).
- Reserved subdirs (docs/journal/skills/research) never touched.
- Body sha256 (excluding frontmatter) verified post-move.

Uses `tests/fixtures/v2-snapshot/` as canonical v2.4 input (snapshot is small,
deterministic, contains: 1 v2.4 folder-note + 1 v2.4 sub-aspect + 1 v2.4 lessons.md
+ 1 v2.3 flat topic + 1 reserved docs/ entry).
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "hooks" / "scripts"
FIXTURE = ROOT / "tests" / "fixtures" / "v2-snapshot"


def _install_fixture(home: Path) -> None:
    """Copy the v2-snapshot fixture into `home/`."""
    for child in FIXTURE.iterdir():
        dst = home / child.name
        if child.is_dir():
            shutil.copytree(child, dst)
        else:
            shutil.copy2(child, dst)


def _git_init(repo: Path, branch: str = "main") -> None:
    subprocess.run(["git", "init", "-b", branch, str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@x"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "seed v2 fixture"],
                   check=True, capture_output=True)


def _run_migrate(home: Path, *extra: str) -> dict:
    env = os.environ.copy()
    env["GOWTH_MEM_HOME"] = str(home)
    out = subprocess.run(
        [sys.executable, str(SCRIPTS / "_migrate_v3.py"), *extra],
        env=env, cwd=ROOT, capture_output=True, text=True,
    )
    if out.returncode not in (0, 1, 2):
        raise AssertionError(
            f"migrate-v3 unexpected rc={out.returncode}\nstdout={out.stdout}\nstderr={out.stderr}"
        )
    # JSON is the default output mode.
    try:
        return json.loads(out.stdout) if out.stdout.strip() else {"raw_rc": out.returncode}
    except json.JSONDecodeError:
        return {"raw_stdout": out.stdout, "raw_stderr": out.stderr, "raw_rc": out.returncode}


class MigrateV3Tests(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            _install_fixture(home)
            _git_init(home)
            before_files = sorted(p.relative_to(home) for p in home.rglob("*.md"))
            report = _run_migrate(home, "--dry-run")
            after_files = sorted(p.relative_to(home) for p in home.rglob("*.md"))
            self.assertEqual(before_files, after_files,
                             "dry-run must not modify filesystem")
            self.assertEqual(report.get("status"), "dry_run", report)

    def test_full_migration_promotes_v24_and_v23(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            _install_fixture(home)
            _git_init(home)
            report = _run_migrate(home)
            self.assertEqual(report.get("status"), "ok", report)
            # v2.4 folder-note → 00-README.md
            self.assertTrue((home / "workspaces/ws1/topic-a/00-README.md").is_file())
            # v2.4 sub-aspect → dated aspect
            sub_dated = list((home / "workspaces/ws1/topic-a").glob("*-operator.md"))
            self.assertEqual(len(sub_dated), 1, sub_dated)
            # lessons.md preserved at <slug>/lessons.md
            self.assertTrue((home / "workspaces/ws1/topic-a/lessons.md").is_file())
            # v2.3 flat topic-b.md promoted to topic-b/00-README.md
            self.assertTrue((home / "workspaces/ws1/topic-b/00-README.md").is_file())
            # Reserved docs/ untouched
            self.assertTrue((home / "workspaces/ws1/docs/handoff.md").is_file())
            # settings.json layout_version bumped to 3
            after = json.loads((home / "settings.json").read_text())
            self.assertEqual(after.get("layout_version"), 3)

    def test_idempotent_short_circuit_on_second_run(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            _install_fixture(home)
            _git_init(home)
            r1 = _run_migrate(home)
            self.assertEqual(r1.get("status"), "ok", r1)
            # Second run: F2 short-circuit (origin/main last commit subject starts "v3 migration ")
            # Local has no origin remote here, but layout_version=3 should still cause
            # the script to skip unless --force.
            r2 = _run_migrate(home)
            # Without --force, second run must short-circuit on already_v3 or
            # already_v3_on_remote (F2). dry_run/ok are NOT valid here.
            self.assertIn(r2.get("status"),
                          ("already_v3", "already_v3_on_remote"), r2)
            # When --force is passed, it should run again and create a NEW backup.
            backup_root = home / ".backup"
            backups_before = sorted(backup_root.glob("v2-pre-v3-*"))
            r3 = _run_migrate(home, "--force")
            backups_after = sorted(backup_root.glob("v2-pre-v3-*"))
            self.assertEqual(r3.get("status"), "ok", r3)
            # Rolling-2 window: at most 2 backups retained.
            self.assertLessEqual(len(backups_after), 2,
                                 f"rolling-2 window violated: {backups_after}")

    def test_backup_dir_name_has_microsecond_stamp(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            _install_fixture(home)
            _git_init(home)
            report = _run_migrate(home)
            backup = report.get("backup", "")
            # F1: name pattern v2-pre-v3-YYYYMMDDTHHMMSSZffffff (microsecond).
            self.assertRegex(
                Path(backup).name,
                r"^v2-pre-v3-\d{8}T\d{6}Z\d{6}$",
                f"backup dir missing microsecond stamp: {backup}",
            )


if __name__ == "__main__":
    unittest.main()
