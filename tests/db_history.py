"""Historical index shapes this project has shipped, for testing migrations.

A fresh mongomock database can never represent "a database that already ran
an earlier version of apply_indexes()" — every fixture starts empty. This
module is the seed for that missing state: one entry per index shape that
was ever the *current* one in api/db/collections.py, so a regression test
can reproduce exactly what apply_indexes() must migrate away from.

Add an entry here whenever an index in api/db/collections.py's INDEXES is
renamed, dropped, or reshaped. Never edit or remove an existing entry — it is
a historical record of what a real, already-deployed database looked like at
that point, not a place to keep something current. See NOTES.md's day 2 "F1"
entry for why this exists: the first compliance-auditor pass on the
rz_event_id partial-index fix found a real migration bug that no test in this
suite could have caught, because every fixture builds a database with no
past.
"""

from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING, IndexModel

HISTORICAL_INDEXES: dict[str, dict[str, list[IndexModel]]] = {
    # api/db/collections.py, day 0 through day 2 pre-fix: rz_event_id
    # uniqueness was a single global index. Superseded by the compound
    # (rz_event_id, signature_valid) index — see collections.py's docstring.
    "day0_global_rz_event_id": {
        "events": [
            IndexModel([("rz_event_id", ASCENDING)], unique=True, name="uq_rz_event_id"),
        ],
    },
    # api/db/collections.py, an interim day-2 shape: two partial unique
    # indexes, one per signature_valid value, in place between the global
    # index above and the compound index that replaced both. Never
    # committed (git log -S confirms it), but it was the working-tree state
    # for long enough that a real dev cluster could carry these two names —
    # compliance-auditor (F9) flagged that this shape had no entry here,
    # meaning _drop_legacy_indexes' coverage of it was unverified even
    # though _LEGACY_INDEXES lists both names.
    "day2_partial_rz_event_id": {
        "events": [
            IndexModel(
                [("rz_event_id", ASCENDING)],
                unique=True,
                name="uq_rz_event_id_valid",
                partialFilterExpression={"signature_valid": True},
            ),
            IndexModel(
                [("rz_event_id", ASCENDING)],
                unique=True,
                name="uq_rz_event_id_invalid",
                partialFilterExpression={"signature_valid": False},
            ),
        ],
    },
}


async def seed(db: AsyncIOMotorDatabase[Any], version: str) -> None:
    """Apply one historical index shape directly, bypassing the current
    apply_indexes() — the point is to reproduce what an old database already
    has on disk, not what the current code would create.

    Creates each index individually via `create_index()`, not a single bulk
    `create_indexes(IndexModel[...])` call: mongomock's bulk path silently
    drops `partialFilterExpression` (the same defect `api/db/collections.py`
    already routes around for the same reason — see its module docstring).
    `"day2_partial_rz_event_id"` used the bulk form until compliance-auditor
    (F11) reproduced that it seeded two plain global-unique indexes instead
    of the real partial pair, which made the migration test for that shape
    pass for the wrong reason — it was silently exercising the day-0 shape a
    second time under different names, the exact "fixture cannot represent
    the state it claims to" failure this whole module exists to prevent
    (see NOTES.md's F1 entry), recurring inside its own fix.
    """
    for collection_name, models in HISTORICAL_INDEXES[version].items():
        for model in models:
            options = {k: v for k, v in model.document.items() if k not in ("key", "v")}
            keys = list(model.document["key"].items())
            await db[collection_name].create_index(keys, **options)
