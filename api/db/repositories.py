"""One repository per collection.

The `events` and `audit` repositories expose `insert` and reads. No update,
delete, replace or upsert method exists on them, and none may be added:
PRD §8.4 layer 1, CLAUDE.md invariant 6, enforced by
scripts/check_boundaries.py.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from motor.motor_asyncio import AsyncIOMotorCollection, AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from api.db import collections as coll
from api.db.chain import GENESIS_HASH, link

# How many times a chained insert re-reads the head after losing a race on the
# unique prev_hash index. Contention is per-collection and writes are short, so
# this is generous.
CHAIN_APPEND_ATTEMPTS = 8


def _utcnow() -> dt.datetime:
    """All timestamps are stored UTC (NFR-04). Gate logic converts to IST."""
    return dt.datetime.now(dt.UTC)


class _ChainedAppendOnly:
    """Base for a hash-chained, append-only collection.

    Deliberately narrow: it offers `insert` and read helpers, nothing else.
    Subclasses must not add a mutating method.
    """

    collection_name: str

    def __init__(self, db: AsyncIOMotorDatabase[Any]) -> None:
        self._c: AsyncIOMotorCollection[Any] = db[self.collection_name]

    async def _head_hash(self) -> str:
        """Hash of the newest document, or GENESIS_HASH for an empty chain.

        Read by insertion order rather than by walking the chain. Under a race
        two writers can read the same head; the unique index on prev_hash makes
        the loser fail with DuplicateKeyError and retry against the new head.
        """
        newest = await self._c.find_one(sort=[("_id", DESCENDING)], projection={"hash": 1})
        if newest is None:
            return GENESIS_HASH
        return str(newest["hash"])

    async def insert(self, document: dict[str, Any]) -> dict[str, Any]:
        """Append one hash-linked document. Returns it as stored, with `_id`.

        DuplicateKeyError on a business-unique index (rz_event_id,
        idempotency_key) propagates to the caller — that is the idempotency
        signal, not an error to swallow.
        """
        last_error: DuplicateKeyError | None = None
        for _ in range(CHAIN_APPEND_ATTEMPTS):
            linked = link(document, await self._head_hash())
            try:
                result = await self._c.insert_one(linked)
            except DuplicateKeyError as exc:
                if not _is_chain_race(exc):
                    raise
                last_error = exc
                continue
            linked["_id"] = result.inserted_id
            return linked
        raise RuntimeError(
            f"{self.collection_name}: chain head contended {CHAIN_APPEND_ATTEMPTS} times "
            f"without landing an append. Check for a stuck writer, then retry. "
            f"Last driver error: {last_error}"
        )

    async def get(self, doc_id: Any) -> dict[str, Any] | None:
        result: dict[str, Any] | None = await self._c.find_one({"_id": doc_id})
        return result

    async def count(self) -> int:
        return int(await self._c.count_documents({}))

    async def all_by_insertion(self) -> list[dict[str, Any]]:
        """Every document, insertion-ordered. Used by scripts/verify_chain.py."""
        cursor = self._c.find({}).sort([("_id", ASCENDING)])
        return [doc async for doc in cursor]


def _is_chain_race(exc: DuplicateKeyError) -> bool:
    """True when a DuplicateKeyError came from the prev_hash index, not a business key."""
    return "prev_hash" in str(exc)


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
        self.events = EventRepository(db)
        self.audit = AuditRepository(db)
        self.cases = CaseRepository(db)
        self.decisions = DecisionRepository(db)
        self.customers = CustomerRepository(db)
        self.exceptions = ExceptionRepository(db)
        self.runs = RunRepository(db)
