"""Hash-chain helpers for the append-only `events` and `audit` collections.

PRD §8.4: every chained document carries `prev_hash` and

    hash = sha256(prev_hash + canonical_json(body_without_hash))

The chain is tamper-*evident*, not tamper-*proof*: it detects modification of a
stored document, it does not prevent one. That distinction is stated in
LIMITATIONS.md and should be stated out loud rather than glossed over.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from decimal import Decimal
from typing import Any

# The prev_hash of the first document in a chain. An explicit sentinel, never
# null: the uniqueness guard is a compound index on (run_id, prev_hash), and
# MongoDB indexes a missing field and an explicit null identically. A null
# genesis would therefore be indistinguishable from a document whose prev_hash
# was never written — a malformed row would look like the head of a chain. 64
# zeros also keeps every prev_hash the same width, so a bad link is obvious on
# sight.
GENESIS_HASH = "0" * 64

# Chains are partitioned per eval run. run_id is None for the live chain.
LIVE_RUN = None

# Excluded from the hashed body: `hash` cannot hash itself, and `_id` is
# assigned by the driver on insert, after the hash is computed.
UNHASHED_FIELDS = frozenset({"hash", "_id"})


def _encode(value: Any) -> Any:
    """Reduce a BSON-bound value to something json.dumps handles deterministically."""
    if isinstance(value, dict):
        return {str(k): _encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(v) for v in value]
    if isinstance(value, dt.datetime):
        # Naive datetimes are treated as UTC: everything is stored UTC (NFR-04).
        #
        # Millisecond precision, not microsecond: BSON's date type only stores
        # milliseconds, truncating (not rounding) anything finer. A document
        # hashed at microsecond precision before insert and re-hashed at
        # whatever MongoDB actually persisted will disagree on any timestamp
        # whose microseconds aren't already a multiple of 1000 — which real
        # `datetime.now()` calls almost never are. Day 0's own chain tests
        # never caught this because their fixtures used hand-built, exactly
        # round-second datetimes with zero microseconds; day 2's ingest tests
        # exercise a genuine `datetime.now(dt.UTC)` and found it immediately.
        # Truncating to milliseconds here, matching BSON's own truncation
        # (verified empirically, not assumed — see NOTES.md), makes hashing
        # agree with storage regardless of which happens first.
        moment = value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)
        return moment.astimezone(dt.UTC).isoformat(timespec="milliseconds")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    # ObjectId and anything else BSON-native: stable string form.
    return str(value)


def canonical_json(document: dict[str, Any]) -> str:
    """Serialise a document so the same content always produces the same bytes.

    Sorted keys, no insignificant whitespace, unicode preserved. Two processes
    computing a hash for the same document must agree without coordinating.
    """
    body = {k: _encode(v) for k, v in document.items() if k not in UNHASHED_FIELDS}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_hash(prev_hash: str, document: dict[str, Any]) -> str:
    """sha256(prev_hash + canonical_json(body)) — PRD §8.4."""
    payload = prev_hash + canonical_json(document)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def link(document: dict[str, Any], prev_hash: str) -> dict[str, Any]:
    """Return `document` with prev_hash/hash set. Does not mutate the input."""
    linked = dict(document)
    linked["prev_hash"] = prev_hash
    linked["hash"] = compute_hash(prev_hash, linked)
    return linked


class ChainBreakError(Exception):
    """A chained document does not match its recomputed hash or its predecessor."""


class ChainAppendExhaustedError(Exception):
    """A chained insert lost the head race repeatedly and never landed.

    Carries the unlinked document so the caller — and the exception desk row
    written before this is raised — holds what was going to be recorded. An
    audit row that is silently dropped is worse than an action that failed:
    the action leaves a trace elsewhere, the missing row leaves none.
    """

    def __init__(
        self,
        collection: str,
        run_id: Any,
        attempts: int,
        document: dict[str, Any],
        last_error: str,
    ) -> None:
        self.collection = collection
        self.run_id = run_id
        self.attempts = attempts
        self.document = document
        self.last_error = last_error
        super().__init__(
            f"{collection}: chain head for run_id={run_id!r} was contended {attempts} times "
            f"without landing an append. The document was NOT written and has been filed to "
            f"the exception desk. Check for a stuck writer on this partition, then replay it. "
            f"Last driver error: {last_error}"
        )


def _short(value: Any) -> str:
    """Abbreviate a hash for a diagnostic, without making a null look truncated."""
    if not isinstance(value, str):
        return repr(value)
    return f"{value[:12]}…" if len(value) > 12 else value


def verify_link(document: dict[str, Any]) -> None:
    """Raise ChainBreakError if a single document's own hash does not recompute."""
    stored = document.get("hash")
    prev = document.get("prev_hash")
    if not isinstance(stored, str) or not isinstance(prev, str):
        raise ChainBreakError(f"document {document.get('_id')!r} is missing prev_hash or hash")
    recomputed = compute_hash(prev, document)
    if recomputed != stored:
        raise ChainBreakError(
            f"document {document.get('_id')!r} hash mismatch: "
            f"stored {_short(stored)} recomputed {_short(recomputed)} "
            f"— the document was modified after insert"
        )


def verify_sequence(documents: list[dict[str, Any]]) -> int:
    """Verify an ordered chain from genesis. Returns the number of links checked.

    Raises ChainBreakError naming the first document that fails, because the first
    break is the one worth investigating.
    """
    expected_prev = GENESIS_HASH
    for position, document in enumerate(documents):
        if document.get("prev_hash") != expected_prev:
            raise ChainBreakError(
                f"link {position} (_id={document.get('_id')!r}) claims prev_hash "
                f"{_short(document.get('prev_hash'))} but the chain head was "
                f"{_short(expected_prev)} — a document was inserted, removed or reordered"
            )
        verify_link(document)
        expected_prev = str(document["hash"])
    return len(documents)


def order_chain(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order documents by prev_hash linkage, starting at genesis.

    Insertion order is not sufficient: a fork or an orphan must be visible. Any
    document not reachable from genesis is left out, and the caller compares the
    returned length against the collection count to detect that.
    """
    by_prev: dict[str, dict[str, Any]] = {}
    for document in documents:
        prev = str(document.get("prev_hash"))
        if prev in by_prev:
            raise ChainBreakError(
                f"two documents claim prev_hash {_short(prev)} "
                f"(_id={by_prev[prev].get('_id')!r} and _id={document.get('_id')!r}) "
                f"— the chain forked"
            )
        by_prev[prev] = document

    ordered: list[dict[str, Any]] = []
    cursor = GENESIS_HASH
    while cursor in by_prev:
        node = by_prev.pop(cursor)
        ordered.append(node)
        cursor = str(node.get("hash"))
    return ordered


def partition_chains(documents: list[dict[str, Any]]) -> dict[Any, list[dict[str, Any]]]:
    """Group documents into one chain per run_id.

    Each eval run owns its own chain, so sixty batch runs append to sixty
    independent heads instead of serialising behind one. `LIVE_RUN` (None) is
    the live chain. A document with no run_id field falls into the live
    partition, which matches how MongoDB indexes a missing field.
    """
    partitions: dict[Any, list[dict[str, Any]]] = {}
    for document in documents:
        partitions.setdefault(document.get("run_id", LIVE_RUN), []).append(document)
    return partitions


def partition_label(run_id: Any) -> str:
    return "live" if run_id is None else f"run {run_id}"
