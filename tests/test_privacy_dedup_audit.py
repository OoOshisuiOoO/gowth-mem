"""Tests for v3.1 privacy filter, dedup window, prune audit (adopted from agentmemory)."""
import importlib.util
import json
import os
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
        out, n = self.privacy.sanitize(None)  # type: ignore[arg-type]
        self.assertIsNone(out)
        self.assertEqual(n, 0)
        out, n = self.privacy.sanitize("")
        self.assertEqual(out, "")
        self.assertEqual(n, 0)

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


if __name__ == "__main__":
    unittest.main()
