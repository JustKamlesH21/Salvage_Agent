"""Read routes for the frontend — SPEC §7's contract.

Everything here is read-only and scoped to the live partition (`run_id: None`)
unless a run is named explicitly. An eval batch's synthetic cases share the
database but not the dashboard: a judge looking at the Command Deck should be
looking at real traffic, not at forty simulated mandates.

The decision document these return is exactly what `api/pipeline.py` writes,
unflattened — the Decision Tape renders PRD §8.2's shape directly rather than
a projection invented here, so there is one definition of what a decision is.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from api.db.client import get_db
from api.db.repositories import Repositories
from api.gates.types import CONTACT_ACTIONS
from api.policy.types import Action

router = APIRouter(prefix="/cases", tags=["cases"])

CHARGE_ACTION_VALUES = {Action.RETRY_NOW.value, Action.RETRY_SCHEDULED.value}
CONTACT_ACTION_VALUES = {a.value for a in CONTACT_ACTIONS}


def get_repositories(
    db: Annotated[AsyncIOMotorDatabase[Any], Depends(get_db)],
) -> Repositories:
    return Repositories(db)


RepositoriesDep = Annotated[Repositories, Depends(get_repositories)]


def _case_id(raw: str) -> Any:
    """Case ids are Mongo ObjectIds for live traffic and plain strings for
    anything seeded by hand. Accept both rather than 404-ing on a valid id
    that simply is not hex.
    """
    try:
        return ObjectId(raw)
    except (InvalidId, TypeError):
        return raw


def _jsonable(value: Any) -> Any:
    """ObjectId and datetime, made JSON-safe, recursively.

    Datetimes are stored UTC and rendered as ISO-8601 with the offset intact.
    The frontend converts to IST for display and always shows the zone — half
    the compliance logic in this system is about clocks, so a timestamp that
    does not say which one it is in is a bug waiting to be believed.
    """
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, dt.datetime):
        stamped = value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)
        return stamped.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def _jsonable_doc(value: dict[str, Any]) -> dict[str, Any]:
    """`_jsonable` for a document, keeping the mapping type through the walk.

    The recursive form has to return `Any` — it handles scalars too — and a
    route declared to return a dict cannot return that without erasing its own
    signature. This is the one narrowing point rather than a cast at every
    call site.
    """
    walked = _jsonable(value)
    if not isinstance(walked, dict):  # unreachable for a dict input
        raise TypeError(f"expected a document, walked to {type(walked).__name__}")
    return walked


@router.get("")
async def list_cases(
    repos: RepositoriesDep,
    state: str | None = None,
    failure_class: str | None = Query(default=None, alias="class"),
    run_id: str | None = None,
    limit: int = Query(default=100, le=500),
) -> dict[str, Any]:
    query: dict[str, Any] = {"run_id": run_id}
    if state:
        query["state"] = state

    cases = await repos.cases.find(query, limit=limit, sort=[("opened_at", -1)])
    latest = {c["_id"]: await _latest_decision(repos, c["_id"]) for c in cases}

    if failure_class:
        cases = [
            c
            for c in cases
            if (decision := latest[c["_id"]]) is not None
            and decision.get("diagnosis", {}).get("failure_class") == failure_class
        ]

    return {
        "cases": [_summarise_case(c, latest[c["_id"]]) for c in cases],
        "counters": await _counters(repos, run_id),
    }


async def _latest_decision(repos: Repositories, case_id: Any) -> dict[str, Any] | None:
    decisions = await repos.decisions.find({"case_id": case_id}, sort=[("created_at", -1)], limit=1)
    return decisions[0] if decisions else None


def _summarise_case(case: dict[str, Any], decision: dict[str, Any] | None) -> dict[str, Any]:
    return _jsonable_doc(
        {
            "id": case["_id"],
            "subscription_id": case.get("subscription_id"),
            "customer_ref": _customer_ref(case),
            "amount_paise": case.get("amount_paise", 0),
            "currency": case.get("currency", "INR"),
            "mandate_type": case.get("mandate_type"),
            "category": case.get("category"),
            "state": case.get("state"),
            "opened_at": case.get("opened_at"),
            "failure_class": (decision or {}).get("diagnosis", {}).get("failure_class"),
            "final_action": (decision or {}).get("final", {}).get("action"),
            "final_verdict": (decision or {}).get("final", {}).get("verdict"),
            "decision_id": (decision or {}).get("decision_id"),
        }
    )


def _customer_ref(case: dict[str, Any]) -> str:
    """A short, stable, non-identifying handle for the UI.

    PRD §8.2 stores contact and email hashed and the UI shows a reference, not
    a person — there is no screen in this product that needs a customer's name
    to make a money decision legible.
    """
    raw = case.get("customer_id") or case.get("subscription_id") or str(case.get("_id"))
    return f"cust_{str(raw)[-6:]}"


async def _counters(repos: Repositories, run_id: str | None) -> dict[str, Any]:
    """S1's four counters. `blocked_by_compliance` is deliberately given the
    same weight as `recovered` — restraint as a first-class outcome is the
    product's whole thesis (SPEC §6.3).
    """
    cases = await repos.cases.find({"run_id": run_id}, limit=1000)
    case_ids = [c["_id"] for c in cases]

    at_risk = sum(c.get("amount_paise", 0) for c in cases)
    recovered = sum(c.get("recovered_paise", 0) for c in cases)
    in_flight = sum(1 for c in cases if c.get("state") in ("open", "recovering"))

    blocked = 0
    for case_id in case_ids:
        for row in await repos.audit.by_case(case_id):
            if row.get("phase") == "block":
                blocked += 1

    return {
        "at_risk_paise": at_risk,
        "recovered_paise": recovered,
        "recovery_rate": (recovered / at_risk) if at_risk else 0.0,
        "in_flight_cases": in_flight,
        "blocked_by_compliance": blocked,
        "total_cases": len(cases),
    }


@router.get("/{case_id}")
async def get_case(case_id: str, repos: RepositoriesDep) -> dict[str, Any]:
    resolved = _case_id(case_id)
    case = await repos.cases.get(resolved)
    if case is None:
        raise HTTPException(
            status_code=404,
            detail=f"No case {case_id}. Check the id from GET /cases, or the case may "
            f"belong to an eval run rather than live traffic.",
        )

    decisions = await repos.decisions.by_case(resolved)
    audit = await repos.audit.by_case(resolved)

    return _jsonable_doc(
        {
            "case": {**case, "customer_ref": _customer_ref(case)},
            "decisions": [_decision_tape(d, audit) for d in decisions],
        }
    )


def _decision_tape(decision: dict[str, Any], audit: list[dict[str, Any]]) -> dict[str, Any]:
    """One decision, plus the audit rows that belong to it.

    The join is on `decision_id`, which the decision document carries in its
    own body — it did not until day 4's review found the join was structurally
    impossible despite comments claiming otherwise.
    """
    decision_id = decision.get("decision_id")
    rows = [r for r in audit if r.get("decision_id") == decision_id]
    return {
        **decision,
        "executed": [
            {
                "action": r.get("action"),
                "phase": r.get("phase"),
                "at": r.get("ts"),
                "dry_run": r.get("dry_run"),
                "response": r.get("response"),
                "clause_ref": r.get("clause_ref"),
                "note": r.get("note"),
            }
            for r in rows
        ],
    }


@router.get("/{case_id}/audit")
async def get_case_audit(case_id: str, repos: RepositoriesDep) -> dict[str, Any]:
    """The raw audit documents, hash chain fields and all.

    Unprojected on purpose: this is the screen a sceptic reads, and a summary
    would be exactly the wrong thing to hand them.
    """
    rows = await repos.audit.by_case(_case_id(case_id))
    return _jsonable_doc({"case_id": case_id, "audit": rows})
