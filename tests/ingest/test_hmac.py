"""Signature verification, tested against every committed fixture.

FR-01's acceptance criterion is "tampered payload rejected; test proves body
is not parsed before verification." This file proves the first half
concretely, byte for byte, against real captured shapes rather than a
hand-built example. test_router.py proves the ordering half.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from api.ingest.hmac import verify_signature
from scripts.redact_fixtures import FIXTURE_SECRET

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def committed_fixture_stems() -> list[str]:
    stems = []
    for raw_path in sorted(FIXTURES.glob("*.raw.json")):
        stem = raw_path.name.removesuffix(".raw.json")
        if stem == "tampered":
            continue
        stems.append(stem)
    return stems


class TestGenuineFixtures:
    @pytest.mark.parametrize("stem", committed_fixture_stems())
    def test_every_committed_fixtures_real_signature_verifies(self, stem: str) -> None:
        raw = (FIXTURES / f"{stem}.raw.json").read_bytes()
        headers = json.loads((FIXTURES / f"{stem}.headers.json").read_text())
        assert verify_signature(raw, headers["x-razorpay-signature"], FIXTURE_SECRET)

    def test_at_least_one_fixture_of_each_required_event_type(self) -> None:
        # A sanity check on the parametrised test above: it is only meaningful
        # coverage if the fixture set actually spans the required events.
        events = {
            json.loads((FIXTURES / f"{s}.raw.json").read_bytes())["event"]
            for s in committed_fixture_stems()
        }
        required = {
            "payment.failed",
            "subscription.charged",
            "invoice.paid",
            "subscription.halted",
            "payment_link.paid",
        }
        assert required <= events, f"missing: {required - events}"


class TestTamperedFixture:
    def test_the_tampered_fixture_fails_verification(self) -> None:
        raw = (FIXTURES / "tampered.raw.json").read_bytes()
        headers = json.loads((FIXTURES / "tampered.headers.json").read_text())
        assert not verify_signature(raw, headers["x-razorpay-signature"], FIXTURE_SECRET)

    def test_a_single_flipped_byte_breaks_verification(self) -> None:
        stem = committed_fixture_stems()[0]
        raw = (FIXTURES / f"{stem}.raw.json").read_bytes()
        headers = json.loads((FIXTURES / f"{stem}.headers.json").read_text())
        tampered = bytes([raw[0] ^ 0x01]) + raw[1:]
        assert not verify_signature(tampered, headers["x-razorpay-signature"], FIXTURE_SECRET)

    def test_correct_body_wrong_secret_fails(self) -> None:
        stem = committed_fixture_stems()[0]
        raw = (FIXTURES / f"{stem}.raw.json").read_bytes()
        headers = json.loads((FIXTURES / f"{stem}.headers.json").read_text())
        assert not verify_signature(raw, headers["x-razorpay-signature"], "wrong-secret")


class TestAdversarialInput:
    """A webhook endpoint has no business raising on bad input — it rejects."""

    def test_missing_signature_is_false_not_an_exception(self) -> None:
        assert verify_signature(b"{}", None, FIXTURE_SECRET) is False

    def test_empty_signature_string_is_false(self) -> None:
        assert verify_signature(b"{}", "", FIXTURE_SECRET) is False

    def test_empty_secret_is_false(self) -> None:
        assert verify_signature(b"{}", "deadbeef", "") is False

    def test_nonsense_signature_does_not_raise(self) -> None:
        assert verify_signature(b"{}", "not-hex-at-all!!", FIXTURE_SECRET) is False

    def test_empty_body_does_not_raise(self) -> None:
        assert verify_signature(b"", "deadbeef", FIXTURE_SECRET) is False
