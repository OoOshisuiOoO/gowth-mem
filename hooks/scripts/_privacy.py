"""Privacy filter for ~/.gowth-mem/ writes (pattern adopted from agentmemory).

sanitize(text) -> (clean_text, redactions) strips:
  1. `<private>...</private>` blocks (any case, multiline)
  2. Well-known secret/token shapes — modern GitHub PATs (classic + fine-grained),
     GitLab, npm, PyPI, OpenAI (classic + project), Anthropic, AWS access/session,
     Slack tokens + webhooks, Discord, Google API, Stripe, SendGrid, Twilio, JWT,
     SSH private key markers
  3. `password=...`, `bearer ...`, `client_secret=...`, etc. (broad kv vocab)
  4. Database URLs with embedded credentials (`postgres://user:pw@host/...`)

Matched secrets become `[REDACTED:<kind>]`; `<private>` blocks become
`[REDACTED:private-block]`.

Return contract:
  - `(text, n)` where `n` is the count of redactions
  - `n == 0`  — clean text, nothing redacted
  - `n >= 1`  — redactions applied; caller may log
  - `n == -1` — filter crashed; original text returned unchanged AND a stderr
    warning + sanitize-failures audit line are emitted. Callers SHOULD inspect
    `n` to detect this bypass (writes proceed so user content is never lost).

Design notes:
  - Fail OPEN on internal exceptions (never block a legitimate write) but
    surface the bypass via stderr + audit log so silent regressions are visible.
  - `sanitize(None)` returns `("", 0)` for caller safety (was `(None, 0)`).
"""
from __future__ import annotations

import re
import sys
from typing import Tuple

PRIVATE_BLOCK_RE = re.compile(r"<private>.*?</private>", re.IGNORECASE | re.DOTALL)

# (label, compiled regex). Order matters — longer/specific patterns first so
# they win over the generic kv catch-all.
_PATTERNS: list[tuple[str, "re.Pattern[str]"]] = [
    # AWS
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    # GitHub — flexible upper bound to survive token format changes
    ("github-pat", re.compile(r"\bghp_[A-Za-z0-9]{36,255}\b")),
    ("github-oauth", re.compile(r"\bgho_[A-Za-z0-9]{36,255}\b")),
    ("github-app", re.compile(r"\b(?:ghu|ghs)_[A-Za-z0-9]{36,255}\b")),
    ("github-refresh", re.compile(r"\bghr_[A-Za-z0-9]{36,255}\b")),
    ("github-fine-grained", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,255}\b")),
    # GitLab / npm / PyPI
    ("gitlab-pat", re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}\b")),
    ("npm-token", re.compile(r"\bnpm_[A-Za-z0-9]{36,}\b")),
    ("pypi-token", re.compile(r"\bpypi-AgEI[A-Za-z0-9_\-]{40,}\b")),
    # OpenAI / Anthropic — project keys (`sk-proj-…`) BEFORE generic `sk-` so
    # the more specific label wins.
    ("openai-proj-key", re.compile(r"\bsk-proj-[A-Za-z0-9_\-]{20,}\b")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b")),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    # Slack tokens + webhooks
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("slack-webhook", re.compile(r"\bhooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]+\b")),
    # Discord bot token (three base64url segments)
    ("discord-bot", re.compile(r"\b[A-Za-z0-9_\-]{24}\.[A-Za-z0-9_\-]{6}\.[A-Za-z0-9_\-]{27,}\b")),
    # Google / Stripe / SendGrid / Twilio
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("stripe-key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[0-9A-Za-z]{16,}\b")),
    ("sendgrid", re.compile(r"\bSG\.[A-Za-z0-9_\-]{22}\.[A-Za-z0-9_\-]{43}\b")),
    ("twilio-sid", re.compile(r"\b(?:SK|AC|AU)[a-f0-9]{32}\b")),
    # JWT / SSH-private
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")),
    ("ssh-private", re.compile(r"-----BEGIN (?:RSA |DSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    # Database URL credentials: scheme://user:pass@host
    ("db-url-creds", re.compile(r"\b[a-z][a-z0-9+.\-]{2,}://[^/\s:@]+:[^/\s:@]+@", re.IGNORECASE)),
    # HTTP Bearer header: `Bearer <token>` (whitespace separator, not `:` / `=`)
    ("bearer-token", re.compile(r"\bbearer\s+[A-Za-z0-9_\-\.+/=]{16,}", re.IGNORECASE)),
    # Generic kv-secret LAST. Value class excludes URL chars (`&?#/`) and
    # punctuation that ends prose, requires ≥12 chars to reduce false-positives
    # on short identifiers. Vocab extended for common synonyms.
    ("kv-secret", re.compile(
        r"\b(?:password|passwd|pwd|secret|token|bearer|api[_-]?key|access[_-]?key|"
        r"private[_-]?key|auth[_-]?token|refresh[_-]?token|client[_-]?secret|"
        r"session[_-]?token|credentials?|passphrase|dsn|connection[_-]?string)"
        r"\s*[:=]\s*['\"]?[A-Za-z0-9_\-\.+/=]{12,}",
        re.IGNORECASE,
    )),
]


def _warn_bypass(exc: Exception) -> None:
    """Surface a sanitize() failure to stderr + audit log. Never raises."""
    msg = f"WARN: _privacy.sanitize bypassed (regex failure): {exc!r}"
    try:
        print(msg, file=sys.stderr)
    except Exception:
        pass
    try:
        # Local import — avoids cycle (and lets sanitize work pre-_audit-bootstrap).
        from pathlib import Path
        from datetime import datetime
        from _home import gowth_home  # type: ignore
        d = gowth_home() / ".audit"
        d.mkdir(parents=True, exist_ok=True)
        log = d / "sanitize-failures.log"
        with log.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')}\t{msg}\n")
    except Exception:
        pass


def sanitize(text) -> Tuple[str, int]:
    """Redact secrets and `<private>` blocks. Returns (text, redaction_count).

    - `text=None` → returns ("", 0) for caller safety.
    - On internal failure: returns the ORIGINAL text with count=-1 and emits
      a stderr + audit warning. Callers should inspect count < 0 to detect
      bypass; writes proceed to avoid losing user data.
    """
    if text is None:
        return "", 0
    if not isinstance(text, str) or not text:
        return text, 0
    redactions = 0
    out = text
    try:
        def _strip_private(_m: "re.Match[str]") -> str:
            nonlocal redactions
            redactions += 1
            return "[REDACTED:private-block]"
        out = PRIVATE_BLOCK_RE.sub(_strip_private, out)

        for label, pat in _PATTERNS:
            def _sub(_m: "re.Match[str]", _label: str = label) -> str:
                nonlocal redactions
                redactions += 1
                return f"[REDACTED:{_label}]"
            out = pat.sub(_sub, out)
    except Exception as exc:
        _warn_bypass(exc)
        return text, -1
    return out, redactions


def has_secret(text) -> bool:
    """Quick check — True if any pattern matches. Cheap enough for log gates."""
    if not isinstance(text, str) or not text:
        return False
    try:
        if PRIVATE_BLOCK_RE.search(text):
            return True
        for _label, pat in _PATTERNS:
            if pat.search(text):
                return True
    except Exception:
        return False
    return False
