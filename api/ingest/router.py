"""POST /webhooks/razorpay — the ingest endpoint. FR-01/FR-02/FR-03.

`request.body()` is the FIRST statement in the handler, literally. Nothing
above it may touch the request — parsing, casting, or even logging an
interpreted form of the body before this line would mean the HMAC check
downstream runs against something other than what Razorpay actually sent.
This is the exact bug SPEC §5.1 calls "classic", and it's the reason
`test_router.py` asserts the ordering structurally rather than just by
reading this file.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse
from motor.motor_asyncio import AsyncIOMotorDatabase

from api.config import settings
from api.db.client import get_db
from api.db.repositories import EventRepository, ExceptionRepository, Repositories
from api.ingest.webhook import IngestOutcome, ingest_webhook
from api.pipeline import process_payment_failed

router = APIRouter()

# The one event Salvage's thesis is about (PRD §2, api/pipeline.py's scope
# note). Every other event type is still captured and hash-chained by ingest;
# nothing downstream acts on it yet.
PROCESSED_EVENT_TYPE = "payment.failed"

# Never 200 unless the event is durably stored: invalid signature and
# storage failure both need Razorpay to keep retrying, not stand down.
_STATUS_FOR_OUTCOME: dict[IngestOutcome, int] = {
    IngestOutcome.RECEIVED: 200,
    IngestOutcome.DUPLICATE: 200,
    IngestOutcome.INVALID_SIGNATURE: 401,
    IngestOutcome.STORAGE_FAILED: 500,
}


def get_event_repository(
    db: Annotated[AsyncIOMotorDatabase[Any], Depends(get_db)],
) -> EventRepository:
    return EventRepository(db, ExceptionRepository(db))


def get_repositories(
    db: Annotated[AsyncIOMotorDatabase[Any], Depends(get_db)],
) -> Repositories:
    return Repositories(db)


@router.post("/webhooks/razorpay")
async def receive_webhook(
    request: Request,
    background: BackgroundTasks,
    events: Annotated[EventRepository, Depends(get_event_repository)],
    repos: Annotated[Repositories, Depends(get_repositories)],
) -> JSONResponse:
    raw = await request.body()  # first statement — nothing above touches it

    result = await ingest_webhook(
        raw,
        request.headers,
        events,
        webhook_secret=settings().razorpay_webhook_secret,
    )

    # Capture, ack, then decide. The hash-chained raw row is the source of
    # truth and the only thing the status code speaks for — but until day 5
    # nothing consumed it: ingest (day 2) and the pipeline (day 4) were built
    # in separate phases and never wired together, so a real webhook produced
    # an event row and nothing else. No case, no decision, no audit.
    #
    # Deciding happens in a background task rather than inline because a
    # decision cycle can include an LLM call — NFR-01's cap is 8s per HTTP
    # request with one repair retry, so ~16s worst case (PRD §7). Holding a
    # webhook ack open that long invites Razorpay's own delivery timeout and
    # a redelivery of an event already durably stored, which is a
    # self-inflicted duplicate. The ack answers exactly one question — "is it
    # stored?" — and nothing slower is allowed to change that answer.
    #
    # RECEIVED only, never DUPLICATE: FR-02's acceptance is "same rz_event_id
    # replayed 100x produces one case and one set of actions", and the dedupe
    # that guarantees it is the unique index this branch reads the result of.
    # Re-running the pipeline on a replay would push that guarantee down onto
    # the executor's idempotency keys alone.
    if result.outcome is IngestOutcome.RECEIVED and result.document is not None:
        background.add_task(_process_if_actionable, result.document, repos)

    body: dict[str, Any] = {"status": result.outcome.value}
    if result.detail:
        body["detail"] = result.detail
    return JSONResponse(body, status_code=_STATUS_FOR_OUTCOME[result.outcome])


async def _process_if_actionable(document: dict[str, Any], repos: Repositories) -> None:
    """Run the decision pipeline for a stored, signature-valid event.

    Uses the payload the ingest layer already decoded rather than re-parsing
    the raw body: re-parsing after verification is how a verified-then-mutated
    body sneaks in, and there is no reason to hold two decodings of the same
    bytes. `dry_run` and `kill_switch_engaged` are left to
    `process_payment_failed()`'s own `settings()` resolution — passing
    literals here is exactly the bypass-by-omission that function's docstring
    warns about.

    Nothing here can change the webhook's status code (it has already been
    sent), so a failure that vanished into a background task would be a
    silent one. It goes to the exception desk instead, which is what that
    collection is for.
    """
    if document.get("event_type") != PROCESSED_EVENT_TYPE:
        return
    payload = document.get("payload")
    if not isinstance(payload, dict):
        return  # unparseable body, stored verbatim by ingest; nothing to decide on
    try:
        await process_payment_failed(payload, repos)
    except Exception as exc:
        await repos.exceptions.insert(
            {
                "case_id": None,
                "reason_code": "PIPELINE_FAILED",
                "detail": {
                    "rz_event_id": document.get("rz_event_id"),
                    "event_type": document.get("event_type"),
                    "error": f"{type(exc).__name__}: {exc}",
                },
                "ts": dt.datetime.now(dt.UTC),
                "resolved_by": None,
            }
        )
