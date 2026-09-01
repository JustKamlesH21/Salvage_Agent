"""Collection names and index definitions from PRD §8.3, applied at startup.

Two indexes here are additions to PRD §8.3. Both are recorded in that section
with their reasons:

  * `(run_id, prev_hash)` UNIQUE on both chained collections — two documents
    cannot claim the same predecessor *within a chain*, so a concurrent
    double-append fails with DuplicateKeyError instead of silently forking. The
    loser retries against the new head. This is the same "unique index instead
    of a lock" move the PRD already makes for idempotency.

    The chain is partitioned by run_id rather than global because the eval runs
    twenty seeds across three arms (PRD §10). Sixty runs appending to one chain
    would serialise every audit write behind a single contended head, against
    NFR-02's three-minute budget. One chain per run makes those appends
    independent; run_id None is the live chain.

  * `(run_id, _id)` on both — the head lookup is "newest document in this
    partition", and the unique index above cannot serve that sort.

  * `audit` idempotency is UNIQUE on (idempotency_key, phase) rather than on
    idempotency_key alone, because SPEC §5.6 writes a `pre` row and a `post` row
    carrying the same key. A unique index on the key alone would make the post
    row impossible to insert. The dedupe property invariant 9 depends on is
    unchanged: a second `pre` insert for the same key still raises
    DuplicateKeyError, which still means "already executed".

  * `events.rz_event_id` uniqueness is compound with `signature_valid` —
    `(rz_event_id, signature_valid)` UNIQUE — day-2 fix, superseding a
    global-unique `uq_rz_event_id`. A single index unique across all rows
    meant a forged delivery (invalid signature, stored as forensic data per
    the ingest module's design decisions) permanently occupied that
    `rz_event_id`: after a webhook-secret rotation, Razorpay's
    legitimately-signed retry of the same event would collide on the same
    key, get classified `DUPLICATE`, and never be stored as valid — the
    event vanishes rather than recovers. The compound key lets one invalid
    row and one valid row coexist per event id (the valid retry after
    rotation succeeds), while still bounding growth to at most those two: a
    second identical forgery at the same event id still collides on the
    same `(rz_event_id, false)` pair and is rejected. `rz_event_id` itself
    is attacker-reachable (Fix 1's premise — it comes from the
    unauthenticated `X-Razorpay-Event-Id` header), so this bounds a
    *replay at one id*, not the number of ids an attacker can create; it is
    an idempotency property, not a rate limit.

    An earlier version of this fix used two *partial* unique indexes
    (`partialFilterExpression: {signature_valid: true}` / `false`) instead
    of one compound index. `compliance-auditor` found two problems with
    that shape, both real: (1) a document where `signature_valid` is
    missing or not exactly `true`/`false` matches neither partial filter,
    so it would have had no uniqueness constraint at all — never reachable
    today since `verify_signature` always returns a strict `bool`, but a
    structural gap in a guard that used to be unconditional; (2) MongoDB
    only uses a partial index when the query predicate guarantees a match,
    so an unqualified `find({rz_event_id: x})` — which `by_rz_event_id`
    issues — would silently fall back to a collection scan on real Atlas, a
    regression invisible in mongomock. A single non-partial compound index
    has no uncovered partition and stays usable by any prefix query.

    Whichever shape replaces `uq_rz_event_id`, the same-named old index
    does not disappear on its own: `create_index`/`create_indexes` only
    add. `apply_indexes()` explicitly drops known-superseded index names
    (see `_LEGACY_INDEXES` below) so a database that already ran an earlier
    version of this function converges to the current shape rather than
    enforcing both the old and new constraint at once — *after* creating the
    replacement, not before (see `apply_indexes()`'s own docstring for why
    the order matters). Caught by `compliance-auditor` re-running the fix
    end to end against a database seeded with the pre-fix index — the
    standard test fixture always builds a fresh mongomock database, so no
    test in this suite could have caught it either. See NOTES.md's day 2
    entries.
"""

