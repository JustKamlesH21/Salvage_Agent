"""The hash chain must detect every way a stored document can be tampered with.

These tests are the reason the chain is worth claiming as tamper-evident. If
they pass, the claim is defensible; if they were never written, it is decoration.
"""

from __future__ import annotations

import datetime as dt

import pytest
from api.db.chain import (
    GENESIS_HASH,
    ChainBreakError,
    canonical_json,
    compute_hash,
    link,
    order_chain,
    verify_link,
    verify_sequence,
)


def build_chain(bodies: list[dict[str, object]]) -> list[dict[str, object]]:
    chain: list[dict[str, object]] = []
    prev = GENESIS_HASH
    for index, body in enumerate(bodies):
        linked = link(body, prev)
        linked["_id"] = index
        prev = str(linked["hash"])
        chain.append(linked)
    return chain


class TestCanonicalJson:
    def test_key_order_does_not_change_the_bytes(self) -> None:
        assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})

    def test_hash_and_id_are_excluded_from_the_body(self) -> None:
        # _id is assigned by the driver after the hash is computed, so including
        # it would make every hash unverifiable.
        assert canonical_json({"a": 1, "hash": "x", "_id": "y"}) == canonical_json({"a": 1})

    def test_datetimes_serialise_as_utc(self) -> None:
        ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
        aware = dt.datetime(2026, 9, 1, 15, 30, tzinfo=ist)
        naive_utc = dt.datetime(2026, 9, 1, 10, 0)
        assert canonical_json({"ts": aware}) == canonical_json({"ts": naive_utc})

    def test_nested_structures_are_encoded(self) -> None:
        payload = {"a": [{"b": dt.datetime(2026, 1, 1, tzinfo=dt.UTC)}], "c": (1, 2)}
        assert "2026-01-01T00:00:00.000000+00:00" in canonical_json(payload)

    def test_non_json_values_get_a_stable_string_form(self) -> None:
        class Opaque:
            def __str__(self) -> str:
                return "opaque-1"

        assert canonical_json({"x": Opaque()}) == '{"x":"opaque-1"}'


class TestLinking:
    def test_first_document_links_to_genesis(self) -> None:
        first = link({"n": 1}, GENESIS_HASH)
        assert first["prev_hash"] == GENESIS_HASH
        verify_link(first)

    def test_link_does_not_mutate_its_input(self) -> None:
        body = {"n": 1}
        link(body, GENESIS_HASH)
        assert body == {"n": 1}

    def test_hash_depends_on_the_predecessor(self) -> None:
        # Identical content at a different chain position must hash differently,
        # otherwise a document could be moved without detection.
        assert compute_hash(GENESIS_HASH, {"n": 1}) != compute_hash("a" * 64, {"n": 1})


class TestTamperDetection:
    def test_edited_field_is_caught(self) -> None:
        chain = build_chain([{"n": 1}, {"n": 2}, {"n": 3}])
        chain[1]["n"] = 99
        with pytest.raises(ChainBreakError, match="hash mismatch"):
            verify_sequence(chain)

    def test_deleted_document_is_caught(self) -> None:
        chain = build_chain([{"n": 1}, {"n": 2}, {"n": 3}])
        del chain[1]
        with pytest.raises(ChainBreakError, match="chain head was"):
            verify_sequence(chain)

    def test_reordered_documents_are_caught(self) -> None:
        chain = build_chain([{"n": 1}, {"n": 2}, {"n": 3}])
        chain[1], chain[2] = chain[2], chain[1]
        with pytest.raises(ChainBreakError):
            verify_sequence(chain)

    def test_document_missing_its_hash_is_caught(self) -> None:
        with pytest.raises(ChainBreakError, match="missing prev_hash or hash"):
            verify_link({"_id": 1, "prev_hash": GENESIS_HASH})

    def test_intact_chain_verifies(self) -> None:
        chain = build_chain([{"n": i} for i in range(25)])
        assert verify_sequence(chain) == 25

    def test_empty_chain_is_vacuously_intact(self) -> None:
        assert verify_sequence([]) == 0


class TestOrdering:
    def test_insertion_order_is_recovered_from_linkage(self) -> None:
        chain = build_chain([{"n": 1}, {"n": 2}, {"n": 3}])
        shuffled = [chain[2], chain[0], chain[1]]
        assert [d["_id"] for d in order_chain(shuffled)] == [0, 1, 2]

    def test_fork_is_reported(self) -> None:
        chain = build_chain([{"n": 1}, {"n": 2}])
        sibling = link({"n": 99}, GENESIS_HASH)
        sibling["_id"] = 99
        with pytest.raises(ChainBreakError, match="forked"):
            order_chain([*chain, sibling])

    def test_orphan_is_left_unreachable(self) -> None:
        # An orphan is not an exception here — verify_chain.py compares the
        # returned length against the collection count and reports the gap.
        chain = build_chain([{"n": 1}, {"n": 2}])
        orphan = link({"n": 3}, "f" * 64)
        orphan["_id"] = 42
        assert len(order_chain([*chain, orphan])) == 2
