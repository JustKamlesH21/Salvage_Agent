"""Collection names and index definitions from PRD §8.3, applied at startup.

Two indexes here are additions to PRD §8.3. Both are recorded in that section
with their reasons:

  * `events.prev_hash` and `audit.prev_hash` UNIQUE — two documents cannot claim
    the same predecessor, so a concurrent double-append fails with
    DuplicateKeyError instead of silently forking the chain. The loser retries
    against the new head. This is the same "unique index instead of a lock"
    move the PRD already makes for idempotency.

  * `audit` idempotency is UNIQUE on (idempotency_key, phase) rather than on
    idempotency_key alone, because SPEC §5.6 writes a `pre` row and a `post` row
    carrying the same key. A unique index on the key alone would make the post
    row impossible to insert. The dedupe property invariant 9 depends on is
    unchanged: a second `pre` insert for the same key still raises
    DuplicateKeyError, which still means "already executed".
"""

from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING, IndexModel

EVENTS = "events"
CASES = "cases"
DECISIONS = "decisions"
AUDIT = "audit"
CUSTOMERS = "customers"
EXCEPTIONS = "exceptions"
RUNS = "runs"

# Append-only. Their repositories expose insert only, and scripts/check_boundaries.py
# fails the build on any update/delete against them (PRD §8.4, invariant 6).
APPEND_ONLY = frozenset({EVENTS, AUDIT})

INDEXES: dict[str, list[IndexModel]] = {
    EVENTS: [
        # The webhook idempotency guard. FR-02: same rz_event_id replayed 100x
        # produces one case.
        IndexModel([("rz_event_id", ASCENDING)], unique=True, name="uq_rz_event_id"),
        IndexModel([("received_at", DESCENDING)], name="ix_received_at"),
        IndexModel([("prev_hash", ASCENDING)], unique=True, name="uq_events_prev_hash"),
    ],
    CASES: [
        IndexModel([("state", ASCENDING), ("opened_at", DESCENDING)], name="ix_state_opened"),
        IndexModel([("subscription_id", ASCENDING)], name="ix_subscription"),
        IndexModel([("run_id", ASCENDING), ("state", ASCENDING)], name="ix_run_state"),
    ],
    DECISIONS: [
        IndexModel([("case_id", ASCENDING), ("created_at", ASCENDING)], name="ix_case_created"),
        IndexModel([("run_id", ASCENDING)], name="ix_run"),
    ],
    AUDIT: [
        # The executor dedupe guard. Insert-first makes double-dispatch
        # structurally impossible rather than checked-then-raced (invariant 9).
        IndexModel(
            [("idempotency_key", ASCENDING), ("phase", ASCENDING)],
            unique=True,
            name="uq_idempotency_phase",
        ),
        IndexModel([("case_id", ASCENDING), ("ts", ASCENDING)], name="ix_case_ts"),
        IndexModel([("prev_hash", ASCENDING)], unique=True, name="uq_audit_prev_hash"),
    ],
    # customers uses customer_id as _id, so PRD §8.3's {_id: 1} is the implicit
    # index MongoDB already creates. Nothing to declare.
    CUSTOMERS: [],
    EXCEPTIONS: [
        IndexModel([("case_id", ASCENDING)], name="ix_case"),
    ],
    RUNS: [],
}


async def apply_indexes(db: AsyncIOMotorDatabase[Any]) -> dict[str, list[str]]:
    """Create every declared index. Idempotent — safe to run on every startup."""
    created: dict[str, list[str]] = {}
    for name, models in INDEXES.items():
        if not models:
            created[name] = []
            continue
        created[name] = list(await db[name].create_indexes(models))
    return created
