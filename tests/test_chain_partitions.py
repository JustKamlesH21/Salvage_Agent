"""Chains are partitioned per eval run.

Twenty seeds across three arms is sixty runs. One global chain would serialise
every audit write in the batch behind a single contended head, against NFR-02's
three-minute budget. These tests hold the partitioning to its two obligations:
the partitions are genuinely independent, and each one is still individually
verifiable end to end.
"""

from __future__ import annotations

import pytest
from api.db.chain import (
    GENESIS_HASH,
    LIVE_RUN,
    ChainBreakError,
    link,
    order_chain,
    partition_chains,
    partition_label,
    verify_link,
    verify_sequence,
)


def chain_for(run_id: str | None, count: int, start: int = 0) -> list[dict[str, object]]:
    documents: list[dict[str, object]] = []
    prev = GENESIS_HASH
    for n in range(count):
        row = link({"n": start + n, "run_id": run_id}, prev)
        row["_id"] = f"{run_id}-{n}"
        prev = str(row["hash"])
        documents.append(row)
    return documents


class TestPartitioning:
    def test_each_run_is_grouped_separately(self) -> None:
        documents = chain_for("run_a", 3) + chain_for("run_b", 2) + chain_for(LIVE_RUN, 4)
        partitions = partition_chains(documents)
        assert {k: len(v) for k, v in partitions.items()} == {"run_a": 3, "run_b": 2, None: 4}

    def test_every_partition_verifies_independently(self) -> None:
        partitions = partition_chains(chain_for("run_a", 5) + chain_for("run_b", 5))
        for documents in partitions.values():
            assert verify_sequence(order_chain(documents)) == 5

    def test_two_runs_may_both_start_at_genesis(self) -> None:
        # The whole point of partitioning: run_a and run_b each have a document
        # whose prev_hash is GENESIS_HASH. Under a global unique index on
        # prev_hash alone, the second run could not write its first row.
        a, b = chain_for("run_a", 1), chain_for("run_b", 1)
        assert a[0]["prev_hash"] == b[0]["prev_hash"] == GENESIS_HASH

    def test_a_missing_run_id_falls_into_the_live_partition(self) -> None:
        # Matches how MongoDB indexes a missing field: as null.
        orphaned = link({"n": 1}, GENESIS_HASH)
        assert list(partition_chains([orphaned])) == [LIVE_RUN]

    def test_run_id_is_part_of_the_hashed_body(self) -> None:
        # A row cannot be moved from one run's chain to another undetected.
        moved = dict(chain_for("run_a", 1)[0])
        moved["run_id"] = "run_b"
        with pytest.raises(ChainBreakError, match="hash mismatch"):
            verify_sequence([moved])

    def test_a_break_in_one_run_leaves_the_others_verifiable(self) -> None:
        good, bad = chain_for("run_a", 3), chain_for("run_b", 3)
        bad[1]["n"] = 999
        partitions = partition_chains(good + bad)
        assert verify_sequence(order_chain(partitions["run_a"])) == 3
        with pytest.raises(ChainBreakError):
            verify_sequence(order_chain(partitions["run_b"]))

    def test_partitions_are_labelled_for_the_operator(self) -> None:
        assert partition_label(LIVE_RUN) == "live"
        assert partition_label("run_7") == "run run_7"


class TestGenesisSentinel:
    def test_genesis_is_an_explicit_value_not_null(self) -> None:
        # The uniqueness guard is a compound index on (run_id, prev_hash), and
        # MongoDB indexes a missing field and an explicit null identically. A
        # null genesis would be indistinguishable from a document whose
        # prev_hash was never written.
        assert isinstance(GENESIS_HASH, str)
        assert GENESIS_HASH is not None
        assert len(GENESIS_HASH) == 64

    def test_genesis_is_the_same_width_as_a_real_hash(self) -> None:
        first = link({"n": 1}, GENESIS_HASH)
        assert len(str(first["hash"])) == len(GENESIS_HASH)

    def test_a_document_with_a_null_prev_hash_is_rejected_not_read_as_genesis(self) -> None:
        # It fails on linkage, not on being mistaken for the head of a chain —
        # which is exactly the confusion the sentinel exists to prevent.
        with pytest.raises(ChainBreakError, match="claims prev_hash None"):
            verify_sequence([{"_id": 1, "prev_hash": None, "hash": "x" * 64}])

    def test_a_document_with_no_prev_hash_at_all_is_rejected(self) -> None:
        with pytest.raises(ChainBreakError, match="missing prev_hash or hash"):
            verify_link({"_id": 1, "hash": "x" * 64})
