"""The case history: what the gates need to know, and where refusals are filed.

Everything a gate reasons about is a fact about this case's past — how many
charge attempts fall inside G4_BUDGET's fourteen days, when the last contact
was for G5_COOLDOWN's forty-eight hours, whether a pre-debit notice went out
early enough for G2. None of it is cached: `build_gate_context()` re-derives
all of it from `audit` and `customers` on every call, which is the whole
mechanism behind fire-time re-evaluation (CLAUDE.md invariant 5). A context
built at schedule time and reused would defeat the invariant silently.

The refusals go back to the same place they came from. `write_block_audit_rows()`
files one row per blocking gate, carrying its clause — SPEC §5.5 — so the
count of blocked-by-compliance actions is derivable from storage rather than
from whatever the gate chain happened to return.

Why this is its own module: both `api.pipeline` (a webhook arriving) and
`api.scheduler.live` (a scheduled action coming due) need exactly these two
functions and neither wants the other's orchestration. Until day 6 they lived
inside `api/pipeline.py`, and the scheduler imported `_write_block_audit_rows`
through the underscore — a module reaching past another module's seam, which
is the code saying the seam is in the wrong place.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from api.db.repositories import Repositories
from api.gates.types import CHARGE_ACTIONS, CONTACT_ACTIONS, GateContext
from api.policy.types import Action

# G4_BUDGET counts Salvage-initiated charge attempts inside this window;
# G5_COOLDOWN counts contacts inside the shorter one.
SALVAGE_ATTEMPT_WINDOW = dt.timedelta(days=14)
CONTACT_WINDOW = dt.timedelta(days=7)


def as_utc(value: dt.datetime) -> dt.datetime:
    """Mongo (mongomock included) round-trips a stored datetime back as
    naive — BSON has no timezone concept, so tzinfo is stripped on the way
    out even though every write in this codebase stores UTC (invariant 10).
    Comparing a freshly-read `r["ts"]` against a tz-aware `now_utc` without
    this raises `TypeError: can't compare offset-naive and offset-aware
    datetimes` — found by this phase's own pipeline test, the same class of
    "storage round-trip doesn't preserve what the write-side assumed" gap
    NOTES.md's day-2 entries already logged for the hash chain's millisecond
    precision.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)


