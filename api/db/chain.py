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

# The prev_hash of the first document in a chain. 64 zeros so that every
# prev_hash in the collection is the same width and a missing link is obvious.
GENESIS_HASH = "0" * 64

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
        moment = value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)
        return moment.astimezone(dt.UTC).isoformat(timespec="microseconds")
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
            f"stored {stored[:12]}… recomputed {recomputed[:12]}… "
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
                f"{str(document.get('prev_hash'))[:12]}… but the chain head was "
                f"{expected_prev[:12]}… — a document was inserted, removed or reordered"
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
                f"two documents claim prev_hash {prev[:12]}… "
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
