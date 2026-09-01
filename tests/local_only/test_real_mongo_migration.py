"""Does the rz_event_id index migration actually behave against real MongoDB?

Everything in tests/test_repositories.py proves the migration's contract
against mongomock. mongomock is not MongoDB: it has already been wrong twice
in this exact area — it silently drops `partialFilterExpression` on the bulk
`create_indexes` path (api/db/collections.py's and tests/db_history.py's
module docstrings both route around this), and it does not model partial
indexes' actual constraint semantics at all, so it cannot exhibit the one
failure mode compliance-auditor's F12 finding depends on: that the two
partial indexes and the compound index that replaced them disagree on
documents where `signature_valid` is missing or non-boolean.

This is the test that closes that gap, against a real `mongod`. It can run
locally (skipped automatically if nothing is listening on 127.0.0.1:27017 or
$MONGO_TEST_URI) and it must never gate CI — a real database dependency has
no business in the standard suite. Run it by hand and record the result in
NOTES.md, same convention as tests/local_only/test_real_signature.py:

    .venv/bin/pytest tests/local_only/test_real_mongo_migration.py -v

Not being able to run it is not a pass. If it never runs, F12's residual risk
is still open and NOTES.md should say so.
"""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError, PyMongoError, ServerSelectionTimeoutError

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.db.collections import EVENTS, INDEXES, apply_indexes  # noqa: E402
from api.db.repositories import Repositories  # noqa: E402
from tests import db_history  # noqa: E402

MONGO_TEST_URI = os.environ.get("MONGO_TEST_URI", "mongodb://127.0.0.1:27017")


def _reachable() -> bool:
    """Synchronous on purpose: pytestmark below runs at collection time,
    before any event loop exists, so this uses pymongo's sync client rather
    than motor purely to check the server is there.
    """
    from pymongo import MongoClient

    client: MongoClient[Any] = MongoClient(MONGO_TEST_URI, serverSelectionTimeoutMS=500)
    try:
        client.admin.command("ping")
        return True
    except (PyMongoError, ServerSelectionTimeoutError, OSError):
        return False
    finally:
        client.close()


pytestmark = pytest.mark.skipif(
    not _reachable(), reason=f"no reachable MongoDB at {MONGO_TEST_URI}"
)


@pytest.fixture
async def real_db() -> AsyncIterator[AsyncIOMotorDatabase[Any]]:
    client: AsyncIOMotorClient[Any] = AsyncIOMotorClient(MONGO_TEST_URI)
    name = f"salvage_migration_real_test_{uuid.uuid4().hex[:8]}"
    db = client[name]
    try:
        yield db
    finally:
        await client.drop_database(name)
        client.close()


async def test_the_partial_legacy_shape_admits_non_boolean_data_the_compound_index_rejects(
    real_db: AsyncIOMotorDatabase[Any],
) -> None:
    """F12 (compliance-auditor). This is the failure a corrected docstring in
    api/db/collections.py now describes but mongomock cannot demonstrate:
    the partial legacy shape and the compound replacement disagree on
    documents where signature_valid is missing.
    """
    await db_history.seed(real_db, "day2_partial_rz_event_id")
    repos = Repositories(real_db)

    # Both partial indexes only constrain documents matching their filter.
    # Neither has an opinion on a document with no signature_valid field at
    # all, so both inserts succeed under the legacy shape. Through the real
    # repository, not a raw insert_one, so each gets a proper chain link —
    # a raw insert_one with no prev_hash would collide with itself on
    # uq_events_run_prev_hash the moment that index exists, which has
    # nothing to do with the rz_event_id behaviour this test is about.
    await repos.events.insert({"rz_event_id": "evt_no_signature_field"})
    await repos.events.insert({"rz_event_id": "evt_no_signature_field"})
    assert await real_db[EVENTS].count_documents({"rz_event_id": "evt_no_signature_field"}) == 2

    # apply_indexes() creates-before-drops (see its docstring), so this must
    # fail at the create step — and the failure must be the *safe* one: the
    # legacy guard stays fully in place, nothing is silently lost.
    with pytest.raises(DuplicateKeyError):
        await apply_indexes(real_db)

    surviving = {index["name"] async for index in real_db[EVENTS].list_indexes()}
    assert {"uq_rz_event_id_valid", "uq_rz_event_id_invalid"} <= surviving
    assert "uq_rz_event_id_signature_valid" not in surviving


async def test_migration_from_every_legacy_shape_converges_on_real_mongo(
    real_db: AsyncIOMotorDatabase[Any],
) -> None:
    """The end-to-end migration story, proven against real MongoDB rather
    than mongomock's approximation of it — on realistic data (a strict bool
    signature_valid on every row, exactly what verify_signature() produces),
    where the F12 failure mode above does not apply.
    """
    await db_history.seed(real_db, "day0_global_rz_event_id")
    await db_history.seed(real_db, "day2_partial_rz_event_id")
    repos = Repositories(real_db)

    forged = {"rz_event_id": "evt_real_mongo_migration", "signature_valid": False}
    await repos.events.insert(forged)

    await apply_indexes(real_db)

    legit = {"rz_event_id": "evt_real_mongo_migration", "signature_valid": True}
    await repos.events.insert(legit)

    assert await real_db[EVENTS].count_documents({"rz_event_id": "evt_real_mongo_migration"}) == 2

    index_names = {index["name"] async for index in real_db[EVENTS].list_indexes()}
    assert index_names.isdisjoint(
        {"uq_rz_event_id", "uq_rz_event_id_valid", "uq_rz_event_id_invalid"}
    )
    assert "uq_rz_event_id_signature_valid" in index_names

    # apply_indexes() must also be safely re-runnable — the "idempotent"
    # claim in its docstring, checked here against a real server rather
    # than assumed.
    await apply_indexes(real_db)
    await apply_indexes(real_db)
    unchanged = {index["name"] async for index in real_db[EVENTS].list_indexes()}
    assert unchanged == index_names


async def test_declared_indexes_are_actually_created_on_a_fresh_real_database(
    real_db: AsyncIOMotorDatabase[Any],
) -> None:
    """Sanity check that INDEXES itself round-trips through the real driver
    with no option pymongo/MongoDB rejects — mongomock is permissive about
    index option combinations in ways a real server is not.
    """
    result = await apply_indexes(real_db)
    for collection_name, models in INDEXES.items():
        expected_names = {model.document["name"] for model in models}
        assert expected_names <= set(result[collection_name])