def dedupe_attempts_by_key(audit_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per `idempotency_key`, preferring `phase="post"` over `"pre"`
    when both exist for the same key. Counting `pre`-only rows (not just
    `post`) is deliberate: a dispatch that inserted its pre-audit row and
    died before the post row is a real attempt — or may have been — and
    under-counting it means G4_BUDGET/G5_COOLDOWN can be satisfied by
    attempts that silently never got recorded as complete (compliance-auditor
    F5). This is the conservative direction: over-counting a possible attempt
    costs one skipped retry; under-counting risks exceeding the attempt
    budget the gate exists to enforce.
    """
    by_key: dict[str, dict[str, Any]] = {}
    for row in audit_rows:
        if row.get("phase") not in ("pre", "post"):
            continue
        key = str(row.get("idempotency_key"))
        existing = by_key.get(key)
        if existing is None or (existing.get("phase") == "pre" and row.get("phase") == "post"):
            by_key[key] = row
    return list(by_key.values())


async def build_gate_context(
    case: dict[str, Any],
    action: Action,
    now_utc: dt.datetime,
    *,
    idempotency_key: str,
    repos: Repositories,
    amount_paise: int,
    kill_switch_engaged: bool,
) -> GateContext:
    """Everything gates need, re-derived fresh from `audit` (the durable
    record of what actually happened) and `customers` — never cached from an
    earlier decision cycle. This is what fire-time re-evaluation depends on.

    `amount_paise` and `kill_switch_engaged` are both required, explicit
    keyword arguments — no default. `amount_paise`: taken by the caller from
    the *current* webhook payload, not read from the case document
    (compliance-auditor F3: the case document is written once at
    find-or-create and never updated, so a later, larger failure on the same
    case would silently reuse the first failure's amount and could pass
    G3_AFA on a stale, smaller figure). `kill_switch_engaged`: a `= False`
    default here is exactly the "bypass by omission" `process_payment_failed()`'s
    own docstring warns about — spec-guardian D4 found a test in this
    codebase that already called this function without passing it, proving
    the omission is realistic, not hypothetical. `category`, `mandate_type`
    and `subscription_state` still come from `case` — real limitations of
    this phase's payment.failed-only scope, documented in LIMITATIONS.md
    rather than silently accepted here.
    """
    case_id = case["_id"]
    audit_rows = await repos.audit.by_case(case_id)
    attempt_rows = [{**r, "ts": as_utc(r["ts"])} for r in dedupe_attempts_by_key(audit_rows)]

    charge_window_start = now_utc - SALVAGE_ATTEMPT_WINDOW
    salvage_attempts_in_window = tuple(
        r["ts"]
        for r in attempt_rows
        if r.get("action") in {a.value for a in CHARGE_ACTIONS} and r["ts"] > charge_window_start
    )

    contact_window_start = now_utc - CONTACT_WINDOW
    contact_rows = sorted(
        (r for r in attempt_rows if r.get("action") in {a.value for a in CONTACT_ACTIONS}),
        key=lambda r: r["ts"],
    )
    contacts_in_7d = tuple(r["ts"] for r in contact_rows if r["ts"] > contact_window_start)
    last_contact_at = contact_rows[-1]["ts"] if contact_rows else None

    # The pre-debit notice's lead time is only real once the notice is
    # confirmed *sent* (post), unlike the budget/cooldown counts above where
    # the conservative direction is the opposite — see _dedupe_attempts_by_key.
    notice_rows = [
        {**r, "ts": as_utc(r["ts"])}
        for r in audit_rows
        if r.get("phase") == "post" and r.get("action") == Action.SEND_PRE_DEBIT_NOTICE.value
    ]
    pre_debit_notice_sent_at = max((r["ts"] for r in notice_rows), default=None)

    customer_id = case.get("customer_id")
    customer = await repos.customers.get(customer_id) if customer_id else None
    opted_out = bool(customer and customer.get("opt_out_at") is not None)
    has_confirmation_channel = bool(
        customer and (customer.get("contact_hash") or customer.get("email_hash"))
    )

    idempotency_key_exists = await repos.audit.any_phase_exists(idempotency_key)

    return GateContext(
        now_utc=now_utc,
        action=action,
        kill_switch_engaged=kill_switch_engaged,
        amount_paise=amount_paise,
        category=case.get("category", "standard"),
        mandate_type=case.get("mandate_type", "card"),
        subscription_state=case.get("subscription_status", "active"),
        salvage_attempts_in_window=salvage_attempts_in_window,
        contacts_in_7d=contacts_in_7d,
        last_contact_at=last_contact_at,
        pre_debit_notice_sent_at=pre_debit_notice_sent_at,
        opted_out=opted_out,
        contact_target="account_holder",  # no code path in this build ever sets it otherwise (G7)
        has_confirmation_channel=has_confirmation_channel,
        idempotency_key=idempotency_key,
        idempotency_key_exists=idempotency_key_exists,
    )


async def write_block_audit_rows(
    chain_blocking: list[Any],
    *,
    case_id: Any,
    decision_id: str,
    proposed_action: Action,
    repos: Repositories,
    dry_run: bool,
    now_utc: dt.datetime,
    run_id: Any = None,
) -> None:
    """SPEC §5.5: "Every BLOCK writes an audit document with the clause
    reference." One row per blocking gate, phase="block" — distinct from the
    executor's own pre/post rows for whatever final action actually dispatches.

    `now_utc` is the decision's own clock, not the wall clock, for the reason
    execute_action()'s docstring gives: the gate chain re-derives its context
    from these timestamps, so they have to agree with the time the gates were
    evaluated at.
    """
    for verdict in chain_blocking:
        key = f"{case_id}:{decision_id}:{proposed_action.value}:BLOCK:{verdict.gate}"
        await repos.audit.insert(
            {
                "case_id": case_id,
                "decision_id": decision_id,
                "ts": now_utc,
                "actor": "system",
                "phase": "block",
                "action": proposed_action.value,
                "idempotency_key": key,
                "request": None,
                "response": None,
                "dry_run": dry_run,
                "clause_ref": verdict.clause_ref,
                "note": verdict.detail,
            },
            run_id=run_id,
        )
