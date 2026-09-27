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
# v4.7.5: the regex above is the REFERENCE semantics only. As a scanner it is
# quadratic on unclosed tags (every <private> rescans to the end: 0.9 MB of
# them took 298 s) and sanitize() runs over WHOLE files on every safe_write.
# _strip_private_blocks / _has_private_block implement it in linear time.
_PRIVATE_OPEN_RE = re.compile(r"<private>", re.IGNORECASE)
_PRIVATE_CLOSE_RE = re.compile(r"</private>", re.IGNORECASE)


def _strip_private_blocks(text: str) -> Tuple[str, int]:
    """PRIVATE_BLOCK_RE.sub("[REDACTED:private-block]", text), linear time.

    Left to right, each <private> not already inside a stripped block pairs
    with the FIRST </private> after it (the lazy regex); an opening with no
    closing after it ends the scan (no later opening can have one either).
    """
    closes = [(m.start(), m.end()) for m in _PRIVATE_CLOSE_RE.finditer(text)]
    if not closes:
        return text, 0
    out, pos, ci, n = [], 0, 0, 0
    for mo in _PRIVATE_OPEN_RE.finditer(text):
        if mo.start() < pos:
            continue  # inside a block already stripped
        while ci < len(closes) and closes[ci][0] < mo.end():
            ci += 1
        if ci >= len(closes):
            break
        out.append(text[pos:mo.start()])
        out.append("[REDACTED:private-block]")
        n += 1
        pos = closes[ci][1]
        ci += 1
    out.append(text[pos:])
    return "".join(out), n


def _has_private_block(text: str) -> bool:
    """bool(PRIVATE_BLOCK_RE.search(text)), linear time: some <private> has a
    </private> after it — i.e. the first opening precedes the last closing."""
    first_open = _PRIVATE_OPEN_RE.search(text)
    if first_open is None:
        return False
    last_close = None
    for last_close in _PRIVATE_CLOSE_RE.finditer(text, first_open.end()):
        pass
    return last_close is not None

# PEM/OpenSSH/PGP private-key armor (`-----BEGIN <kind>PRIVATE KEY[ BLOCK]-----`).
_PEM_KIND = r"(?:RSA |DSA |EC |OPENSSH |PGP |ENCRYPTED )?"
# A line break: real, or JSON-escaped (a service-account file's "private_key":
# "-----BEGIN PRIVATE KEY-----\nMIIE…" pasted into a prompt).
_PEM_NL = r"(?:\r?\n|\\(?:r\\)?n)"
# Armor header lines (RFC 1421 Proc-Type/DEK-Info, RFC 4880 Version/Comment/…);
# the value stops at a backslash so it cannot swallow a JSON one-liner.
_PEM_HEADERS = (r"(?:[ \t]*" + _PEM_NL + r"[ \t]*"
                r"(?:Proc-Type|DEK-Info|Version|Comment|Hash|Charset):[^\r\n\\]*)*")
# ATOMIC (lookahead-capture + backreference: Python 3.9 has no atomic groups).
# A header value may end in spaces that the next line's `[ \t]*` can also take;
# once a rule can FAIL after its headers (the residue rule requires an END),
# every split is retried — exponential (security review r3: 30 lines > 1 h).
_PEM_HEADERS_ATOMIC = r"(?=(?P<hdr>" + _PEM_HEADERS + r"))(?P=hdr)"
# Whole base64 body LINES only (prose after an unclosed key survives), after
# the optional blank line both RFCs put between the armor headers and the body.
_PEM_BODY_LINES = (r"(?:[ \t]*" + _PEM_NL + r"(?=[ \t]*" + _PEM_NL + r"[ \t]*[A-Za-z0-9+/=]{16,}))?"
                   r"(?:[ \t]*" + _PEM_NL + r"[ \t]*[A-Za-z0-9+/=]{16,}[ \t]*(?=" + _PEM_NL + r'|$|"))')

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
    # Starts only at a token boundary: with `\b`, every `eyJ` inside a run like
    # `eyJ-eyJ-…` started a scan to the end (200 KB: 11.4 s).
    ("jwt", re.compile(r"(?<![A-Za-z0-9_\-])eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")),
    # v4.7.5: the WHOLE key, not just its BEGIN line — the base64 body IS the
    # secret, and replacing only the header left it (and the END line) in every
    # synced write; PGP `… PRIVATE KEY BLOCK` never matched at all. Closed
    # block: header, armor headers, a body of ONLY base64/whitespace/escaped
    # line breaks, and an END of the SAME kind (a different key's END never
    # closes it — that swallowed the notes between). Otherwise (no END, a key
    # cut by a cap or pasted partially, or prose before the END): header +
    # armor headers + whole base64 body lines, prose after them kept.
    # Linear: single-class runs bounded by literals, no ambiguous nesting.
    ("ssh-private", re.compile(
        r"-----BEGIN (?P<k>" + _PEM_KIND + r")PRIVATE KEY(?P<b>(?: BLOCK)?)-----" + _PEM_HEADERS_ATOMIC +
        r"(?:[A-Za-z0-9+/=\s]*(?:\\[rn/][A-Za-z0-9+/=\s]*)*-----END (?P=k)PRIVATE KEY(?P=b)-----"
        r"|" + _PEM_BODY_LINES + r"*)"
    )),
    # Residue: machines on <= v4.7.4 wrote header-only redactions into the
    # SHARED vault, leaving the body AND the END line under the marker.
    # safe_write re-sanitizes whole files, so the next write here removes it.
    # The END line is REQUIRED: the marker is also what the rule above writes,
    # and hash / fingerprint lines after a redacted key are user memory
    # (security review: an optional END ate them, more on every write).
    ("ssh-private", re.compile(
        r"\[REDACTED:ssh-private\]" + _PEM_HEADERS_ATOMIC + _PEM_BODY_LINES + r"+"
        r"(?:[ \t]*" + _PEM_NL + r"[ \t]*[A-Za-z0-9+/=]{1,15}[ \t]*(?=" + _PEM_NL + r'|$|"))?'
        r"[ \t]*" + _PEM_NL + r"[ \t]*-----END " + _PEM_KIND + r"PRIVATE KEY(?: BLOCK)?-----"
    )),
    # Database URL credentials: scheme://user:pass@host
    # Scheme run bounded ({2,31}): unbounded, "a.a.a…" was quadratic (100 KB:
    # 18 s) — and safe_write runs this over whole files on every write.
    ("db-url-creds", re.compile(r"\b[a-z][a-z0-9+.\-]{2,31}://[^/\s:@]+:[^/\s:@]+@", re.IGNORECASE)),
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
        out, stripped = _strip_private_blocks(out)
        redactions += stripped

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
        if _has_private_block(text):
            return True
        for _label, pat in _PATTERNS:
            if pat.search(text):
                return True
    except Exception:
        return False
    return False
