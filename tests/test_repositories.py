"""Repository behaviour against an in-memory Mongo.

mongomock is not Atlas, so this proves the code's contract, not the cluster's.
The unique-index behaviour these tests rely on is re-proved against a real
Atlas M0 on day 2, when the replay test lands (PRD §11).
"""

from __future__ import annotations

import datetime as dt

import pytest
from api.db.chain import (
    GENESIS_HASH,
    ChainAppendExhaustedError,
    order_chain,
    partition_chains,
    verify_sequence,
)
from api.db.collections import apply_indexes
from api.db.repositories import (
    CHAIN_APPEND_ATTEMPTS,
    CHAIN_RETRY_BASE_SECONDS,
    CHAIN_RETRY_CAP_SECONDS,
    Repositories,
    _backoff,
)
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


class TestPartitionedChains:
    async def test_two_runs_write_independent_chains(self, repos: Repositories) -> None:
        for n in range(3):
            await repos.audit.insert({"idempotency_key": f"a{n}", "phase": "pre"}, run_id="run_a")
            await repos.audit.insert({"idempotency_key": f"b{n}", "phase": "pre"}, run_id="run_b")
        partitions = partition_chains(await repos.audit.all_by_insertion())
        assert sorted(partitions) == ["run_a", "run_b"]
        for documents in partitions.values():
            assert verify_sequence(order_chain(documents)) == 3

    async def test_each_run_starts_its_own_chain_at_genesis(self, repos: Repositories) -> None:
        # Under a global unique index on prev_hash alone, this second insert
        # would collide with the first run's genesis row and never land.
        a = await repos.audit.insert({"idempotency_key": "a", "phase": "pre"}, run_id="run_a")
        b = await repos.audit.insert({"idempotency_key": "b", "phase": "pre"}, run_id="run_b")
        assert a["prev_hash"] == b["prev_hash"] == GENESIS_HASH

    async def test_the_live_chain_is_run_id_none(self, repos: Repositories) -> None:
        stored = await repos.events.insert(event("evt_live"))
        assert stored["run_id"] is None

    async def test_run_id_is_written_explicitly_not_left_missing(self, repos: Repositories) -> None:
        # A missing field and an explicit null index identically, but the hash
        # is computed over the body — so the field has to be present and
        # consistent or two logically identical rows hash differently.
        await repos.events.insert(event("evt_1"))
        stored = await repos.events.all_by_insertion()
        assert "run_id" in stored[0]

    async def test_a_conflicting_run_id_is_refused(self, repos: Repositories) -> None:
        with pytest.raises(ValueError, match="wrong partition"):
            await repos.audit.insert(
                {"idempotency_key": "k", "phase": "pre", "run_id": "run_a"}, run_id="run_b"
            )

    async def test_a_matching_run_id_in_the_body_is_accepted(self, repos: Repositories) -> None:
        stored = await repos.audit.insert(
            {"idempotency_key": "k", "phase": "pre", "run_id": "run_a"}, run_id="run_a"
        )
        assert stored["run_id"] == "run_a"

    async def test_counts_can_be_scoped_to_one_run(self, repos: Repositories) -> None:
        await repos.audit.insert({"idempotency_key": "a", "phase": "pre"}, run_id="run_a")
        await repos.audit.insert({"idempotency_key": "b", "phase": "pre"}, run_id="run_b")
        assert await repos.audit.count(run_id="run_a") == 1
        assert await repos.audit.count() == 2


class TestBackoff:
    def test_jitter_spreads_the_retry(self) -> None:
        # Deterministic backoff makes two colliding writers re-collide on the
        # same schedule. Distinct values are the property that matters.
        samples = {_backoff(3) for _ in range(200)}
        assert len(samples) > 100

    def test_delay_is_bounded_by_the_cap(self) -> None:
        assert all(0 <= _backoff(n) <= CHAIN_RETRY_CAP_SECONDS for n in range(20))

    def test_worst_case_total_stays_inside_the_batch_budget(self) -> None:
        # NFR-02 gives the whole 500-case batch three minutes. The retry loop
        # must not be able to eat a meaningful slice of it.
        worst = sum(
            min(CHAIN_RETRY_CAP_SECONDS, CHAIN_RETRY_BASE_SECONDS * 2**n)
            for n in range(CHAIN_APPEND_ATTEMPTS)
        )
        assert worst < 1.0


class TestAppendExhaustion:
    async def test_exhaustion_files_to_the_exception_desk_and_raises(
        self, repos: Repositories
    ) -> None:
        # Force every attempt to lose the head race.
        async def always_contended(_: dict[str, object]) -> None:
            # keyPattern, not just a string mentioning the field name: the
            # chain-race classifier reads structured driver details, since
            # the dup-key VALUE (attacker-controlled for events.rz_event_id)
            # must never be able to spoof this by containing "prev_hash"
            # as text. See api/db/repositories.py's _is_chain_race.
            raise DuplicateKeyError(
                "E11000 duplicate key error ... index: uq_audit_run_prev_hash",
                details={"keyPattern": {"run_id": 1, "prev_hash": 1}},
            )

        repos.audit._c.insert_one = always_contended  # type: ignore[method-assign]

        with pytest.raises(ChainAppendExhaustedError) as caught:
            await repos.audit.insert({"idempotency_key": "k", "phase": "pre", "case_id": "c1"})

        # The row was not written, and it did not vanish either.
        assert caught.value.document["idempotency_key"] == "k"
        assert "exception desk" in str(caught.value)

        filed = await repos.exceptions.find({"reason_code": "CHAIN_APPEND_EXHAUSTED"})
        assert len(filed) == 1
        assert filed[0]["case_id"] == "c1"
        assert filed[0]["detail"]["unwritten_document"]["idempotency_key"] == "k"
        assert filed[0]["detail"]["collection"] == "audit"

    async def test_a_business_key_collision_is_not_retried(self, repos: Repositories) -> None:
        # DuplicateKeyError on rz_event_id is the idempotency signal, not a
        # head race. It must surface immediately, unretried and unfiled.
        await repos.events.insert(event("evt_dup"))
        with pytest.raises(DuplicateKeyError):
            await repos.events.insert(event("evt_dup"))
        assert await repos.exceptions.find({}) == []
