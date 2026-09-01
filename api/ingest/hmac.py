"""HMAC-SHA256 signature verification over the raw webhook body.

Razorpay signs each webhook with HMAC-SHA256 using the secret configured on
the webhook (the `X-Razorpay-Signature` header carries the hex digest).
Verification MUST run over the exact bytes as delivered — before any parsing,
casting or re-serialisation — because a parsed-then-reserialised body is a
different byte string with a different signature. This is the classic bug
SPEC §5.1 calls out, and it is exactly the mistake day 1's redaction work had
to design around (see `scripts/redact_fixtures.py`).

Pure and stateless: no I/O, no config reads. The caller supplies the secret,
so this module has no opinion about where it comes from — that is what keeps
it trivially testable against every committed fixture and the tampered
negative case without touching `api.config` at all.
"""

from __future__ import annotations

import hashlib
import hmac


def verify_signature(raw_body: bytes, signature: str | None, secret: str) -> bool:
    """True iff `signature` is the HMAC-SHA256 hex digest of `raw_body` under `secret`.

    A missing signature or an empty secret are "not valid", not exceptions —
    a webhook endpoint has no business raising on adversarial or misconfigured
    input, it has business rejecting it and moving on.
    """
    if not signature or not secret:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
