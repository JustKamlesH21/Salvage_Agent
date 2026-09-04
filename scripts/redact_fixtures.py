#!/usr/bin/env python3
"""Turn captured deliveries into committable fixtures. Reproducible, never by hand.

Run:  .venv/bin/python scripts/redact_fixtures.py

Reads  tests/fixtures/_unredacted/   (gitignored, real deliveries)
Writes tests/fixtures/               (committed)

Why the signature is recomputed
-------------------------------
Redaction changes the bytes, so Razorpay's signature no longer verifies over
them. A committed fixture cannot be both redacted and carry a valid Razorpay
signature; those two requirements are incompatible and the honest resolution is
to say which one the fixture keeps.

These fixtures are re-signed with FIXTURE_SECRET below — a throwaway constant,
committed, worth nothing. Phase 2's test verifies that our HMAC implementation
accepts a correctly signed body and rejects a tampered one, and a body signed
with a known test secret exercises that code path identically to one signed by
Razorpay.

What that does NOT prove is that our HMAC matches *Razorpay's*. Only a real
delivery checked against the real webhook secret proves that, and it can never
be committed. `tests/local_only/test_real_signature.py` does it against the
uncommitted captures; run it once and record the result in NOTES.md.

Structure preservation
----------------------
Replacements keep the type, the length and the shape of what they replace. A
redacted `contact` is a plausible-length phone string; a redacted id keeps its
`pay_`/`sub_` prefix and its character count. A redaction that changes shapes
produces fixtures that pass tests the real payloads would fail — which is worse
than no fixture, because it is a test that lies.

Replacements are derived by hash from the original value, so the same input
always produces the same output. Re-running produces byte-identical files and
no git churn.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "tests" / "fixtures" / "_unredacted"
DEST = ROOT / "tests" / "fixtures"

# Worthless by design. The real secret never enters the repository.
FIXTURE_SECRET = "salvage_fixture_webhook_secret"  # noqa: S105

# Stands in for the capture-time ngrok hostname. Shape-preserving like every
# other replacement here: still a plausible tunnel host, carries no address.
TUNNEL_HOST = "salvage-fixture.ngrok-free.app"

# Free-text and contact fields. Replaced, shape preserved.
PII_KEYS = frozenset(
    {
        "email",
        "contact",
        "name",
        "customer_email",
        "customer_contact",
        "customer_name",
        "billing_address",
        "shipping_address",
        "description",
    }
)

# Kept verbatim — these are what the fixtures exist to preserve. Amounts, error
# structure, timestamps, statuses and every rz id stay exactly as delivered,
# because the taxonomy in SPEC §4.1 is built on the error fields and the gates
# are built on the amounts.
KEEP_VERBATIM = frozenset(
    {
        "amount",
        "amount_paid",
        "amount_due",
        "base_amount",
        "currency",
        "status",
        "created_at",
        "paid_at",
        "start_at",
        "end_at",
        "charge_at",
        "expire_by",
        "error_code",
        "error_description",
        "error_source",
        "error_step",
        "error_reason",
        "method",
        "international",
        "captured",
        "event",
        "entity",
        "account_id",
        "attempts",
        "total_count",
        "paid_count",
        "remaining_count",
        "last4",
        "network",
        "type",
        "issuer",
        "sub_type",
    }
)

# Razorpay id prefixes. The prefix and total length are preserved so anything
# that parses or validates an id still sees a well-formed one.
ID_PREFIXES = (
    "pay_",
    "sub_",
    "plan_",
    "inv_",
    "plink_",
    "cust_",
    "order_",
    "token_",
    "card_",
    "acc_",
    "item_",
    "addr_",
    "evt_",
)

ID_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
# Names get letters only. A replacement name containing a digit preserves the
# length but not the kind, and anything downstream that validates a name would
# behave differently on the fixture than on the real payload.
NAME_ALPHABET = "abcdefghijklmnopqrstuvwxyz"


def _stream(value: str, salt: str, alphabet: str = ID_ALPHABET) -> str:
    """Deterministic pseudo-random characters derived from a value."""
    out: list[str] = []
    counter = 0
    while len(out) < 64:
        block = hashlib.sha256(f"{salt}:{value}:{counter}".encode()).digest()
        out.extend(alphabet[b % len(alphabet)] for b in block)
        counter += 1
    return "".join(out)


def redact_id(value: str) -> str:
    """Replace a Razorpay id, keeping its prefix and its exact length."""
    for prefix in ID_PREFIXES:
        if value.startswith(prefix):
            body_len = len(value) - len(prefix)
            return prefix + _stream(value, "id")[:body_len]
    return value


def redact_email(value: str) -> str:
    """Same length class, obviously fake domain."""
    local, _, _domain = value.partition("@")
    replacement = _stream(value, "email")[: max(len(local), 4)].lower()
    return f"{replacement}@example.invalid"


def redact_contact(value: str) -> str:
    """Randomise the subscriber number, keep everything that carries meaning.

    The country code is not PII and it is load-bearing: anything routing or
    validating on `+91` would behave differently on a fixture whose prefix was
    scrambled. The digit count is preserved, and the first subscriber digit is
    forced into 6-9 so the result is a well-formed Indian mobile number rather
    than merely a string of the right length.
    """
    digits = re.sub(r"\D", "", value)
    seed = _stream(value, "contact")
    randomised = [str(ord(c) % 10) for c in seed[: len(digits)]]

    keep = 2 if (value.startswith("+") and len(digits) > 10) else 0
    head = list(digits[:keep])
    tail = randomised[keep:]
    if tail:
        # Indian mobile numbers start 6-9.
        tail[0] = str(6 + int(tail[0]) % 4)

    return ("+" if value.startswith("+") else "") + "".join(head + tail)


def redact_name(value: str) -> str:
    """Preserve word count and word lengths."""
    words = value.split()
    seed = _stream(value, "name", NAME_ALPHABET)
    out, cursor = [], 0
    for word in words:
        out.append((seed[cursor : cursor + len(word)] or "X").capitalize())
        cursor += len(word)
    return " ".join(out) or "Redacted"


def redact_value(key: str, value: Any) -> Any:
    if isinstance(value, str):
        if key in KEEP_VERBATIM:
            return value
        if key in {"email", "customer_email"}:
            return redact_email(value)
        if key in {"contact", "customer_contact"}:
            return redact_contact(value)
        if key in PII_KEYS:
            return redact_name(value)
        if any(value.startswith(p) for p in ID_PREFIXES):
            return redact_id(value)
    return value


def walk(node: Any, key: str = "") -> Any:
    if isinstance(node, dict):
        return {k: walk(v, k) for k, v in node.items()}
    if isinstance(node, list):
        return [walk(item, key) for item in node]
    return redact_value(key, node)


def sign(body: bytes) -> str:
    return hmac.new(FIXTURE_SECRET.encode(), body, hashlib.sha256).hexdigest()


def redact_file(raw_path: Path) -> dict[str, Any]:
    stem = raw_path.name.removesuffix(".raw.json")
    original = raw_path.read_bytes()
    parsed = json.loads(original)

    # Redact, then serialise once. Those exact bytes are what gets signed and
    # what gets committed, so the fixture's signature is valid over the file on
    # disk — which is the property the day 2 test needs.
    redacted_bytes = json.dumps(walk(parsed), indent=2, sort_keys=True).encode() + b"\n"
    (DEST / f"{stem}.raw.json").write_bytes(redacted_bytes)

    headers_path = raw_path.with_name(f"{stem}.headers.json")
    headers = json.loads(headers_path.read_text()) if headers_path.exists() else {}
    headers["x-razorpay-signature"] = sign(redacted_bytes)
    headers.pop("cookie", None)
    headers.pop("authorization", None)
    # A free-tier ngrok hostname embeds the tunnel client's public IP
    # ("69c8-223-181-12-125.ngrok-free.app" is 223.181.12.125), so the capture
    # host is the author's home address, not Razorpay's. Committing it to a
    # public repo publishes it. Replaced rather than dropped, because the
    # headers exist to show what a real delivery looks like and a fixture with
    # no Host is not one. `x-forwarded-for` stays: that is Razorpay's own
    # egress IP, which is theirs and public.
    for header in ("host", "x-forwarded-host"):
        if header in headers:
            headers[header] = TUNNEL_HOST
    (DEST / f"{stem}.headers.json").write_text(
        json.dumps(headers, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    return {
        "stem": stem,
        "event": parsed.get("event"),
        "bytes": len(redacted_bytes),
        "sha256": hashlib.sha256(redacted_bytes).hexdigest(),
    }


def write_tampered(summary: list[dict[str, Any]]) -> None:
    """One fixture whose signature is deliberately wrong.

    Without a real negative case, the tamper-rejection test can pass against an
    implementation that accepts everything.
    """
    if not summary:
        return
    source = DEST / f"{summary[0]['stem']}.raw.json"
    body = source.read_bytes()
    (DEST / "tampered.raw.json").write_bytes(body)
    (DEST / "tampered.headers.json").write_text(
        json.dumps(
            {
                "x-razorpay-signature": "0" * 64,
                "x-razorpay-event-id": "evt_tampered_negative_case",
                "content-type": "application/json",
                "_note": "Signature is deliberately wrong. HMAC verification MUST reject this.",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    if not SOURCE.exists() or not any(SOURCE.glob("*.raw.json")):
        print(
            f"redact: nothing to do — no captures in {SOURCE.relative_to(ROOT)}/\n"
            f"        Run scripts/capture_webhooks.py, drive Razorpay per\n"
            f"        tests/fixtures/README.md, then run this again."
        )
        return 0

    DEST.mkdir(parents=True, exist_ok=True)
    summary = [redact_file(p) for p in sorted(SOURCE.glob("*.raw.json"))]
    write_tampered(summary)

    print(f"redact: {len(summary)} fixture(s) written to {DEST.relative_to(ROOT)}/")
    for row in summary:
        print(f"  {row['stem']:34} {row['event']:24} {row['bytes']:>6}B  {row['sha256'][:12]}…")
    print("  tampered                           (negative case, invalid signature)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
