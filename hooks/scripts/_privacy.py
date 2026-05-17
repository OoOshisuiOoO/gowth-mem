"""Privacy filter for ~/.gowth-mem/ writes (pattern adopted from agentmemory).

sanitize(text) -> (clean_text, redactions) strips:
  1. `<private>...</private>` blocks (any case, multiline)
  2. Well-known secret/token shapes (API keys, JWTs, AWS keys, GitHub PATs,
     OpenAI/Anthropic keys, Slack tokens, generic high-entropy bearer tokens)
  3. `password=...`, `token=...`, `secret=...` key/value pairs
  4. Common SSH private key block markers

Matched secrets become `[REDACTED:<kind>]`; `<private>` blocks become
`[REDACTED:private-block]`. The hooks themselves call `sanitize()` before
`atomic_write` on any path that may carry user-typed content.

Designed to FAIL OPEN — any exception inside a pattern falls back to the
original text (we never want privacy filtering to block a legitimate write).
"""
from __future__ import annotations

import re
from typing import Tuple

PRIVATE_BLOCK_RE = re.compile(r"<private>.*?</private>", re.IGNORECASE | re.DOTALL)

# (label, compiled regex). Order matters — longer/specific patterns first so
# they win over the generic key=value catch-all.
_PATTERNS: list[tuple[str, "re.Pattern[str]"]] = [
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github-pat", re.compile(r"\bghp_[A-Za-z0-9]{36}\b")),
    ("github-oauth", re.compile(r"\bgho_[A-Za-z0-9]{36}\b")),
    ("github-app", re.compile(r"\b(?:ghu|ghs)_[A-Za-z0-9]{36}\b")),
    ("github-refresh", re.compile(r"\bghr_[A-Za-z0-9]{36}\b")),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("stripe-key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[0-9A-Za-z]{16,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")),
    ("ssh-private", re.compile(r"-----BEGIN (?:RSA |DSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    # Generic key=value last so it doesn't pre-empt specific tokens above.
    ("kv-secret", re.compile(
        r"\b(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
        r"private[_-]?key|auth[_-]?token)\s*[:=]\s*['\"]?[^\s'\"<>{}\[\]]{6,}",
        re.IGNORECASE,
    )),
]


def sanitize(text: str) -> Tuple[str, int]:
    """Redact secrets and `<private>` blocks. Returns (text, redaction_count).

    Always returns a valid string; on any internal failure the original input
    flows through unchanged with redaction_count=0.
    """
    if not isinstance(text, str) or not text:
        return text, 0
    redactions = 0
    out = text
    try:
        # Step 1: strip <private>...</private>
        def _strip_private(_m: "re.Match[str]") -> str:
            nonlocal redactions
            redactions += 1
            return "[REDACTED:private-block]"
        out = PRIVATE_BLOCK_RE.sub(_strip_private, out)

        # Step 2: per-pattern redaction
        for label, pat in _PATTERNS:
            def _sub(_m: "re.Match[str]", _label: str = label) -> str:
                nonlocal redactions
                redactions += 1
                return f"[REDACTED:{_label}]"
            out = pat.sub(_sub, out)
    except Exception:
        # Fail open — never block a write because the filter blew up.
        return text, 0
    return out, redactions


def has_secret(text: str) -> bool:
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
