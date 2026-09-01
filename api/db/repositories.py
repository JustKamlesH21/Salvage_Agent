"""One repository per collection.

The `events` and `audit` repositories expose `insert` and reads. No update,
delete, replace or upsert method exists on them, and none may be added:
PRD §8.4 layer 1, CLAUDE.md invariant 6, enforced by
scripts/check_boundaries.py.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import random
from typing import Any

from motor.motor_asyncio import AsyncIOMotorCollection, AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from api.db import collections as coll
from api.db.chain import GENESIS_HASH, LIVE_RUN, ChainAppendExhaustedError, link

# How many times a chained insert re-reads the head after losing a race on the
# unique (run_id, prev_hash) index. Chains are partitioned per run, so genuine
# contention is only between concurrent writers inside one run.
CHAIN_APPEND_ATTEMPTS = 8

# Full-jitter backoff between attempts. Without jitter, two writers that
# collide re-read the head at the same instant and collide again on the same
# schedule — the retry loop becomes a lockstep livelock rather than a
# resolution. Worst case across all attempts is well under a second, which
# keeps it inside NFR-02's batch budget.
CHAIN_RETRY_BASE_SECONDS = 0.005
CHAIN_RETRY_CAP_SECONDS = 0.2


# Sentinel distinguishing "every partition" from "the live partition (None)".
_ANY_RUN: Any = object()


def _utcnow() -> dt.datetime:
    """All timestamps are stored UTC (NFR-04). Gate logic converts to IST."""
    return dt.datetime.now(dt.UTC)


class _ChainedAppendOnly:
    """Base for a hash-chained, append-only collection.

    Deliberately narrow: it offers `insert` and read helpers, nothing else.
    Subclasses must not add a mutating method.

    Chains are partitioned by `run_id` — one per eval run, plus the live chain
    at run_id None. Every stored document carries `run_id` explicitly, because
    it is part of the hashed body: the chain commits to which run a row belongs
    to, so a row cannot be moved between runs undetected.
    """

    collection_name: str

    def __init__(
        self,
        db: AsyncIOMotorDatabase[Any],
        exceptions: ExceptionRepository | None = None,
    ) -> None:
        self._c: AsyncIOMotorCollection[Any] = db[self.collection_name]
        # Where an exhausted append is filed before it is raised. Injected
        # rather than imported so this class stays insert-and-reads only.
        self._exceptions = exceptions

    async def _head_hash(self, run_id: Any) -> str:
        """Hash of the newest document in this partition, or GENESIS_HASH if empty.

        Read by insertion order rather than by walking the chain. Under a race
        two writers can read the same head; the unique (run_id, prev_hash)
        index makes the loser fail with DuplicateKeyError and retry against the
        new head.
        """
        newest = await self._c.find_one(
            {"run_id": run_id}, sort=[("_id", DESCENDING)], projection={"hash": 1}
        )
        if newest is None:
            return GENESIS_HASH
        return str(newest["hash"])

    async def insert(self, document: dict[str, Any], *, run_id: Any = LIVE_RUN) -> dict[str, Any]:
        """Append one hash-linked document to `run_id`'s chain. Returns it as stored.

        DuplicateKeyError on a business-unique index (rz_event_id,
        idempotency_key) propagates to the caller — that is the idempotency
        signal, not an error to swallow. Only a collision on the chain head is
        retried.

        Raises ChainAppendExhaustedError if the head stays contended. The
        document is filed to the exception desk first, so a chained row is
        never lost silently.
        """
        body = dict(document)
        carried = body.get("run_id", run_id)
        if carried != run_id:
            raise ValueError(
                f"{self.collection_name}: document carries run_id={carried!r} but was inserted "
                f"into the run_id={run_id!r} chain. Pass one or the other, not both — a row in "
                f"the wrong partition is a chain that cannot be verified."
            )
        body["run_id"] = run_id

        last_error = "none"
        for attempt in range(CHAIN_APPEND_ATTEMPTS):
            linked = link(body, await self._head_hash(run_id))
            try:
                result = await self._c.insert_one(linked)
            except DuplicateKeyError as exc:
                if not _is_chain_race(exc):
                    raise
                last_error = str(exc)
                await asyncio.sleep(_backoff(attempt))
                continue
            linked["_id"] = result.inserted_id
            return linked

        filing = await self._file_exhaustion(body, run_id, last_error)
        raise ChainAppendExhaustedError(
            self.collection_name, run_id, CHAIN_APPEND_ATTEMPTS, body, f"{last_error} [{filing}]"
        )

    async def _file_exhaustion(self, body: dict[str, Any], run_id: Any, last_error: str) -> str:
        """Put the undelivered row on the exception desk, where a human will see it.

        Returns what happened, which the raise then carries: the caller must be
        able to tell "filed, go look at the desk" from "not even the desk write
        landed". Best-effort by necessity — if this write also fails there is
        nowhere left to record it — but the caller always gets the raise, so an
        exhausted append is never mistaken for a successful one.
        """
        if self._exceptions is None:
            return "no exception desk wired; document exists only on the raised error"
        try:
            await self._exceptions.insert(
                {
                    "case_id": body.get("case_id"),
                    "reason_code": "CHAIN_APPEND_EXHAUSTED",
                    "detail": {
                        "collection": self.collection_name,
                        "run_id": run_id,
                        "attempts": CHAIN_APPEND_ATTEMPTS,
                        "last_error": last_error,
                        "unwritten_document": body,
                    },
                    "ts": _utcnow(),
                    "resolved_by": None,
                }
            )
        except PyMongoError as exc:
            return f"exception desk write also failed: {exc}"
        return "filed to the exception desk"

    async def get(self, doc_id: Any) -> dict[str, Any] | None:
        result: dict[str, Any] | None = await self._c.find_one({"_id": doc_id})
        return result

    async def count(self, *, run_id: Any = _ANY_RUN) -> int:
        query: dict[str, Any] = {} if run_id is _ANY_RUN else {"run_id": run_id}
        return int(await self._c.count_documents(query))

    async def all_by_insertion(self, *, run_id: Any = _ANY_RUN) -> list[dict[str, Any]]:
        """Every document, insertion-ordered. Used by scripts/verify_chain.py."""
        query: dict[str, Any] = {} if run_id is _ANY_RUN else {"run_id": run_id}
        cursor = self._c.find(query).sort([("_id", ASCENDING)])
        return [doc async for doc in cursor]


def _is_chain_race(exc: DuplicateKeyError) -> bool:
    """True when a DuplicateKeyError came from the chain index, not a business key."""
    return "prev_hash" in str(exc)


def _backoff(attempt: int) -> float:
    """Full jitter: uniform over [0, min(cap, base * 2**attempt)].

    Jitter is the point. Two writers backing off by the same deterministic
    amount re-collide on the same schedule.
    """
    ceiling = min(CHAIN_RETRY_CAP_SECONDS, CHAIN_RETRY_BASE_SECONDS * (2**attempt))
    return float(random.random() * ceiling)  # noqa: S311 - scheduling jitter, not a secret


class EventRepository(_ChainedAppendOnly):
    """Raw webhook capture. Append-only, hash-chained (FR-03)."""

    collection_name = coll.EVENTS

    async def by_rz_event_id(self, rz_event_id: str) -> dict[str, Any] | None:
        result: dict[str, Any] | None = await self._c.find_one({"rz_event_id": rz_event_id})
        return result


class AuditRepository(_ChainedAppendOnly):
    """The only record of money actions. Append-only, hash-chained (FR-03)."""

    collection_name = coll.AUDIT

    async def prior_result(self, idempotency_key: str) -> dict[str, Any] | None:
        """The post row for an already-executed action, for the DuplicateKeyError path.

        Returns None when the pre row exists but the post row does not — the
        dispatch is in flight or died mid-flight. The caller must not treat that
        as "no prior attempt" and re-dispatch (invariant 9).
        """
        result: dict[str, Any] | None = await self._c.find_one(
            {"idempotency_key": idempotency_key, "phase": "post"}
        )
        return result

    async def by_case(self, case_id: Any) -> list[dict[str, Any]]:
        cursor = self._c.find({"case_id": case_id}).sort([("ts", ASCENDING)])
        return [doc async for doc in cursor]


class _Mutable:
    """Base for the collections that legitimately change."""

    collection_name: str

    def __init__(self, db: AsyncIOMotorDatabase[Any]) -> None:
        self._c: AsyncIOMotorCollection[Any] = db[self.collection_name]

    async def insert(self, document: dict[str, Any]) -> Any:
        result = await self._c.insert_one(document)
        return result.inserted_id

    async def get(self, doc_id: Any) -> dict[str, Any] | None:
        result: dict[str, Any] | None = await self._c.find_one({"_id": doc_id})
        return result

    async def find(
        self, query: dict[str, Any], *, limit: int = 0, sort: list[tuple[str, int]] | None = None
    ) -> list[dict[str, Any]]:
        cursor = self._c.find(query)
        if sort:
            cursor = cursor.sort(sort)
        if limit:
            cursor = cursor.limit(limit)
        return [doc async for doc in cursor]


class CaseRepository(_Mutable):
    collection_name = coll.CASES

    async def transition(
        self, case_id: Any, *, expect: str, to: str, extra: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """Single-document atomic state move (PRD §8.5).

        Returns None when the case was not in `expect` — someone else moved it.
        Handle that path; do not assume it cannot happen.
        """
        update: dict[str, Any] = {"state": to, **(extra or {})}
        result: dict[str, Any] | None = await self._c.find_one_and_update(
            {"_id": case_id, "state": expect},
            {"$set": update},
            return_document=ReturnDocument.AFTER,
        )
        return result

    async def record_attempt(self, case_id: Any, attempt: dict[str, Any]) -> dict[str, Any] | None:
        """Append an attempt. Attempts are bounded at 12 per PRD §8.2."""
        result: dict[str, Any] | None = await self._c.find_one_and_update(
            {"_id": case_id},
            {"$push": {"attempts": attempt}},
            return_document=ReturnDocument.AFTER,
        )
        return result


class DecisionRepository(_Mutable):
    collection_name = coll.DECISIONS

    async def by_case(self, case_id: Any) -> list[dict[str, Any]]:
        return await self.find({"case_id": case_id}, sort=[("created_at", ASCENDING)])


class CustomerRepository(_Mutable):
    collection_name = coll.CUSTOMERS

    async def set_opt_out(self, customer_id: str, at: dt.datetime | None = None) -> None:
        """Opt-out is permanent and immediate (G6_OPTOUT). There is no un-set."""
        await self._c.update_one(
            {"_id": customer_id},
            {"$set": {"opt_out_at": at or _utcnow()}},
            upsert=True,
        )


class ExceptionRepository(_Mutable):
    collection_name = coll.EXCEPTIONS


class RunRepository(_Mutable):
    collection_name = coll.RUNS


class Repositories:
    """Everything the app needs to reach storage, constructed once at startup."""

    def __init__(self, db: AsyncIOMotorDatabase[Any]) -> None:
        self.db = db
        # Built first: the chained repositories file an exhausted append here
        # before raising, so a chained row is never dropped in silence.
        self.exceptions = ExceptionRepository(db)
        self.events = EventRepository(db, self.exceptions)
        self.audit = AuditRepository(db, self.exceptions)
        self.cases = CaseRepository(db)
        self.decisions = DecisionRepository(db)
        self.customers = CustomerRepository(db)
        self.runs = RunRepository(db)
