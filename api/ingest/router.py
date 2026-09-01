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

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from motor.motor_asyncio import AsyncIOMotorDatabase

from api.config import settings
from api.db.client import get_db
from api.db.repositories import EventRepository, ExceptionRepository
from api.ingest.webhook import IngestOutcome, ingest_webhook

router = APIRouter()

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


@router.post("/webhooks/razorpay")
async def receive_webhook(
    request: Request,
    events: Annotated[EventRepository, Depends(get_event_repository)],
) -> JSONResponse:
    raw = await request.body()  # first statement — nothing above touches it

    result = await ingest_webhook(
        raw,
        request.headers,
        events,
        webhook_secret=settings().razorpay_webhook_secret,
    )

    body: dict[str, Any] = {"status": result.outcome.value}
    if result.detail:
        body["detail"] = result.detail
    return JSONResponse(body, status_code=_STATUS_FOR_OUTCOME[result.outcome])
