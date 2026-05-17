"""Tests for v3.1 privacy filter, dedup window, prune audit (adopted from agentmemory).

Also covers v3.1.1 hardening:
  - expanded secret patterns (modern PATs, GitLab, npm, PyPI, OpenAI project,
    Slack webhook, Discord, SendGrid, Twilio, db-URL creds, expanded kv vocab)
  - tightened kv-secret value class (no URL-query false positives)
  - sanitize(None) → ("", 0) contract
  - safe_write chokepoint: sanitizes synced .md, passes JSON through
  - prune duplicate rule = first-write-wins
  - audit dir 0700 / log file 0600
  - dedup window self-healing
  - gitignore backfill line-by-line membership
"""
import importlib.util
import json
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "hooks" / "scripts"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class PrivacyFilterTests(unittest.TestCase):
    def setUp(self):
        self.privacy = load_module("gowth_privacy", SCRIPTS / "_privacy.py")

    def test_redacts_openai_key(self):
        text = "my key is sk-abcdef1234567890ghijkl and that's it"
        out, n = self.privacy.sanitize(text)
        self.assertIn("[REDACTED:openai-key]", out)
        self.assertNotIn("sk-abcdef1234567890ghijkl", out)
        self.assertEqual(n, 1)

    def test_redacts_anthropic_key(self):
        text = "ANTHROPIC=sk-ant-api03-AAAAAAAAAAAAAAAAAAAA_xx"
        out, n = self.privacy.sanitize(text)
        self.assertIn("[REDACTED:anthropic-key]", out)
        self.assertGreaterEqual(n, 1)

    def test_redacts_github_pat(self):
        text = "token: ghp_abcdefghijklmnopqrstuvwxyz0123456789"
        out, n = self.privacy.sanitize(text)
        self.assertIn("[REDACTED:github-pat]", out)
        self.assertEqual(n, 1)

    def test_redacts_aws_access_key(self):
        text = "AKIAIOSFODNN7EXAMPLE is the key"
        out, n = self.privacy.sanitize(text)
        self.assertIn("[REDACTED:aws-access-key]", out)
        self.assertEqual(n, 1)

    def test_redacts_jwt(self):
        text = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signaturepart_x"
        out, n = self.privacy.sanitize(text)
        self.assertIn("[REDACTED:jwt]", out)
        self.assertGreaterEqual(n, 1)

    def test_redacts_private_block(self):
        text = "before <private>my secret\nmultiline notes</private> after"
        out, n = self.privacy.sanitize(text)
        self.assertIn("[REDACTED:private-block]", out)
        self.assertNotIn("my secret", out)
        self.assertNotIn("multiline notes", out)
        self.assertEqual(n, 1)

    def test_redacts_kv_secret(self):
        text = "password=hunter2supersecret and api_key: abcdef12345678"
        out, n = self.privacy.sanitize(text)
        self.assertIn("[REDACTED:kv-secret]", out)
        self.assertGreaterEqual(n, 1)

    def test_leaves_clean_text_alone(self):
        text = "# Heading\n\nNo secrets here, just thoughts."
        out, n = self.privacy.sanitize(text)
        self.assertEqual(out, text)
        self.assertEqual(n, 0)

    def test_fails_open_on_bad_input(self):
        # Contract: sanitize(None) → ("", 0). Was (None, 0); callers expect str.
        out, n = self.privacy.sanitize(None)  # type: ignore[arg-type]
        self.assertEqual(out, "")
        self.assertEqual(n, 0)
        out, n = self.privacy.sanitize("")
        self.assertEqual(out, "")
        self.assertEqual(n, 0)

    def test_redacts_github_fine_grained_pat(self):
        text = "TOKEN=github_pat_22ABCDEFGHIJKLMNOPQRSTUV_xxxxxxxxxxxxxxxxxxxx"
        out, n = self.privacy.sanitize(text)
        self.assertIn("[REDACTED:github-fine-grained]", out)
        self.assertGreaterEqual(n, 1)

    def test_redacts_openai_project_key(self):
        text = "OPENAI_API_KEY=sk-proj-" + "A" * 48
        out, n = self.privacy.sanitize(text)
        self.assertIn("[REDACTED:openai-proj-key]", out)

    def test_redacts_slack_webhook(self):
        text = "POST https://hooks.slack.com/services/T01ABCDEFGH/B01ABCDEFGH/abcdefghijklmnop"
        out, n = self.privacy.sanitize(text)
        self.assertIn("[REDACTED:slack-webhook]", out)

    def test_redacts_sendgrid(self):
        # SendGrid format: SG.<22 chars>.<43 chars> — strict regex enforces lengths
        text = "SG." + "a" * 22 + "." + "b" * 43
        out, n = self.privacy.sanitize(text)
        self.assertIn("[REDACTED:sendgrid]", out)

    def test_redacts_db_url_credentials(self):
        text = "DATABASE_URL=postgres://app:hunter2pass@db.example.com:5432/foo"
        out, n = self.privacy.sanitize(text)
        self.assertIn("[REDACTED:db-url-creds]", out)
        self.assertNotIn("hunter2pass", out)

    def test_redacts_bearer_token(self):
        text = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123"
        out, n = self.privacy.sanitize(text)
        self.assertIn("[REDACTED:bearer-token]", out)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz0123", out)

    def test_redacts_gitlab_pat(self):
        text = "GITLAB_TOKEN=glpat-abcdefghijklmnopqrst"
        out, n = self.privacy.sanitize(text)
        self.assertIn("[REDACTED:gitlab-pat]", out)

    def test_no_false_positive_on_url_query(self):
        # `token=foo` in a URL with `&id=bar` must not silently destroy the URL
        # tail. Value class excludes `&`, so the truncation stops at `&`.
        text = "see https://api.example.com/v1?token=xyz123abcdefg&id=42"
        out, n = self.privacy.sanitize(text)
        # `&id=42` must survive (only the token segment is touched)
        self.assertIn("&id=42", out)

    def test_no_false_positive_on_short_identifier(self):
        # `api_key: thereadme` (11 chars) is below the 12-char threshold
        text = "see api_key: prose"
        out, n = self.privacy.sanitize(text)
        self.assertEqual(n, 0)
        self.assertEqual(out, text)

    def test_has_secret_quick_gate(self):
        self.assertTrue(self.privacy.has_secret("sk-abcdef1234567890ghijkl"))
        self.assertTrue(self.privacy.has_secret("<private>x</private>"))
        self.assertFalse(self.privacy.has_secret("nothing here"))


class DedupWindowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_dedup_")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        self.dedup = load_module("gowth_dedup", SCRIPTS / "_dedup.py")

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)

    def test_first_seen_returns_false(self):
        self.assertFalse(self.dedup.seen_recently("hello world entry"))

    def test_record_then_seen_returns_true(self):
        self.dedup.record("entry text alpha")
        self.assertTrue(self.dedup.seen_recently("entry text alpha"))

    def test_check_and_record_atomic(self):
        self.assertFalse(self.dedup.check_and_record("first-time text"))
        self.assertTrue(self.dedup.check_and_record("first-time text"))

    def test_normalization_strips_whitespace(self):
        self.dedup.record("hello   world")
        self.assertTrue(self.dedup.seen_recently("HELLO  world"))

    def test_expiry_drops_old_entries(self):
        self.dedup.record("aged entry")
        path = Path(self.tmp) / ".dedup-window.json"
        data = json.loads(path.read_text())
        data["window_seconds"] = 1
        data["entries"] = {k: time.time() - 5 for k in data["entries"]}
        path.write_text(json.dumps(data))
        self.assertFalse(self.dedup.seen_recently("aged entry"))


class AuditLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_audit_")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        self.audit = load_module("gowth_audit", SCRIPTS / "_audit.py")

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)

    def test_log_prune_delete_writes_jsonl(self):
        self.audit.log_prune_delete(
            "workspaces/x/ema/lessons.md",
            "superseded",
            "- [exp] old broken thing",
        )
        log_files = list((Path(self.tmp) / ".audit").glob("prune-*.log"))
        self.assertEqual(len(log_files), 1)
        line = log_files[0].read_text().strip()
        entry = json.loads(line)
        self.assertEqual(entry["op"], "prune-delete")
        self.assertEqual(entry["reason"], "superseded")
        self.assertEqual(entry["file"], "workspaces/x/ema/lessons.md")
        self.assertIn("old broken thing", entry["preview"])

    def test_preview_capped(self):
        long = "x" * 200
        self.audit.log_prune_delete("f.md", "duplicate", long)
        log_files = list((Path(self.tmp) / ".audit").glob("prune-*.log"))
        entry = json.loads(log_files[0].read_text().strip())
        self.assertLessEqual(len(entry["preview"]), 80)

    def test_fail_open_when_home_missing(self):
        os.environ["GOWTH_MEM_HOME"] = "/nonexistent/path/that/cannot/exist/xyz"
        try:
            self.audit.log_prune_delete("f.md", "expired", "x")
        except Exception as e:
            self.fail(f"audit.log_prune_delete should fail open, got: {e}")


class AuditPermissionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_audit_perm_")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        self.audit = load_module("gowth_audit_perm", SCRIPTS / "_audit.py")

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)

    def test_dir_is_0700_and_log_is_0600(self):
        self.audit.log_prune_delete("f.md", "expired", "x")
        d = Path(self.tmp) / ".audit"
        log = next(d.glob("prune-*.log"))
        self.assertEqual(stat.S_IMODE(d.stat().st_mode), 0o700,
                         "audit dir must be owner-only")
        self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600,
                         "audit log must be owner-only")


class DedupSelfHealTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gowth_dedup_heal_")
        os.environ["GOWTH_MEM_HOME"] = self.tmp
        self.dedup = load_module("gowth_dedup_heal", SCRIPTS / "_dedup.py")

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)

    def test_recovers_from_non_dict_root(self):
        Path(self.tmp, ".dedup-window.json").write_text('["not", "a", "dict"]')
        # Must not raise, must not silently disable dedup
        self.assertFalse(self.dedup.check_and_record("entry-after-poison"))
        self.assertTrue(self.dedup.check_and_record("entry-after-poison"))

    def test_recovers_from_string_ttl(self):
        Path(self.tmp, ".dedup-window.json").write_text(
            '{"window_seconds": "garbage", "entries": {"x": "also-garbage"}}'
        )
        self.assertFalse(self.dedup.check_and_record("post-string-ttl"))

    def test_recovers_from_list_entries(self):
        Path(self.tmp, ".dedup-window.json").write_text(
            '{"window_seconds": 300, "entries": [1, 2, 3]}'
        )
        self.assertFalse(self.dedup.check_and_record("post-list-entries"))


class SafeWriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gowth_safe_"))
        os.environ["GOWTH_MEM_HOME"] = str(self.tmp)
        # Load _atomic in a way that lets it find the sibling modules
        import sys as _sys
        _sys.path.insert(0, str(SCRIPTS))
        self.atomic = load_module("gowth_safe_atomic", SCRIPTS / "_atomic.py")

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)

    def test_sanitizes_synced_markdown(self):
        path = self.tmp / "workspaces" / "demo" / "topic" / "lessons.md"
        n = self.atomic.safe_write(path, "## Notes\n\nsk-abcdef1234567890ghijkl\n")
        self.assertEqual(n, 1)
        self.assertIn("[REDACTED:openai-key]", path.read_text())

    def test_passthrough_for_state_json(self):
        path = self.tmp / "state.json"
        n = self.atomic.safe_write(path, '{"password": "hunter2supersecret"}')
        # state.json is NOT under workspaces/ or shared/ — bypass sanitize
        self.assertEqual(n, 0)
        self.assertIn("hunter2supersecret", path.read_text())

    def test_passthrough_for_non_synced_markdown(self):
        # README at home root — not under workspaces/ — passes through
        path = self.tmp / "README.md"
        n = self.atomic.safe_write(path, "sk-abcdef1234567890ghijkl")
        self.assertEqual(n, 0)


class PruneFirstWriteWinsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gowth_prune_"))
        os.environ["GOWTH_MEM_HOME"] = str(self.tmp)
        self.prune = load_module("gowth_prune_dup", SCRIPTS / "_prune.py")

    def tearDown(self):
        os.environ.pop("GOWTH_MEM_HOME", None)

    def test_duplicate_newer_dropped(self):
        # Two entries with ≥0.85 Jaccard (only the newer is verbose). v3.1.1
        # rule: first survives, second dropped (was: longer wins).
        path = self.tmp / "lessons.md"
        path.write_text(
            "- [exp] alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo\n"
            "- [exp] alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo extra\n"
        )
        deleted, kept = self.prune.prune_file(path, dry_run=False,
                                              today_iso="2026-05-17",
                                              audit_rel="lessons.md")
        self.assertEqual(deleted, 1)
        self.assertEqual(kept, 1)
        remaining = path.read_text()
        # First (concise) entry survives; second (with "extra") is dropped.
        self.assertNotIn("extra\n", remaining)

    def test_audit_reason_is_duplicate_newer_dropped(self):
        path = self.tmp / "lessons.md"
        path.write_text(
            "- [exp] alpha bravo charlie delta echo foxtrot golf hotel india\n"
            "- [exp] alpha bravo charlie delta echo foxtrot golf hotel india variant\n"
        )
        self.prune.prune_file(path, dry_run=False, today_iso="2026-05-17",
                              audit_rel="lessons.md")
        log_files = list((self.tmp / ".audit").glob("prune-*.log"))
        self.assertTrue(log_files, "audit log was not written")
        line = log_files[0].read_text().strip().splitlines()[0]
        entry = json.loads(line)
        self.assertEqual(entry["reason"], "duplicate-newer-dropped")


class GitignoreBackfillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gowth_gi_"))
        self.sync = load_module("gowth_sync_gi", SCRIPTS / "_sync.py")

    def test_writes_template_when_missing(self):
        self.sync.write_default_gitignore(self.tmp)
        gi = (self.tmp / ".gitignore").read_text()
        self.assertIn(".audit/", gi)
        self.assertIn(".dedup-window.json", gi)
        self.assertIn("config.json", gi)

    def test_backfills_missing_entries(self):
        gi = self.tmp / ".gitignore"
        gi.write_text("config.json\nstate.json\n")
        self.sync.write_default_gitignore(self.tmp)
        out = gi.read_text()
        self.assertIn(".audit/", out)
        self.assertIn(".dedup-window.json", out)
        self.assertIn("config.json", out)  # preserved

    def test_idempotent_when_entries_present(self):
        gi = self.tmp / ".gitignore"
        gi.write_text("config.json\n.audit/\n.dedup-window.json\n")
        before = gi.read_text()
        self.sync.write_default_gitignore(self.tmp)
        self.assertEqual(gi.read_text(), before)

    def test_comment_only_mention_does_not_block_backfill(self):
        # v3.1.1: substring check used to skip backfill when a comment merely
        # mentioned `.audit/` — line-by-line membership now ignores comments.
        gi = self.tmp / ".gitignore"
        gi.write_text("config.json\n# Maybe ignore .audit/ later\n")
        self.sync.write_default_gitignore(self.tmp)
        out = gi.read_text()
        # Must contain a real (non-comment) line `.audit/`
        real_lines = [ln.strip() for ln in out.splitlines()
                      if ln.strip() and not ln.strip().startswith("#")]
        self.assertIn(".audit/", real_lines)
        self.assertIn(".dedup-window.json", real_lines)

    def test_negation_does_not_block_backfill(self):
        # `!entry` is a negation pattern, not a positive entry
        gi = self.tmp / ".gitignore"
        gi.write_text("config.json\n!.audit/\n")
        self.sync.write_default_gitignore(self.tmp)
        out = gi.read_text()
        real_lines = [ln.strip() for ln in out.splitlines()
                      if ln.strip() and not ln.strip().startswith(("#", "!"))]
        self.assertIn(".audit/", real_lines)


if __name__ == "__main__":
    unittest.main()
