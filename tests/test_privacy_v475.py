#!/usr/bin/env python3
"""v4.7.5 — privacy follow-ups found by the v4.7.4 review.

1. A pasted private key's BODY reached the synced vault: the `ssh-private` rule
   replaced only the `-----BEGIN … PRIVATE KEY-----` header line, leaving the
   base64 body (the actual secret) and the END line in every synced write.
   PGP private-key blocks (`… PRIVATE KEY BLOCK-----`) never matched at all.
2. A `<private>` block longer than a captured field's cap + 4 KB sanitize
   window leaked its first ~2,000 chars into the session log: the closing tag
   lay beyond the window, so the block never matched.
3. `reflection.max_prompt_chars` is user-settable; a huge value re-opened the
   super-linear sanitize path the v4.7.4 window closed.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "hooks" / "scripts"

B64 = "MIIEowIBAAKCAQEA0Z3VS5JJcds3xfn/ygWyF8PbnGy0AHB7MhgHcTz6sE2I2yPB"  # 64 chars


def _load(name: str):
    sys.path.insert(0, str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _pem(kind: str = "RSA PRIVATE KEY", lines: int = 25, last: str = "c2hvcnQ=",
         headers: str = "", end: bool = True) -> str:
    body = "\n".join([B64] * lines + [last])
    block = f"-----BEGIN {kind}-----\n{headers}{body}"
    return block + (f"\n-----END {kind}-----" if end else "")


class TestPrivateKeyBodies(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = _load("_privacy")

    def clean(self, text: str) -> str:
        out, n = self.p.sanitize(text)
        self.assertGreaterEqual(n, 0)
        return out

    def assert_no_key_material(self, out: str):
        self.assertNotIn(B64[:20], out, "the key BODY is the secret")
        self.assertNotIn("c2hvcnQ=", out, "the short final body line too")
        self.assertNotIn("PRIVATE KEY-----", out)

    def test_closed_blocks_are_removed_whole(self):
        for kind in ("RSA PRIVATE KEY", "EC PRIVATE KEY", "OPENSSH PRIVATE KEY",
                     "PRIVATE KEY", "ENCRYPTED PRIVATE KEY", "DSA PRIVATE KEY"):
            out = self.clean(f"before the key\n{_pem(kind)}\nafter the key")
            self.assert_no_key_material(out)
            self.assertIn("[REDACTED:ssh-private]", out, kind)
            self.assertTrue(out.startswith("before the key\n"), kind)
            self.assertTrue(out.endswith("\nafter the key"), kind)

    def test_legacy_encrypted_pem_headers(self):
        headers = "Proc-Type: 4,ENCRYPTED\nDEK-Info: AES-128-CBC,3F17F5316E2BAC89\n\n"
        out = self.clean(_pem(headers=headers))
        self.assert_no_key_material(out)
        self.assertNotIn("DEK-Info", out)

    def test_pgp_private_key_block(self):
        pgp = ("-----BEGIN PGP PRIVATE KEY BLOCK-----\nVersion: GnuPG v2\n\n"
               + "\n".join([B64] * 10) + "\n=c2hv\n-----END PGP PRIVATE KEY BLOCK-----")
        out = self.clean("key:\n" + pgp + "\ndone")
        self.assertNotIn(B64[:20], out)
        self.assertNotIn("PRIVATE KEY BLOCK", out)
        self.assertTrue(out.endswith("\ndone"))

    def test_unclosed_block_body_is_removed_prose_after_it_kept(self):
        """A key cut by a field cap (or pasted partially): no END line."""
        text = _pem(end=False, last=B64) + "\nand then I ran the deploy script"
        out = self.clean(text)
        self.assertNotIn(B64[:20], out)
        self.assertIn("and then I ran the deploy script", out)

    def test_public_material_is_untouched(self):
        for text in ("-----BEGIN PUBLIC KEY-----\n" + B64 + "\n-----END PUBLIC KEY-----",
                     "-----BEGIN CERTIFICATE-----\n" + B64 + "\n-----END CERTIFICATE-----",
                     "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl me@host"):
            self.assertEqual(self.clean(text), text)

    def test_prose_between_begin_and_a_far_end_is_not_swallowed(self):
        text = ("-----BEGIN RSA PRIVATE KEY-----\n" + B64 + "\n"
                "notes: this is ordinary prose, not key material.\n"
                "-----END RSA PRIVATE KEY-----")
        out = self.clean(text)
        self.assertNotIn(B64[:20], out)
        self.assertIn("notes: this is ordinary prose, not key material.", out)


    def test_armor_blank_line_without_end(self):
        """RFC 4880 §6.2 / RFC 1421: armor headers, a BLANK line, then the body."""
        for text in ("-----BEGIN PGP PRIVATE KEY BLOCK-----\nVersion: GnuPG v2\n\n" + "\n".join([B64] * 8),
                     "-----BEGIN RSA PRIVATE KEY-----\nProc-Type: 4,ENCRYPTED\nDEK-Info: AES-128-CBC,3F17F5\n\n"
                     + "\n".join([B64] * 8)):
            self.assertNotIn(B64[:20], self.clean(text + "\nprose after"), text[:40])

    def test_json_escaped_key(self):
        """GCP service-account JSON: literal backslash-n line breaks."""
        key = "-----BEGIN PRIVATE KEY-----\\n" + "\\n".join([B64] * 10) + "\\n-----END PRIVATE KEY-----\\n"
        text = '{"type": "service_account", "private_key": "' + key + '", "client_email": "x@y.iam"}'
        out = self.clean(text)
        self.assertNotIn(B64[:20], out)
        self.assertIn('"client_email": "x@y.iam"', out, "the rest of the JSON survives")
        truncated = '{"private_key": "-----BEGIN PRIVATE KEY-----\\n' + "\\n".join([B64] * 10)
        self.assertNotIn(B64[:20], self.clean(truncated))

    def test_end_of_a_different_key_type_does_not_close_the_block(self):
        text = ("-----BEGIN EC PRIVATE KEY-----\n" + B64 + "\n"
                "these are my notes\nabout rotation\n-----END OPENSSH PRIVATE KEY-----")
        out = self.clean(text)
        self.assertNotIn(B64[:20], out)
        self.assertIn("these are my notes", out)
        self.assertIn("about rotation", out)

    def test_residue_from_older_machines_is_cleaned(self):
        """≤ v4.7.4 machines wrote header-only redactions into the SHARED vault;
        the next safe_write here must remove the body they left behind."""
        text = "k:\n[REDACTED:ssh-private]\n" + "\n".join([B64] * 6) + "\nc2hvcnQ=\n-----END RSA PRIVATE KEY-----\ndone"
        out = self.clean(text)
        self.assertNotIn(B64[:20], out)
        self.assertNotIn("END RSA PRIVATE KEY", out)
        self.assertEqual(out, "k:\n[REDACTED:ssh-private]\ndone")
        clean_once = self.clean("k:\n[REDACTED:ssh-private]\ndone")
        self.assertEqual(clean_once, "k:\n[REDACTED:ssh-private]\ndone", "idempotent")

    def test_db_url_scheme_run_is_linear(self):
        t = time.perf_counter()
        self.p.sanitize("a." * 100_000)
        self.assertLess(time.perf_counter() - t, 1.0)
        out, n = self.p.sanitize("dsn postgresql://admin:hunter2pass@db.internal:5432/app")
        self.assertIn("[REDACTED:db-url-creds]", out)


    def test_private_block_stripping_is_linear(self):
        """Pre-existing: <private>.*?</private> rescans to the end for every
        unclosed tag — 0.9 MB of them took 298 s, and safe_write runs it over
        WHOLE files on every write."""
        for text in ("<private>" * 100_000, "<private>" * 100_000 + "</private>",
                     ("<private> x " * 50_000) + "</private>"):
            t = time.perf_counter()
            self.p.sanitize(text)
            self.p.has_secret(text)
            self.assertLess(time.perf_counter() - t, 1.0, text[:30])

    def test_private_block_semantics_match_the_reference_regex(self):
        """Differential fuzz: the linear scanner must produce exactly what the
        lazy regex produced (same text, same count) on every input."""
        import random
        ref = self.p.PRIVATE_BLOCK_RE
        tokens = ["<private>", "</private>", "<PRIVATE>", "</Private>", "<prıvate>", "x", "\n", "<", ">", "private", " "]
        rng = random.Random(4750)
        for _ in range(3000):
            t = "".join(rng.choice(tokens) for _ in range(rng.randint(0, 14)))
            want = ref.sub("[REDACTED:private-block]", t)
            got, n = self.p._strip_private_blocks(t)
            self.assertEqual(got, want, repr(t))
            self.assertEqual(n, len(ref.findall(t)), repr(t))
            self.assertEqual(self.p._has_private_block(t), bool(ref.search(t)), repr(t))


    def test_marker_followed_by_hashes_is_never_eaten(self):
        """Security review r2 MEDIUM: the residue rule keyed on the marker the
        NEW rule also writes — a fingerprint line after a redacted key was
        deleted, and more lines on every later write. Memory must survive."""
        fp = "SHA256:" + "k" * 0 + "Zm9vYmFyYmF6cXV4cXV1eGNvcmdlZ3JhdWx0Z2FycGx5"
        text = ("key below\n" + _pem() + "\n" + "Zm9vYmFyYmF6cXV4cXV1eGNvcmdlZ3JhdWx0Z2FycGx5\nfp ok\n"
                + "YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXo0NTY3ODkw\nshort\n" + fp)
        once = self.clean(text)
        self.assertIn("Zm9vYmFyYmF6cXV4cXV1eGNvcmdlZ3JhdWx0Z2FycGx5\nfp ok", once)
        self.assertIn("YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXo0NTY3ODkw\nshort", once)
        twice = self.clean(self.clean(once))
        self.assertEqual(twice, once, "repeated safe_writes must change nothing")

    def test_php_escaped_json_key(self):
        key = "-----BEGIN PRIVATE KEY-----\\n" + "\\n".join([B64.replace("/", "\\/")] * 10) + "\\n-----END PRIVATE KEY-----"
        out = self.clean('{"private_key":"' + key + '","x":1}')
        self.assertNotIn(B64[:12], out)
        self.assertIn('"x":1', out)

    def test_jwt_is_linear_and_still_redacted(self):
        t = time.perf_counter()
        self.p.sanitize("eyJ-" * 50_000)
        self.assertLess(time.perf_counter() - t, 1.0)
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlLXBhcnQ"
        for ctx in (f"Bearer {jwt}", f'"token":"{jwt}"', f"jwt={jwt} ok"):
            self.assertIn("[REDACTED:jwt]", self.clean(ctx), ctx)

    def test_key_body_scan_uses_bounded_memory(self):
        """Security review r2 LOW: the body alternation kept state per char —
        5 MB after a BEGIN line peaked at 1.1 GB; a MemoryError bypasses
        sanitize() entirely."""
        import subprocess
        code = ("import resource, sys; sys.path.insert(0, %r); import _privacy as p; "
                "t = '-----BEGIN RSA PRIVATE KEY-----\\n' + 'A' * 5_000_000; "
                "b = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss; p.sanitize(t); "
                "a = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss; "
                "scale = 1 if sys.platform == 'darwin' else 1024; print((a - b) * scale)") % str(SCRIPTS_DIR)
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(int(r.stdout.strip()), 300 * 1024 * 1024, "peak growth must stay bounded")


    def test_armor_headers_cannot_backtrack_exponentially(self):
        """Security review r3 HIGH: once the residue rule could fail AFTER its
        armor headers, a header line ending in a space split two ways between
        the value and the next line's indent — 4x per 2 lines (30 lines: > 1 h).
        Hooks have no timeout."""
        for text in ("[REDACTED:ssh-private]" + "\nComment: x " * 40,
                     "[REDACTED:ssh-private]" + "\nComment: x \t" * 40 + "\n" + B64,
                     "-----BEGIN RSA PRIVATE KEY-----" + "\nComment: x " * 40 + "\nprose after",
                     "-----BEGIN PGP PRIVATE KEY BLOCK-----" + "\nVersion: a b " * 40):
            t = time.perf_counter()
            self.p.sanitize(text)
            self.assertLess(time.perf_counter() - t, 1.0, text[:40])

    def test_linear_time_on_hostile_input(self):
        for text in ("-----BEGIN RSA PRIVATE KEY-----\n" + "A" * 1_000_000,
                     "-----BEGIN RSA PRIVATE KEY-----\n" + (B64 + "\n") * 20_000,
                     "-----BEGIN RSA PRIVATE KEY-----" * 20_000):
            t = time.perf_counter()
            self.p.sanitize(text)
            self.assertLess(time.perf_counter() - t, 2.0, text[:40])


class TestCaptureWindowPrivacy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cap = _load("_capture")

    def capture(self, prompt: str, settings: dict | None = None) -> str:
        tmp = tempfile.mkdtemp()
        os.environ["GOWTH_MEM_HOME"] = tmp
        try:
            os.makedirs(os.path.join(tmp, "workspaces", "default", "journal"))
            tx = os.path.join(tmp, "t.jsonl")
            with open(tx, "w", encoding="utf-8") as f:
                f.write(json.dumps({"type": "user", "uuid": "u1", "origin": {"kind": "human"},
                                    "message": {"content": prompt}}) + "\n")
            self.assertTrue(self.cap.capture_turn(tx, "default", "privwin1", 1, settings or {},
                                                  final_text="done"))
            sd = Path(tmp) / "workspaces" / "default" / "journal" / "sessions"
            log = "".join(p.read_text(encoding="utf-8") for p in sd.glob("*.md"))
            return next(l for l in log.splitlines() if l.startswith("**User:**"))
        finally:
            os.environ.pop("GOWTH_MEM_HOME", None)

    def test_private_block_longer_than_the_window_never_leaks(self):
        user = self.capture("context first <private>" + "SECRET-NOTE " * 1000
                            + "</private> and the ask")
        self.assertNotIn("SECRET-NOTE", user)
        self.assertIn("[REDACTED:private-block]", user)
        self.assertTrue(user.startswith("**User:** context first"), user)


    def test_keys_through_the_real_capture_path(self):
        """Review HIGH: capture collapsed newlines BEFORE sanitizing, so the
        line-based key rules never fired on the synced User: line."""
        pgp = ("-----BEGIN PGP PRIVATE KEY BLOCK-----\nVersion: GnuPG v2\nComment: laptop\n\n"
               + "\n".join([B64] * 110) + "\n=c2hv\n-----END PGP PRIVATE KEY BLOCK-----")  # ~7 KB: END past the window
        legacy = ("-----BEGIN RSA PRIVATE KEY-----\nProc-Type: 4,ENCRYPTED\nDEK-Info: AES-128-CBC,3F17F5\n\n"
                  + "\n".join([B64] * 25) + "\n-----END RSA PRIVATE KEY-----")
        partial = "-----BEGIN RSA PRIVATE KEY-----\n" + "\n".join([B64] * 25)
        for key in (pgp, legacy, partial):
            user = self.capture("here is my key:\n" + key + "\nwhat now?")
            self.assertNotIn(B64[:20], user, key[:40])

    def test_private_cut_is_unicode_safe(self):
        user = self.capture("İ" * 200 + " <private>" + "TOPSECRET " * 900 + "</private> end")
        self.assertNotIn("TOPSECRET", user)
        user = self.capture("x <prıvate>" + "TOPSECRET " * 900 + "</private> end")
        self.assertNotIn("TOPSECRET", user)

    def test_bare_tag_mention_is_not_cut(self):
        user = self.capture("how does the <private> tag work? also fix the flaky sync test")
        self.assertIn("also fix the flaky sync test", user)


    def test_closing_tag_straddling_the_window_edge(self):
        """Security review r2: a </private> starting 1-9 chars before the
        window end was missed — ~2,000 private chars leaked."""
        window = 2000 + 4096  # default max_prompt_chars + _SANITIZE_SLACK
        for offset in range(1, 10):
            prefix = "context <private>"
            body = "Q" * (window - len(prefix) - offset)
            user = self.capture(prefix + body + "</private> tail")
            self.assertNotIn("QQQQ", user, f"offset {offset}")

    def test_huge_max_prompt_chars_is_clamped(self):
        t = time.perf_counter()
        user = self.capture("a." * 100_000, {"reflection": {"max_prompt_chars": 10_000_000}})
        self.assertLess(time.perf_counter() - t, 5.0)
        self.assertLessEqual(len(user) - len("**User:** "), 8192)


if __name__ == "__main__":
    unittest.main()
