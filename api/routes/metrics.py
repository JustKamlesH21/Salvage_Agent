"""Batch metrics, the rulebook, and the exception desk.

The batch endpoint serves `eval/results.json` exactly as the harness produced
it. It does not recompute, re-aggregate or re-round anything: the committed
file is the artifact the README quotes and the video shows, and an API that
derived its own version of those numbers would create a second answer to the
same question.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from api.gates import registry
from api.policy import RULES as POLICY_RULES
from api.routes.cases import RepositoriesDep, _jsonable_doc

router = APIRouter(tags=["metrics"])

RESULTS_PATH = Path(__file__).resolve().parent.parent.parent / "eval" / "results.json"


@router.get("/metrics/batch")
@router.get("/metrics/batch/{run_id}")
async def batch_metrics(run_id: str | None = None) -> dict[str, Any]:
    if not RESULTS_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail="No batch results yet. Run `make batch` to produce eval/results.json.",
        )
    report: dict[str, Any] = json.loads(RESULTS_PATH.read_text())
    if run_id:
        report["requested_run_id"] = run_id
    return report


@router.get("/rulebook")
async def rulebook() -> dict[str, Any]:
    """The entire decision logic, rendered. Every policy rule with its
    `because` and citation, and every gate with its clause reference and rule
    text — the same constants the code evaluates, read off the modules
    themselves rather than transcribed into a document that can drift.
    """
    gates = []
    for gate_fn in registry.GATES_IN_ORDER:
        module = __import__(gate_fn.__module__, fromlist=["CLAUSE_REF"])
        gates.append(
            {
                # The module name is the gate id — g3_afa is G3_AFA. This used to
                # prefer the part of CLAUSE_REF before a colon, which no clause
                # reference has ever contained: the branch never fired, and the
                # first citation to include a colon would have silently renamed
                # its gate to a fragment of a legal reference.
                "gate": module.__name__.rsplit(".", 1)[-1].upper(),
                "module": module.__name__,
                "clause_ref": module.CLAUSE_REF,
                "rule_text": module.RULE_TEXT,
                # Present only where the gate checks something narrower than, or
                # different from, the clause it cites (PRD §9). Serving it here is
                # what stops a screen rendering the clause as a description of
                # behaviour the gate does not have.
                "scope_note": getattr(module, "SCOPE_NOTE", None),
            }
        )

    return {
        "gates": gates,
        "gate_order_note": (
            "Evaluated in this order, always. G0_KILL is first and no argument, "
            "flag or config value reorders or skips any of them (CLAUDE.md invariant 4)."
        ),
        "remediation": {gate: action.value for gate, action in registry.REMEDIATION.items()},
        "max_remediation_hops": registry.MAX_REMEDIATION_HOPS,
        "policy_rules": [
            {
                "id": rule.id,
                "priority": rule.priority,
                "when": rule.when,
                "action": rule.action.value,
                "because": rule.because,
                "cites": list(rule.cites),
                "timing": rule.timing,
                "stop_all_retries": rule.stop_all_retries,
                "max_delay_minutes": rule.max_delay_minutes,
            }
            for rule in POLICY_RULES
        ],
    }


@router.get("/exceptions")
async def exceptions(repos: RepositoriesDep, limit: int = 100) -> dict[str, Any]:
    """What the agent refused to handle, and why.

    Reachable in one click from the deck and not hidden behind a tab (FR-18):
    a system that only shows its successes is not showing its work.
    """
    rows = await repos.exceptions.find({}, limit=limit, sort=[("ts", -1)])
    return _jsonable_doc({"exceptions": rows, "count": len(rows)})
