"""The core of FR-01/FR-02/FR-03. No FastAPI or HTTP types in this module —
`ingest_webhook()` takes bytes and a header mapping and returns a plain
result, so it is testable directly against a repository without a running
server.

Design decisions made explicit here because PRD §8.2 underspecifies them:

  * `processing_state` is written as `"received"` on every successful insert,
    full stop. `events` is append-only (invariant 6), so `"processed"` and
    `"failed"` — which describe outcomes only knowable after downstream work
    in later phases — can never be recorded on this row without mutating it.
    A later phase that needs that signal will reference `rz_event_id` from
    its own collection rather than updating this one. `"duplicate"` never
    appears as a stored value either: a genuine duplicate produces no new row
    at all, so it is a response classification, not a document state.

  * An invalid signature is stored, not rejected before insert. PRD §8.2
    declares `signature_valid: Boolean` as a field on every document, which
    only makes sense if both outcomes are stored — a forged or misconfigured
    delivery is itself forensic data in a tamper-evident audit trail. Only a
    valid signature is eligible for anything past storage.

  * `payload` holds a parsed dict when the body is valid JSON, and a
    best-effort decoded string when it is not. Malformed JSON with a valid
    signature is a real, storable case (day 1 proved failure payloads can be
    sparse; a body that fails to parse at all is the same principle taken
    one step further) — it must not 500. `event_type` is `None` when the body
    did not parse; there is nothing to extract it from.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pymongo.errors import DuplicateKeyError

from api.db.chain import ChainAppendExhaustedError
from api.db.repositories import EventRepository
from api.ingest.hmac import verify_signature

SIGNATURE_HEADER = "x-razorpay-signature"
EVENT_ID_HEADER = "x-razorpay-event-id"


class IngestOutcome(Enum):
    """What happened to one delivery. Drives both the stored state and the
    HTTP status the router returns — see api/ingest/router.py's status map.
    """

    RECEIVED = "received"
    DUPLICATE = "duplicate"
    INVALID_SIGNATURE = "invalid_signature"
    # Signature verified, but the durable write itself failed (the chain
    # head stayed contended past every retry). We did NOT store the event,
    # so the caller must not be told we did — same logic as
    # INVALID_SIGNATURE returning 401 rather than 200: if the row does not
    # exist, the response must not claim success. Razorpay's own retry is
    # what recovers from this, not a client-side workaround.
    STORAGE_FAILED = "storage_failed"


@dataclass(frozen=True)
class IngestResult:
    outcome: IngestOutcome
    document: dict[str, Any] | None = None
    detail: str | None = None


def _lower_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Case-insensitive lookup over whatever mapping we were handed.

    Starlette's `Request.headers` is already case-insensitive, but this
    function is also called directly in tests with a plain dict, so it does
    its own normalisation rather than assuming the caller's mapping type.
    """
    return {k.lower(): v for k, v in headers.items()}


def _decode_payload(raw_body: bytes) -> tuple[Any, str | None]:
    """Parse for storage. Never raises — a parse failure degrades the stored
    shape, it does not block the insert (see module docstring).
    """
    try:
        parsed = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return _best_effort_text(raw_body), None
    event_type = parsed.get("event") if isinstance(parsed, dict) else None
    return parsed, event_type


def _best_effort_text(raw_body: bytes) -> str:
    try:
        return raw_body.decode("utf-8")
    except UnicodeDecodeError:
        return raw_body.hex()


def _utcnow() -> dt.datetime:
    """All timestamps are stored UTC (invariant 10, NFR-04)."""
    return dt.datetime.now(dt.UTC)


async def ingest_webhook(
    raw_body: bytes,
    headers: Mapping[str, str],
    events: EventRepository,
    *,
    webhook_secret: str,
) -> IngestResult:
    """Verify, store, dedupe. The caller (the router) guarantees `raw_body`
    is exactly what was delivered — nothing above this call may have parsed
    or cast it.
    """
    lowered = _lower_headers(headers)
    signature = lowered.get(SIGNATURE_HEADER)
    signature_valid = verify_signature(raw_body, signature, webhook_secret)

    rz_event_id = lowered.get(EVENT_ID_HEADER)
    if not rz_event_id:
        # Every one of day 1's 54 real captures carried this header; a
        # delivery without it is not real Razorpay traffic. Fall back to a
        # content hash so two distinct headerless deliveries don't collide
        # under the unique index on rz_event_id, while identical bytes
        # replayed still dedupe correctly — the same idempotency property
        # the header path gives us for free.
        rz_event_id = f"missing-event-id:{hashlib.sha256(raw_body).hexdigest()}"

    payload, event_type = _decode_payload(raw_body)

    document = {
        "rz_event_id": rz_event_id,
        "event_type": event_type,
        "payload": payload,
        "signature_valid": signature_valid,
        "received_at": _utcnow(),
        "processing_state": "received",
    }

    try:
        stored = await events.insert(document)
    except DuplicateKeyError:
        return IngestResult(outcome=IngestOutcome.DUPLICATE)
    except ChainAppendExhaustedError as exc:
        return IngestResult(outcome=IngestOutcome.STORAGE_FAILED, detail=str(exc))

    if not signature_valid:
        return IngestResult(outcome=IngestOutcome.INVALID_SIGNATURE, document=stored)
    return IngestResult(outcome=IngestOutcome.RECEIVED, document=stored)