from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING, IndexModel
from pymongo.errors import OperationFailure

# Index names once declared here, since replaced. Dropped (if present) before
# the current INDEXES are applied — see the module docstring's note on
# `events.rz_event_id`. Never remove an entry once a shape has shipped past
# day 2; a database from an earlier day must still converge on next startup.
_LEGACY_INDEXES: dict[str, list[str]] = {
    "events": ["uq_rz_event_id", "uq_rz_event_id_valid", "uq_rz_event_id_invalid"],
}

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
        # produces one case. Compound with signature_valid — see module
        # docstring — so an invalid-signature row (kept as forensic data)
        # cannot permanently block a later legitimately-signed retry of the
        # same event id after a secret rotation, while still bounding a
        # replayed forgery to one stored row per (id, validity) pair.
        IndexModel(
            [("rz_event_id", ASCENDING), ("signature_valid", ASCENDING)],
            unique=True,
            name="uq_rz_event_id_signature_valid",
        ),
        IndexModel([("received_at", DESCENDING)], name="ix_received_at"),
        IndexModel(
            [("run_id", ASCENDING), ("prev_hash", ASCENDING)],
            unique=True,
            name="uq_events_run_prev_hash",
        ),
        IndexModel([("run_id", ASCENDING), ("_id", DESCENDING)], name="ix_events_run_head"),
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
        IndexModel(
            [("run_id", ASCENDING), ("prev_hash", ASCENDING)],
            unique=True,
            name="uq_audit_run_prev_hash",
        ),
        IndexModel([("run_id", ASCENDING), ("_id", DESCENDING)], name="ix_audit_run_head"),
    ],
    # customers uses customer_id as _id, so PRD §8.3's {_id: 1} is the implicit
    # index MongoDB already creates. Nothing to declare.
    CUSTOMERS: [],
    EXCEPTIONS: [
        IndexModel([("case_id", ASCENDING)], name="ix_case"),
    ],
    RUNS: [],
}


def _check_no_name_collides_with_a_legacy_one(
    indexes: dict[str, list[IndexModel]], legacy: dict[str, list[str]]
) -> None:
    """`apply_indexes()` creates before it drops (see its docstring for why),
    so a currently-declared index name that also appears in `_LEGACY_INDEXES`
    would be created and then immediately destroyed, silently, on every
    startup — `create_indexes` would report it created, and the very next
    line would remove it, with no error either way. `_LEGACY_INDEXES` is an
    append-only, never-shrinking list by design, so this is a real risk for
    a future index rename that happens to reuse an old name. Checked at
    import time so the failure is loud and immediate, not a silently
    vanishing index discovered later. Compliance-auditor (F13), found by
    deliberately reintroducing `uq_rz_event_id` into `INDEXES` and observing
    it get created and dropped in the same `apply_indexes()` call.
    """
    for collection_name, models in indexes.items():
        declared = {model.document["name"] for model in models}
        collision = declared & set(legacy.get(collection_name, []))
        if collision:
            raise AssertionError(
                f"{collection_name}: index name(s) {sorted(collision)} are declared in both "
                f"INDEXES and _LEGACY_INDEXES. A currently-declared index cannot also be "
                f"scheduled for removal — apply_indexes() would create then immediately drop "
                f"it on every startup. Rename one side."
            )


_check_no_name_collides_with_a_legacy_one(INDEXES, _LEGACY_INDEXES)


