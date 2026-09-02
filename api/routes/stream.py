"""GET /stream/decisions — the live decision feed behind S1's right-hand column.

SSE, not websockets, and polled rather than tailed. Both choices are about
having less to go wrong on demo day (SPEC §6.4 says so for SSE): a change
stream needs a replica set, which the local and mongomock setups this project
runs on do not provide, and a dropped change stream fails in a way that looks
like "nothing is happening" rather than an error. A poll that finds nothing
new is indistinguishable from a quiet system, which is the correct appearance.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from api.db.repositories import Repositories
from api.routes.cases import RepositoriesDep, _jsonable, _summarise_case

router = APIRouter(tags=["stream"])

POLL_SECONDS = 1.5

# How much history a client sees on connect, so a judge opening the deck
# mid-demo is not looking at an empty column.
BACKFILL = 15


@router.get("/stream/decisions")
async def stream_decisions(request: Request, repos: RepositoriesDep) -> EventSourceResponse:
    return EventSourceResponse(_decision_events(request, repos))


async def _decision_events(request: Request, repos: Repositories) -> AsyncIterator[dict[str, Any]]:
    seen: set[str] = set()
    backfilled = await repos.decisions.find(
        {"run_id": None}, sort=[("created_at", -1)], limit=BACKFILL
    )
    for decision in reversed(backfilled):
        seen.add(str(decision.get("decision_id")))
        yield {"event": "decision", "data": _event_payload(decision)}

    while True:
        # A client that has navigated away leaves this generator running and
        # polling forever otherwise.
        if await request.is_disconnected():
            break

        recent = await repos.decisions.find(
            {"run_id": None}, sort=[("created_at", -1)], limit=BACKFILL
        )
        for decision in reversed(recent):
            decision_id = str(decision.get("decision_id"))
            if decision_id in seen:
                continue
            seen.add(decision_id)
            yield {"event": "decision", "data": _event_payload(decision)}

        await asyncio.sleep(POLL_SECONDS)


def _event_payload(decision: dict[str, Any]) -> str:
    import json

    blocked = [g for g in decision.get("gate_results", []) if g.get("verdict") == "BLOCK"]
    return json.dumps(
        _jsonable(
            {
                "decision_id": decision.get("decision_id"),
                "case_id": decision.get("case_id"),
                "at": decision.get("created_at") or dt.datetime.now(dt.UTC),
                "failure_class": decision.get("diagnosis", {}).get("failure_class"),
                "rule_id": decision.get("policy", {}).get("rule_id"),
                "action": decision.get("final", {}).get("action"),
                "verdict": decision.get("final", {}).get("verdict"),
                "blocked_by": [{"gate": g["gate"], "clause_ref": g["clause_ref"]} for g in blocked],
            }
        )
    )


__all__ = ["_summarise_case", "router"]
