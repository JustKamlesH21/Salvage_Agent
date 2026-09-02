"""execute_action() — the single choke point for every action Salvage takes.
SPEC §5.6, CLAUDE.md invariants 1/8/9.

Insert-first idempotency: the pre-audit row is inserted *before* dispatch, and
`DuplicateKeyError` on that insert means "already executed" — the prior
result is returned, the action is never re-dispatched. This makes
double-execution structurally impossible rather than checked-then-raced.

In-doubt reconciliation is deliberately out of scope (per instruction, logged
in LIMITATIONS.md next to FR-28): a pre-row with no post-row is reported as
`"in_flight"`, correctly distinguished from "never attempted" per invariant
9's own wording, but nothing here actively resolves it later.

`client.py`'s real `dispatch()` stays a stub — live Razorpay execution (F20)
is out of scope for this compressed submission (PRD §6); every call in this
build runs with `dry_run=True`, exercising `simulate()` instead. Even so,
`dry_run=False` alone is not enough to reach that branch: `api.executor.arm`
must have been explicitly armed with the confirm token first (invariant 8) —
found unenforced by compliance-auditor's Day 4 review; `DRY_RUN=false` in the
environment used to be sufficient on its own, contradicting CLAUDE.md's own
claim and the Makefile/README text describing an arm requirement that no
code actually checked.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from pymongo.errors import DuplicateKeyError

from api.db.repositories import AuditRepository
from api.executor import client

# `from api.executor.arm import is_armed`, not `from api.executor import arm`:
# api.executor's own __init__.py re-exports the *function* `arm` under that
# same name, which shadows the submodule as an attribute of the `api.executor`
# package (even `import api.executor.arm as x` resolves through that same
# attribute and hits the function, not the module — verified by hand).
# Importing the name directly from the submodule's own path sidesteps the
# collision entirely.
from api.executor.arm import is_armed
from api.executor.types import ActionResult
from api.policy.types import Action


def simulate(action: Action, payload: dict[str, Any]) -> dict[str, Any]:
    """A small, honest fake response per action — never a fabricated-looking
    real id. Dry-run only; never reaches Razorpay.
    """
    return {
        "simulated": True,
        "action": action.value,
        "note": "dry-run — no real dispatch",
        **payload,
    }


async def execute_action(
    *,
    case_id: str,
    decision_id: str,
    action: Action,
    payload: dict[str, Any],
    idempotency_key: str,
    dry_run: bool,
    audit: AuditRepository,
    kill_switch_engaged: bool,
    actor: str = "system",
    run_id: Any = None,
) -> ActionResult:
    # Bounded set (SPEC §5.6's own pseudocode uses `assert`; an explicit
    # check is used here instead because `assert` is stripped under
    # `python -O`, which would silently remove the one line standing between
    # arbitrary input and the catalog — compliance-auditor F9.
    if not isinstance(action, Action):
        raise ValueError(f"{action!r} is not in the bounded action catalog (SPEC §4.2)")

    if kill_switch_engaged:
        return ActionResult(outcome="blocked_kill_switch", idempotency_key=idempotency_key)

    if not dry_run and not is_armed():
        return ActionResult(outcome="blocked_not_armed", idempotency_key=idempotency_key)

    def _utcnow() -> dt.datetime:
        return dt.datetime.now(dt.UTC)

    pre_document = {
        "case_id": case_id,
        "decision_id": decision_id,
        "ts": _utcnow(),
        "actor": actor,
        "phase": "pre",
        "action": action.value,
        "idempotency_key": idempotency_key,
        "request": payload,
        "response": None,
        "dry_run": dry_run,
        "clause_ref": None,
        "note": None,
    }
    try:
        await audit.insert(pre_document, run_id=run_id)
    except DuplicateKeyError:
        prior = await audit.prior_result(idempotency_key)
        if prior is None:
            # A pre-row exists but no post-row: dispatch is in flight or died
            # mid-flight. NOT "never attempted" — invariant 9.
            return ActionResult(outcome="in_flight", idempotency_key=idempotency_key)
        return ActionResult(
            outcome="already_executed",
            idempotency_key=idempotency_key,
            response=prior.get("response"),
            dry_run=prior.get("dry_run"),
        )

    response = (
        simulate(action, payload)
        if dry_run
        else client.dispatch(action.value, payload, idempotency_key)
    )

    post_document = {
        "case_id": case_id,
        "decision_id": decision_id,
        "ts": _utcnow(),
        "actor": actor,
        "phase": "post",
        "action": action.value,
        "idempotency_key": idempotency_key,
        "request": payload,
        "response": response,
        "dry_run": dry_run,
        "clause_ref": None,
        "note": None,
    }
    await audit.insert(post_document, run_id=run_id)

    return ActionResult(
        outcome="executed", idempotency_key=idempotency_key, response=response, dry_run=dry_run
    )