async def _drop_legacy_indexes(db: AsyncIOMotorDatabase[Any]) -> None:
    """Drop any index this project once declared and has since superseded.

    Checks `list_indexes()` for presence first rather than calling
    `drop_index()` and swallowing a "not found" exception: mongomock raises
    `OperationFailure` with `code=None` for a missing index, not the
    IndexNotFound code (27) real MongoDB sends, so an exception-code-based
    swallow would silently fail to swallow it on mongomock — every fresh
    test database would hit an unhandled error. An existence check behaves
    identically on both.

    The existence check is still a check-then-act: two processes booting at
    once (a rolling deploy, `uvicorn --workers N`) can both see the legacy
    index and both call `drop_index`, and the loser's drop then races the
    same "index not found" mongomock reports with `code=None` — this time a
    real failure to swallow, not a stale-check false negative. Re-checking
    existence *after* a failed drop distinguishes the two: still gone means
    someone else dropped it first (fine), still present means the drop
    itself failed (not fine, re-raise). Found by compliance-auditor (F8)
    stress-testing this exact function.
    """
    for collection_name, legacy_names in _LEGACY_INDEXES.items():
        existing = {index["name"] async for index in db[collection_name].list_indexes()}
        for legacy in legacy_names:
            if legacy not in existing:
                continue
            try:
                await db[collection_name].drop_index(legacy)
            except OperationFailure:
                still_there = {index["name"] async for index in db[collection_name].list_indexes()}
                if legacy in still_there:
                    raise


async def apply_indexes(db: AsyncIOMotorDatabase[Any]) -> dict[str, list[str]]:
    """Create every currently-declared index, then drop superseded ones.

    Idempotent — safe to run on every startup, but not unconditionally: see
    the per-shape note below. Create-before-drop, not the reverse: dropping
    first opens a window with no uniqueness constraint at all on the
    collection between the two statements — a rolling deploy, a SIGKILL, or
    a transient Atlas blip landing in that window can let a genuine
    duplicate `rz_event_id` in, which then makes every *future*
    `create_indexes(unique=True)` fail permanently (F7, compliance-auditor,
    reproduced: a live duplicate lands during the window, and the guard
    never comes back).

    Create-first is safe *when the legacy index is at least as strong as its
    replacement* — the create step cannot then fail while the old index is
    still enforcing a superset of what the new one requires. This holds
    unconditionally for `"day0_global_rz_event_id"`: a global unique
    constraint on `rz_event_id` forbids strictly more than the compound
    `(rz_event_id, signature_valid)` one, so any data satisfying the old
    index already satisfies the new one.

    It does **not** hold unconditionally for `"day2_partial_rz_event_id"`.
    The two partial indexes each constrain only the documents matching
    their filter (`signature_valid: true` / `false`); a document where that
    field is missing or non-boolean satisfies *both* partials trivially
    (matches neither filter, so neither index has an opinion) while
    potentially violating the compound index, which has an opinion on every
    document. Reproduced against real MongoDB (not mongomock, which doesn't
    model this): two documents with the same `rz_event_id` and a missing
    `signature_valid` are both accepted under the partial pair, and the
    subsequent compound-index `create_indexes` call fails with E11000 on
    exactly that pair — see `tests/local_only/test_real_mongo_migration.py`.
    On real, current data this is unreachable (`verify_signature` always
    returns a strict `bool`), so it is a latent risk, not a live one — but
    if it ever fires, the *failure itself* is the safe outcome: the create
    raises, `apply_indexes()` propagates, the FastAPI lifespan
    (`api/main.py`) fails to start, and the legacy guard is left fully
    intact, never dropped. It is a boot loop with an E11000 in the log, not
    a silent constraint loss — noted here so whoever hits it recognises it
    rather than treating it as a mystery. (F12, compliance-auditor: an
    earlier version of this docstring said the legacy shape was uniformly
    "weaker" than its replacement, which is backwards — the safety
    condition is that the legacy shape be *stronger* — and asserted the
    partial case behaved like the global one without having checked.)

    If a future migration ever needs a replacement that is strictly
    *stricter* than every legacy shape it supersedes, create-first fails
    before the old guard is touched regardless of shape — still the safe
    failure.
    """
    created: dict[str, list[str]] = {}
    for name, models in INDEXES.items():
        if not models:
            created[name] = []
            continue
        created[name] = list(await db[name].create_indexes(models))
    await _drop_legacy_indexes(db)
    return created
