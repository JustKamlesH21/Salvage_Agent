"""Repository behaviour against an in-memory Mongo.

mongomock is not Atlas, so this proves the code's contract, not the cluster's.
The unique-index behaviour these tests rely on is re-proved against a real
Atlas M0 on day 2, when the replay test lands (PRD §11).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

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
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from tests import db_history


@pytest.fixture
async def repos() -> Repositories:
    db = AsyncMongoMockClient()["salvage_test"]
    await apply_indexes(db)
    return Repositories(db)


@pytest.fixture
def seeded_db() -> Any:
    """Factory for a database that already has a *historical* index shape on
    it before the current `apply_indexes()` ever runs — see tests/db_history.py.
    A plain `repos` fixture always starts from an empty, freshly-created
    database, which can never represent "a database that already ran an
    earlier version of apply_indexes()" — the exact gap that hid F1.
    """
    counter = iter(range(10_000))

    async def _make(*versions: str) -> AsyncIOMotorDatabase[Any]:
        db = AsyncMongoMockClient()[f"salvage_migration_test_{next(counter)}"]
        for version in versions:
            await db_history.seed(db, version)
        await apply_indexes(db)
        return db

    return _make


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

    async def test_a_business_key_collision_whose_value_contains_prev_hash_is_not_misread(
        self, repos: Repositories
    ) -> None:
        # The exact attack _is_chain_race's fix closes: rz_event_id comes
        # from the unauthenticated X-Razorpay-Event-Id header, so an attacker
        # can make mongomock's real DuplicateKeyError string contain the
        # literal text "prev_hash" just by choosing that event id — no
        # secret needed. A substring match on str(exc) would misclassify
        # this as a chain-head race and burn all CHAIN_APPEND_ATTEMPTS
        # retries before raising a false ChainAppendExhaustedError, instead
        # of surfacing the DuplicateKeyError immediately as a genuine replay
        # must. This uses the real error mongomock raises, not a constructed
        # one, so it also proves the *value* containing "prev_hash" doesn't
        # matter now that the check reads keyPattern.
        doc = event("evil-prev_hash-evil")
        await repos.events.insert(dict(doc))
        assert "prev_hash" in str(doc["rz_event_id"])
        with pytest.raises(DuplicateKeyError):
            await repos.events.insert(dict(doc))
        assert await repos.events.count() == 1
        assert await repos.exceptions.find({}) == []

    async def test_an_unrecognisable_dup_key_error_fails_loud_not_quiet(
        self, repos: Repositories
    ) -> None:
        # F6 (compliance-auditor): a DuplicateKeyError with no keyPattern at
        # all carries no evidence either way. Treating it as "not a chain
        # race" would surface it as a plain DuplicateKeyError, which the
        # ingest layer maps to IngestOutcome.DUPLICATE — a 200 telling
        # Razorpay to stand down for an event that was never durably stored.
        # It must instead be treated as a possible chain race: retried, and
        # on exhaustion raised loudly with a visible exception-desk row.
        async def always_contended_no_details(_: dict[str, object]) -> None:
            raise DuplicateKeyError("E11000 duplicate key error, no structured details")

        repos.events._c.insert_one = always_contended_no_details  # type: ignore[method-assign]

        with pytest.raises(ChainAppendExhaustedError):
            await repos.events.insert(event("evt_unrecognisable_dup"))

        filed = await repos.exceptions.find({"reason_code": "CHAIN_APPEND_EXHAUSTED"})
        assert len(filed) == 1


class TestSignatureValidPartitionedIdempotency:
    """`events.rz_event_id` uniqueness is compound with `signature_valid`
    (day 2 fix, api/db/collections.py). A forged delivery — stored as
    forensic data, see api/ingest/webhook.py — must not permanently block a
    later legitimately-signed retry of the same event id after a webhook
    secret rotation. A replayed forgery is bounded to one stored row per
    (id, validity) pair — not growth in general, since `rz_event_id` is
    attacker-reachable and a different id sidesteps this entirely.
    """

    async def test_a_valid_retry_after_an_invalid_row_for_the_same_event_id_succeeds(
        self, repos: Repositories
    ) -> None:
        forged = event("evt_rotated")
        forged["signature_valid"] = False
        await repos.events.insert(forged)

        legit = event("evt_rotated")
        legit["signature_valid"] = True
        stored = await repos.events.insert(legit)

        assert stored["signature_valid"] is True
        assert await repos.events.count() == 2

    async def test_a_replayed_forgery_at_the_same_event_id_is_still_bounded(
        self, repos: Repositories
    ) -> None:
        forged = event("evt_replayed_forgery")
        forged["signature_valid"] = False
        await repos.events.insert(dict(forged))
        with pytest.raises(DuplicateKeyError):
            await repos.events.insert(dict(forged))
        assert await repos.events.count() == 1

    async def test_two_valid_rows_for_the_same_event_id_still_collide(
        self, repos: Repositories
    ) -> None:
        # The compound key must not weaken the original guarantee: two
        # legitimately-signed deliveries of the same event id are still one
        # stored row.
        await repos.events.insert(event("evt_valid_dup"))
        with pytest.raises(DuplicateKeyError):
            await repos.events.insert(event("evt_valid_dup"))
        assert await repos.events.count() == 1

    async def test_by_rz_event_id_returns_the_valid_row_not_the_forgery(
        self, repos: Repositories
    ) -> None:
        # F3 (compliance-auditor, day 2): with two rows now possible per
        # event id, an unfiltered find_one is ambiguous and — under natural
        # insertion order — returns whichever arrived first, which in the
        # rotation story is the forgery. api/ingest/webhook.py's own
        # design-decisions list states the rule this must not violate: "only
        # a valid signature is eligible for anything past storage."
        forged = event("evt_lookup")
        forged["signature_valid"] = False
        await repos.events.insert(forged)

        legit = event("evt_lookup")
        legit["signature_valid"] = True
        await repos.events.insert(legit)

        found = await repos.events.by_rz_event_id("evt_lookup")
        assert found is not None
        assert found["signature_valid"] is True

    async def test_by_rz_event_id_returns_none_when_only_a_forgery_exists(
        self, repos: Repositories
    ) -> None:
        forged = event("evt_only_forged")
        forged["signature_valid"] = False
        await repos.events.insert(forged)

        assert await repos.events.by_rz_event_id("evt_only_forged") is None


class TestIndexMigration:
    """A plain `repos` fixture always starts from an empty, freshly-created
    database — it can never represent "a database that already ran an
    earlier version of apply_indexes()". This class uses `seeded_db`
    (tests/db_history.py) to reproduce that missing state directly, because a
    migration bug is invisible to a fixture with no history by construction —
    a different failure mode from "the checker's scope excludes what it
    checks" (NOTES.md's first five day-2 entries). See NOTES.md's F1 entry.
    """

    async def test_a_valid_retry_after_a_forgery_succeeds_on_a_pre_fix_database(
        self, seeded_db: Any
    ) -> None:
        db = await seeded_db("day0_global_rz_event_id")
        repos = Repositories(db)

        forged = event("evt_migrated")
        forged["signature_valid"] = False
        await repos.events.insert(forged)

        legit = event("evt_migrated")
        legit["signature_valid"] = True
        stored = await repos.events.insert(legit)

        assert stored["signature_valid"] is True
        assert await repos.events.count() == 2

    async def test_a_valid_retry_after_a_forgery_succeeds_on_the_interim_partial_index_shape(
        self, seeded_db: Any
    ) -> None:
        # F9 (compliance-auditor): the two-partial-index shape was never
        # committed, but it was the working-tree-current shape for long
        # enough that a real dev cluster could carry it. _LEGACY_INDEXES
        # names it; this is the coverage that was missing for that name.
        #
        # The behavioural assertions below (signature_valid/count) pass
        # whether or not the partial indexes were actually dropped — a
        # global-unique fallback of the wrong shape passes them too, which
        # is exactly the false-positive F11 (compliance-auditor) found this
        # test had until db_history.seed() was fixed to preserve
        # partialFilterExpression. The explicit index-name assertion is
        # what actually distinguishes "migrated" from "silently wrong".
        db = await seeded_db("day2_partial_rz_event_id")
        repos = Repositories(db)

        forged = event("evt_migrated_from_partial")
        forged["signature_valid"] = False
        await repos.events.insert(forged)

        legit = event("evt_migrated_from_partial")
        legit["signature_valid"] = True
        stored = await repos.events.insert(legit)

        assert stored["signature_valid"] is True
        assert await repos.events.count() == 2

        index_names = {index["name"] async for index in db["events"].list_indexes()}
        assert index_names.isdisjoint({"uq_rz_event_id_valid", "uq_rz_event_id_invalid"})
        assert "uq_rz_event_id_signature_valid" in index_names

    async def test_a_database_carrying_every_legacy_shape_at_once_still_converges(
        self, seeded_db: Any
    ) -> None:
        # A database could plausibly carry the day-0 global index AND both
        # interim partials simultaneously (e.g. a dev cluster that lived
        # through every shape without a clean reset). apply_indexes() must
        # still converge to exactly the current compound index.
        db = await seeded_db("day0_global_rz_event_id", "day2_partial_rz_event_id")
        repos = Repositories(db)

        forged = event("evt_migrated_from_everything")
        forged["signature_valid"] = False
        await repos.events.insert(forged)

        legit = event("evt_migrated_from_everything")
        legit["signature_valid"] = True
        stored = await repos.events.insert(legit)

        assert stored["signature_valid"] is True
        assert await repos.events.count() == 2

        index_names = {index["name"] async for index in db["events"].list_indexes()}
        assert index_names.isdisjoint(
            {"uq_rz_event_id", "uq_rz_event_id_valid", "uq_rz_event_id_invalid"}
        )
        assert "uq_rz_event_id_signature_valid" in index_names
        assert await repos.events.count() == 2
