"""FR-01/FR-02/FR-03 against a real in-memory Mongo. Uses the committed
fixtures where a real payload matters, and hand-built bytes where the test is
about a specific edge (malformed JSON, missing headers, storage failure).

Two tests exercise the same underlying claim — that a chain-head collision is
recovered by the retry loop without a fork — by different means, and they are
not redundant with each other on purpose:

  * `test_chain_head_collision_recovers_deterministic_authoritative` forces
    the collision deterministically (monkeypatched insert_one) and is the
    test that actually proves the mechanism. It cannot pass by accident.
  * `test_concurrent_ingest_writers_smoke_not_authoritative` fires real
    concurrent coroutines at a shared mongomock backend. It is a useful
    integration-level sanity check, but mongomock's operations may not
    interleave the same way a real server under load would, so it can pass
    without the retry path ever actually firing. Do not delete the
    deterministic test because this one looks redundant — it is not proof of
    anything on its own.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

import pytest
from api.db.chain import order_chain, verify_sequence
from api.db.collections import apply_indexes
from api.db.repositories import Repositories
from api.ingest.webhook import IngestOutcome, ingest_webhook
from mongomock_motor import AsyncMongoMockClient
from pymongo.errors import DuplicateKeyError
from scripts.redact_fixtures import FIXTURE_SECRET

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture
async def repos() -> Repositories:
    db = AsyncMongoMockClient()["salvage_ingest_test"]
    await apply_indexes(db)
    return Repositories(db)


def load_fixture(stem: str) -> tuple[bytes, dict[str, str]]:
    raw = (FIXTURES / f"{stem}.raw.json").read_bytes()
    headers = json.loads((FIXTURES / f"{stem}.headers.json").read_text())
    return raw, headers


def sign(body: bytes, secret: str = FIXTURE_SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def headers_for(
    body: bytes, *, event_id: str = "evt_test_1", secret: str = FIXTURE_SECRET
) -> dict[str, str]:
    return {
        "X-Razorpay-Signature": sign(body, secret),
        "X-Razorpay-Event-Id": event_id,
        "Content-Type": "application/json",
    }


def chain_race_error() -> DuplicateKeyError:
    """A DuplicateKeyError shaped like what pymongo actually raises on the
    `(run_id, prev_hash)` chain index — `details["keyPattern"]` set, not just
    a message string. `_is_chain_race` (api/db/repositories.py) reads that
    structured field rather than substring-matching the message, precisely so
    an attacker-controlled value containing the text "prev_hash" cannot be
    mistaken for a real chain collision. A test double that only sets the
    message no longer exercises the retry path this class is testing.
    """
    return DuplicateKeyError(
        "E11000 duplicate key error collection: salvage.events index: "
        'uq_events_run_prev_hash dup key: { run_id: null, prev_hash: "..." }',
        details={
            "keyPattern": {"run_id": 1, "prev_hash": 1},
            "keyValue": {"run_id": None, "prev_hash": "..."},
        },
    )


class TestValidSignature:
    async def test_a_real_fixture_is_accepted_and_stored(self, repos: Repositories) -> None:
        raw, headers = load_fixture("006-subscription_charged")
        result = await ingest_webhook(raw, headers, repos.events, webhook_secret=FIXTURE_SECRET)
        assert result.outcome is IngestOutcome.RECEIVED
        assert result.document is not None
        assert result.document["signature_valid"] is True
        assert result.document["processing_state"] == "received"

    async def test_event_type_is_extracted_from_the_body(self, repos: Repositories) -> None:
        raw, headers = load_fixture("006-subscription_charged")
        result = await ingest_webhook(raw, headers, repos.events, webhook_secret=FIXTURE_SECRET)
        assert result.document is not None
        assert result.document["event_type"] == "subscription.charged"

    async def test_rz_event_id_comes_from_the_header_not_the_body(
        self, repos: Repositories
    ) -> None:
        raw, headers = load_fixture("006-subscription_charged")
        result = await ingest_webhook(raw, headers, repos.events, webhook_secret=FIXTURE_SECRET)
        assert result.document is not None
        assert result.document["rz_event_id"] == headers["x-razorpay-event-id"]

    async def test_received_at_is_utc_aware(self, repos: Repositories) -> None:
        raw, headers = load_fixture("006-subscription_charged")
        result = await ingest_webhook(raw, headers, repos.events, webhook_secret=FIXTURE_SECRET)
        assert result.document is not None
        assert result.document["received_at"].tzinfo is not None


class TestInvalidSignature:
    async def test_the_tampered_fixture_is_rejected(self, repos: Repositories) -> None:
        raw, headers = load_fixture("tampered")
        result = await ingest_webhook(raw, headers, repos.events, webhook_secret=FIXTURE_SECRET)
        assert result.outcome is IngestOutcome.INVALID_SIGNATURE

    async def test_but_the_tampered_delivery_is_still_stored(self, repos: Repositories) -> None:
        # PRD §8.2's signature_valid field only makes sense if invalid
        # deliveries are stored too — a forged/misconfigured delivery is
        # itself forensic data.
        raw, headers = load_fixture("tampered")
        before = await repos.events.count()
        await ingest_webhook(raw, headers, repos.events, webhook_secret=FIXTURE_SECRET)
        after = await repos.events.count()
        assert after == before + 1

    async def test_the_stored_document_records_signature_valid_false(
        self, repos: Repositories
    ) -> None:
        raw, headers = load_fixture("tampered")
        result = await ingest_webhook(raw, headers, repos.events, webhook_secret=FIXTURE_SECRET)
        assert result.document is not None
        assert result.document["signature_valid"] is False


class TestDedupe:
    async def test_the_same_event_id_replayed_100_times_produces_one_event(
        self, repos: Repositories
    ) -> None:
        raw, headers = load_fixture("006-subscription_charged")
        outcomes = []
        for _ in range(100):
            result = await ingest_webhook(raw, headers, repos.events, webhook_secret=FIXTURE_SECRET)
            outcomes.append(result.outcome)
        assert outcomes[0] is IngestOutcome.RECEIVED
        assert all(o is IngestOutcome.DUPLICATE for o in outcomes[1:])
        assert await repos.events.count() == 1

    async def test_dedupe_is_insert_first_not_check_then_insert(self, repos: Repositories) -> None:
        # The repository's insert() relies on catching DuplicateKeyError from
        # the unique index, not on a preceding find(); this test fails if
        # ingest_webhook were rewritten to check-then-insert, since that
        # rewrite would still pass the 100x replay test above (both styles
        # produce one event) but would reintroduce the exact race FR-02 exists
        # to close.
        raw, headers = load_fixture("006-subscription_charged")
        await ingest_webhook(raw, headers, repos.events, webhook_secret=FIXTURE_SECRET)
        with pytest.raises(DuplicateKeyError):
            await repos.events.insert(
                {
                    "rz_event_id": headers["x-razorpay-event-id"],
                    "event_type": "x",
                    "payload": {},
                    "signature_valid": True,
                    "received_at": None,
                    "processing_state": "received",
                }
            )


class TestChainIntegrity:
    async def test_the_chain_verifies_after_a_batch_of_distinct_inserts(
        self, repos: Repositories
    ) -> None:
        stems = [
            "001-payment_authorized",
            "020-payment_failed",
            "021-subscription_halted",
            "033-payment_link_paid",
        ]
        for stem in stems:
            raw, headers = load_fixture(stem)
            result = await ingest_webhook(raw, headers, repos.events, webhook_secret=FIXTURE_SECRET)
            assert result.outcome is IngestOutcome.RECEIVED

        stored = await repos.events.all_by_insertion()
        assert verify_sequence(order_chain(stored)) == len(stems)


class TestChainHeadCollision:
    async def test_chain_head_collision_recovers_deterministic_authoritative(
        self, repos: Repositories
    ) -> None:
        """The real proof. A forced, deterministic collision on the first
        attempt must be absorbed by the existing retry loop, and the chain
        must still verify afterward with no fork.
        """
        raw, headers = load_fixture("006-subscription_charged")
        real_insert_one = repos.events._c.insert_one
        call_count = 0

        async def flaky_insert_one(document: dict[str, Any]) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise chain_race_error()
            return await real_insert_one(document)

        repos.events._c.insert_one = flaky_insert_one  # type: ignore[method-assign]

        result = await ingest_webhook(raw, headers, repos.events, webhook_secret=FIXTURE_SECRET)

        assert result.outcome is IngestOutcome.RECEIVED
        assert call_count == 2, "the collision must have been retried exactly once"
        stored = await repos.events.all_by_insertion()
        assert verify_sequence(order_chain(stored)) == 1

    async def test_concurrent_ingest_writers_smoke_not_authoritative(
        self, repos: Repositories
    ) -> None:
        """Not authoritative — see module docstring. A useful sanity check,
        not proof the retry mechanism works.
        """
        bodies_and_headers = [
            (f'{{"event": "test.smoke", "n": {n}}}'.encode(), None) for n in range(10)
        ]
        tasks = []
        for n, (body, _) in enumerate(bodies_and_headers):
            h = headers_for(body, event_id=f"evt_smoke_{n}")
            tasks.append(ingest_webhook(body, h, repos.events, webhook_secret=FIXTURE_SECRET))
        results = await asyncio.gather(*tasks)

        assert all(r.outcome is IngestOutcome.RECEIVED for r in results)
        assert await repos.events.count() == 10
        stored = await repos.events.all_by_insertion()
        assert verify_sequence(order_chain(stored)) == 10


class TestStorageFailure:
    async def test_exhausted_chain_append_returns_storage_failed_not_received(
        self, repos: Repositories
    ) -> None:
        raw, headers = load_fixture("006-subscription_charged")

        async def always_contended(_: dict[str, Any]) -> None:
            raise chain_race_error()

        repos.events._c.insert_one = always_contended  # type: ignore[method-assign]

        result = await ingest_webhook(raw, headers, repos.events, webhook_secret=FIXTURE_SECRET)

        assert result.outcome is IngestOutcome.STORAGE_FAILED
        assert result.document is None
        assert result.detail is not None

    async def test_a_storage_failure_files_to_the_exception_desk(self, repos: Repositories) -> None:
        # events.insert()'s own exhaustion path files before raising; this
        # confirms ingest doesn't swallow that behaviour on the way through.
        raw, headers = load_fixture("006-subscription_charged")

        async def always_contended(_: dict[str, Any]) -> None:
            raise chain_race_error()

        repos.events._c.insert_one = always_contended  # type: ignore[method-assign]
        await ingest_webhook(raw, headers, repos.events, webhook_secret=FIXTURE_SECRET)

        filed = await repos.exceptions.find({"reason_code": "CHAIN_APPEND_EXHAUSTED"})
        assert len(filed) == 1


class TestSparsePayload:
    async def test_a_failure_payload_missing_error_fields_ingests_cleanly(
        self, repos: Repositories
    ) -> None:
        # Matches what day 1 actually observed: error_source/error_step/
        # error_reason can all be null on a real payment.failed. Storage must
        # treat that as routine, not as an error path.
        body = json.dumps(
            {
                "event": "payment.failed",
                "payload": {
                    "payment": {
                        "entity": {
                            "id": "pay_sparse_test",
                            "amount": 1500000,
                            "status": "failed",
                            "error_code": "BAD_REQUEST_ERROR",
                            "error_description": "Mandate debit not as per frequency",
                            "error_source": None,
                            "error_step": None,
                            "error_reason": None,
                        }
                    }
                },
            }
        ).encode()
        headers = headers_for(body, event_id="evt_sparse_1")

        result = await ingest_webhook(body, headers, repos.events, webhook_secret=FIXTURE_SECRET)

        assert result.outcome is IngestOutcome.RECEIVED
        assert result.document is not None
        entity = result.document["payload"]["payload"]["payment"]["entity"]
        assert entity["error_source"] is None
        assert entity["error_step"] is None
        assert entity["error_reason"] is None
        assert entity["error_code"] == "BAD_REQUEST_ERROR"


class TestMalformedBody:
    async def test_malformed_json_with_a_valid_signature_is_handled_not_500(
        self, repos: Repositories
    ) -> None:
        body = b"this is not json at all {{{ garbage"
        headers = headers_for(body, event_id="evt_malformed_1")

        # No exception raised is the test — a 500 would surface as one here.
        result = await ingest_webhook(body, headers, repos.events, webhook_secret=FIXTURE_SECRET)

        assert result.outcome is IngestOutcome.RECEIVED
        assert result.document is not None
        assert result.document["event_type"] is None
        assert result.document["payload"] == body.decode("utf-8")

    async def test_malformed_body_proves_verification_ran_before_any_parse_attempt(
        self, repos: Repositories
    ) -> None:
        # If verification ran AFTER a parse attempt, this request would never
        # reach the signature check at all — json.loads() would raise first,
        # and (were that bug present) the ordering violation would surface as
        # an unhandled exception here rather than as the correct
        # signature_valid: True this asserts.
        body = b"{not valid json"
        headers = headers_for(body, event_id="evt_malformed_2")
        result = await ingest_webhook(body, headers, repos.events, webhook_secret=FIXTURE_SECRET)
        assert result.document is not None
        assert result.document["signature_valid"] is True

    async def test_non_utf8_garbage_does_not_raise_either(self, repos: Repositories) -> None:
        body = b"\xff\xfe\x00\x01not-utf8-and-not-json"
        headers = headers_for(body, event_id="evt_malformed_3")
        result = await ingest_webhook(body, headers, repos.events, webhook_secret=FIXTURE_SECRET)
        assert result.outcome is IngestOutcome.RECEIVED
        assert result.document is not None
        assert isinstance(result.document["payload"], str)


class TestMissingEventIdHeader:
    async def test_missing_event_id_falls_back_to_a_content_hash(self, repos: Repositories) -> None:
        body = b'{"event": "no.header.test"}'
        signature = sign(body)
        headers = {"X-Razorpay-Signature": signature}  # no event-id header
        result = await ingest_webhook(body, headers, repos.events, webhook_secret=FIXTURE_SECRET)
        assert result.outcome is IngestOutcome.RECEIVED
        assert result.document is not None
        assert result.document["rz_event_id"].startswith("missing-event-id:")

    async def test_identical_headerless_bodies_still_dedupe(self, repos: Repositories) -> None:
        body = b'{"event": "no.header.test"}'
        headers = {"X-Razorpay-Signature": sign(body)}
        first = await ingest_webhook(body, headers, repos.events, webhook_secret=FIXTURE_SECRET)
        second = await ingest_webhook(body, headers, repos.events, webhook_secret=FIXTURE_SECRET)
        assert first.outcome is IngestOutcome.RECEIVED
        assert second.outcome is IngestOutcome.DUPLICATE

    async def test_distinct_headerless_bodies_do_not_collide(self, repos: Repositories) -> None:
        body_a = b'{"event": "no.header.test", "n": 1}'
        body_b = b'{"event": "no.header.test", "n": 2}'
        result_a = await ingest_webhook(
            body_a,
            {"X-Razorpay-Signature": sign(body_a)},
            repos.events,
            webhook_secret=FIXTURE_SECRET,
        )
        result_b = await ingest_webhook(
            body_b,
            {"X-Razorpay-Signature": sign(body_b)},
            repos.events,
            webhook_secret=FIXTURE_SECRET,
        )
        assert result_a.outcome is IngestOutcome.RECEIVED
        assert result_b.outcome is IngestOutcome.RECEIVED
        assert await repos.events.count() == 2
