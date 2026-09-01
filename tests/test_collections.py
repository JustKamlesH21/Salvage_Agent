"""apply_indexes() internals: ordering, legacy-drop failure handling, and the
name-collision guard. Regression coverage for F10 (compliance-auditor): the
F7/F8 fixes shipped with no test that would fail if either were reverted —
confirmed by compliance-auditor reintroducing both bugs by hand and watching
the full suite stay green. Every test in this file is written to fail if the
specific fix it covers is undone.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

import pytest
from api.db.collections import (
    _LEGACY_INDEXES,
    INDEXES,
    _check_no_name_collides_with_a_legacy_one,
    _drop_legacy_indexes,
    apply_indexes,
)
from mongomock_motor import AsyncMongoMockClient
from pymongo.errors import OperationFailure

from tests import db_history


class _RecordingCollection:
    """Wraps one real collection, appending to a shared call log on the two
    operations apply_indexes() must order correctly. Everything else
    delegates straight through.

    Necessary because `db[name]` returns a fresh wrapper object on every
    access in mongomock_motor (`db["events"] is db["events"]` is False), so
    monkeypatching a method on one such wrapper has no effect on the next
    access — the log has to live on something that persists across accesses,
    which is this proxy's `calls` list, shared via the owning `_RecordingDB`.
    """

    def __init__(self, real: Any, name: str, calls: list[tuple[str, str]]) -> None:
        self._real = real
        self._name = name
        self._calls = calls

    async def create_indexes(self, models: Any) -> Any:
        self._calls.append(("create_indexes", self._name))
        return await self._real.create_indexes(models)

    async def drop_index(self, name: str) -> Any:
        self._calls.append(("drop_index", self._name))
        return await self._real.drop_index(name)

    def list_indexes(self) -> Any:
        return self._real.list_indexes()


class _RecordingDB:
    def __init__(self, real: Any) -> None:
        self._real = real
        self.calls: list[tuple[str, str]] = []

    def __getitem__(self, name: str) -> _RecordingCollection:
        return _RecordingCollection(self._real[name], name, self.calls)


class TestApplyIndexesOrdering:
    async def test_every_legacy_collection_creates_before_it_drops(self) -> None:
        # F7 (compliance-auditor): dropping before creating opened a window
        # with no uniqueness constraint at all on events.rz_event_id. F10
        # (compliance-auditor): that fix shipped with nothing that would
        # fail if the two statements were swapped back — this is that test.
        #
        # Iterates _LEGACY_INDEXES' own collections rather than hard-coding
        # "events": _LEGACY_INDEXES is append-only and may one day name a
        # different collection (e.g. audit) — noted by compliance-auditor's
        # fourth pass as a coverage gap this test would otherwise repeat.
        real_db = AsyncMongoMockClient()["salvage_ordering_test"]
        await db_history.seed(real_db, "day0_global_rz_event_id")
        recording = _RecordingDB(real_db)

        await apply_indexes(recording)

        for collection_name in _LEGACY_INDEXES:
            calls = [op for op, name in recording.calls if name == collection_name]
            create_positions = [i for i, op in enumerate(calls) if op == "create_indexes"]
            drop_positions = [i for i, op in enumerate(calls) if op == "drop_index"]
            assert create_positions, f"expected a create_indexes call on {collection_name}"
            assert drop_positions, f"expected a drop_index call on {collection_name}"
            assert max(create_positions) < min(drop_positions), (
                f"{collection_name} index calls in order {calls!r} — the replacement must be "
                f"created before any legacy index is dropped"
            )


class _FakeIndexCollection:
    """A minimal stand-in for one collection, just enough to drive
    `_drop_legacy_indexes`'s two branches deterministically — no real
    database needed, since the thing under test is pure control flow around
    a failed `drop_index`.
    """

    def __init__(self, names: set[str], *, fail: bool, remove_anyway: bool) -> None:
        self._names = names
        self._fail = fail
        self._remove_anyway = remove_anyway

    async def list_indexes(self) -> AsyncIterator[Mapping[str, str]]:
        for name in list(self._names):
            yield {"name": name}

    async def drop_index(self, name: str) -> None:
        if self._remove_anyway:
            # Simulates a concurrent process winning the race: the index is
            # really gone by the time this call's error comes back.
            self._names.discard(name)
        if self._fail:
            raise OperationFailure("simulated drop failure", code=None)
        self._names.discard(name)


class TestDropLegacyIndexesFailureHandling:
    async def test_a_lost_race_is_swallowed(self) -> None:
        # F8 (compliance-auditor): two processes booting at once can both
        # see the legacy index and both call drop_index; the loser's call
        # fails even though the index really is gone. That must not crash
        # the boot.
        fake_db = {
            "events": _FakeIndexCollection({"uq_rz_event_id"}, fail=True, remove_anyway=True)
        }
        await _drop_legacy_indexes(fake_db)  # must not raise

    async def test_a_genuine_failure_still_raises(self) -> None:
        # The index is still present after the failed drop — a real
        # problem, not a lost race — and must not be swallowed.
        fake_db = {
            "events": _FakeIndexCollection({"uq_rz_event_id"}, fail=True, remove_anyway=False)
        }
        with pytest.raises(OperationFailure):
            await _drop_legacy_indexes(fake_db)

    async def test_a_clean_drop_needs_no_recovery_path(self) -> None:
        fake_db = {
            "events": _FakeIndexCollection({"uq_rz_event_id"}, fail=False, remove_anyway=False)
        }
        await _drop_legacy_indexes(fake_db)


class TestNoNameCollisionGuard:
    def test_the_real_declarations_pass(self) -> None:
        # Documents the invariant this module checks at import time —
        # if this ever raises, so does importing api.db.collections at all.
        _check_no_name_collides_with_a_legacy_one(INDEXES, _LEGACY_INDEXES)

    def test_a_colliding_declaration_is_rejected(self) -> None:
        # F13 (compliance-auditor): apply_indexes() creates before it drops,
        # so a currently-declared index name that also appears in
        # _LEGACY_INDEXES would be created and then immediately destroyed on
        # every startup, silently. This must be caught, loudly, before that
        # can ship.
        from pymongo import ASCENDING, IndexModel

        colliding_indexes = {
            "events": [IndexModel([("x", ASCENDING)], name="uq_rz_event_id")],
        }
        colliding_legacy = {"events": ["uq_rz_event_id"]}
        with pytest.raises(AssertionError):
            _check_no_name_collides_with_a_legacy_one(colliding_indexes, colliding_legacy)
