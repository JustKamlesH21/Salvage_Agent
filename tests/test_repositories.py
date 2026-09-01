"""Repository behaviour against an in-memory Mongo.

mongomock is not Atlas, so this proves the code's contract, not the cluster's.
The unique-index behaviour these tests rely on is re-proved against a real
Atlas M0 on day 2, when the replay test lands (PRD §11).
"""

from __future__ import annotations

import datetime as dt

import pytest
from api.db.chain import GENESIS_HASH, order_chain, verify_sequence
from api.db.collections import apply_indexes
from api.db.repositories import Repositories
from mongomock_motor import AsyncMongoMockClient
from pymongo.errors import DuplicateKeyError


@pytest.fixture
async def repos() -> Repositories:
    db = AsyncMongoMockClient()["salvage_test"]
    await apply_indexes(db)
    return Repositories(db)


def event(rz_event_id: str) -> dict[str, object]:
    return {
        "rz_event_id": rz_event_id,
        "event_type": "payment.failed",
        "payload": {"raw": True},
        "signature_valid": True,
        "received_at": dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.UTC),
        "processing_state": "received",
    }


class TestChainedInsert:
    async def test_first_event_links_to_genesis(self, repos: Repositories) -> None:
        stored = await repos.events.insert(event("evt_1"))
        assert stored["prev_hash"] == GENESIS_HASH
        assert len(stored["hash"]) == 64

    async def test_successive_events_form_a_verifiable_chain(self, repos: Repositories) -> None:
        for n in range(6):
            await repos.events.insert(event(f"evt_{n}"))
        documents = await repos.events.all_by_insertion()
        assert verify_sequence(order_chain(documents)) == 6

    async def test_audit_and_events_are_independent_chains(self, repos: Repositories) -> None:
        await repos.events.insert(event("evt_1"))
        row = await repos.audit.insert(
            {"idempotency_key": "case:dec:A1", "phase": "pre", "action": "A1"}
        )
        assert row["prev_hash"] == GENESIS_HASH


class TestIdempotency:
    async def test_replayed_event_id_is_rejected(self, repos: Repositories) -> None:
        await repos.events.insert(event("evt_dup"))
        with pytest.raises(DuplicateKeyError):
            await repos.events.insert(event("evt_dup"))

    async def test_replayed_event_leaves_one_document(self, repos: Repositories) -> None:
        await repos.events.insert(event("evt_dup"))
        for _ in range(5):
            with pytest.raises(DuplicateKeyError):
                await repos.events.insert(event("evt_dup"))
        assert await repos.events.count() == 1

    async def test_second_pre_row_for_the_same_key_is_rejected(self, repos: Repositories) -> None:
        # This is the executor's dedupe guard: DuplicateKeyError on the pre row
        # means "already executed" (invariant 9).
        row = {"idempotency_key": "case:dec:A1", "phase": "pre", "action": "A1"}
        await repos.audit.insert(row)
        with pytest.raises(DuplicateKeyError):
            await repos.audit.insert(dict(row))

    async def test_post_row_shares_the_key_with_its_pre_row(self, repos: Repositories) -> None:
        # Unique is on (idempotency_key, phase), not on the key alone, so the
        # pre/post pair SPEC §5.6 writes is storable.
        await repos.audit.insert({"idempotency_key": "k", "phase": "pre", "action": "A1"})
        await repos.audit.insert(
            {"idempotency_key": "k", "phase": "post", "action": "A1", "response": {"ok": True}}
        )
        assert await repos.audit.count() == 2

    async def test_prior_result_returns_the_post_row(self, repos: Repositories) -> None:
        await repos.audit.insert({"idempotency_key": "k", "phase": "pre", "action": "A1"})
        await repos.audit.insert(
            {"idempotency_key": "k", "phase": "post", "action": "A1", "response": {"ok": True}}
        )
        prior = await repos.audit.prior_result("k")
        assert prior is not None and prior["response"] == {"ok": True}

    async def test_prior_result_is_none_when_dispatch_never_completed(
        self, repos: Repositories
    ) -> None:
        # A pre row with no post row means the dispatch is in flight or died.
        # The caller must not read None as "no prior attempt" and re-dispatch.
        await repos.audit.insert({"idempotency_key": "k", "phase": "pre", "action": "A1"})
        assert await repos.audit.prior_result("k") is None


class TestCaseTransitions:
    async def test_transition_from_the_expected_state_succeeds(self, repos: Repositories) -> None:
        case_id = await repos.cases.insert({"state": "open", "subscription_id": "sub_1"})
        moved = await repos.cases.transition(case_id, expect="open", to="recovering")
        assert moved is not None and moved["state"] == "recovering"

    async def test_transition_from_the_wrong_state_returns_none(self, repos: Repositories) -> None:
        # None means someone else moved it. PRD §8.5: handle that path.
        case_id = await repos.cases.insert({"state": "recovered", "subscription_id": "sub_1"})
        assert await repos.cases.transition(case_id, expect="open", to="recovering") is None
