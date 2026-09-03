"""webhook -> normalise -> diagnose -> policy -> gates -> executor -> audit.

Not built Day 3 on purpose (noted then as "Day 4's job" — gates need a
decision to evaluate). This is that wiring. `process_payment_failed()`'s
output is exactly the `decisions` document PRD §8.2 defines, because that
document is what Day 5's Decision Tape renders — the shape is decided here,
not discovered while building the frontend.

Scope, stated once rather than scattered in comments: only `payment.failed`
is handled (PRD §2: Salvage sits between `payment.failed` and
`subscription.halted` — that is the one event the product's whole thesis is
about). No reconciliation sweep, no state-machine handling of the other five
webhook types. Case creation is minimal: find-or-create, seed zeros.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid
from typing import Any

from api.config import settings
from api.db.repositories import Repositories
from api.diagnose import DiagnosisContext, diagnose
from api.executor import execute_action

# Direct name import, not `from api.executor import killswitch`: the package's
# __init__ re-exports names that shadow same-named submodules as package
# attributes — the trap day 4 hit with `arm` (see executor.py's note).
from api.executor.killswitch import engaged as killswitch_engaged
from api.gates.registry import ChainResult, evaluate_chain, resolve_with_remediation
from api.gates.types import CHARGE_ACTIONS, CONTACT_ACTIONS, GateContext, VerdictKind
from api.normalise import classify_payload
from api.normalise.types import FailureClass
from api.policy import RULES as POLICY_RULES
from api.policy import PolicyContext, evaluate
from api.policy.timing import earliest_attempt_at
from api.policy.types import Action

SALVAGE_ATTEMPT_WINDOW = dt.timedelta(days=14)
CONTACT_WINDOW = dt.timedelta(days=7)


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _as_utc(value: dt.datetime) -> dt.datetime:
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


def _case_opened_at(case: dict[str, Any], fallback: dt.datetime) -> dt.datetime:
    """When this case's trouble started — the anchor a scheduled retry is
    measured from. Falls back to `fallback` for a case document written
    before `opened_at` was stored, rather than silently anchoring on the
    wall clock.
    """
    opened = case.get("opened_at")
    return _as_utc(opened) if isinstance(opened, dt.datetime) else fallback


async def find_or_create_case(
    payment_entity: dict[str, Any],
    repos: Repositories,
    *,
    run_id: Any = None,
    now_utc: dt.datetime | None = None,
) -> dict[str, Any]:
    """Keyed on the first of `subscription_id`, `invoice_id`, `order_id`,
    `id` (the payment id) that's actually present, scoped to `run_id`.

    Real `payment.failed` captures in this account carry `invoice_id` but not
    `subscription_id` directly on `payload.payment.entity` (NOTES.md's Phase
    4 entry) — and fixture 029, the one real usable decline capture, carries
    neither (`invoice_id: null`), only `order_id` and the payment `id`. A
    two-field fallback silently fails to dedupe that exact case and opens a
    new one every time; found by the pipeline's own duplicate-webhook test,
    not by inspection.

    `run_id` scopes the lookup as well as the stored document: a case belongs
    to exactly one partition (live is `None`, each eval batch its own), and an
    unscoped lookup would let a synthetic batch case adopt a live case's
    identifiers, or vice versa. PRD §8.2's `cases` shape has carried `run_id`
    since day 0; nothing set it until the day-5 eval harness needed real run
    isolation.
    """
    candidates = {
        "subscription_id": payment_entity.get("subscription_id"),
        "invoice_id": payment_entity.get("invoice_id"),
        "order_id": payment_entity.get("order_id"),
        "payment_id": payment_entity.get("id"),
    }
    for field, value in candidates.items():
        if not value:
            continue
        existing = await repos.cases.find({field: value, "run_id": run_id}, limit=1)
        if existing:
            return existing[0]

    method = payment_entity.get("method", "card")
    doc: dict[str, Any] = {
        **candidates,
        "run_id": run_id,
        "customer_id": payment_entity.get("customer_id"),
        "amount_paise": int(payment_entity.get("amount") or 0),
        "currency": payment_entity.get("currency", "INR"),
        "mandate_type": "upi_autopay" if method == "upi" else "card",
        "category": "standard",
        # Razorpay's own subscription lifecycle state — distinct from `state`
        "subscription_status": "active",
        "state": "open",  # Salvage's own case lifecycle (PRD §8.2)
        # The decision's clock, not the wall clock: this is the anchor
        # api.policy.timing measures a scheduled retry from, and the batch
        # harness drives cases on a simulated clock.
        "opened_at": now_utc or _utcnow(),
        "closed_at": None,
        "outcome": None,
        "recovered_paise": 0,
        "attempts": [],
        "salvage_attempt_count": 0,
        "last_contact_at": None,
        "contact_count_7d": 0,
    }
    case_id = await repos.cases.insert(doc)
    doc["_id"] = case_id
    return doc


def build_diagnosis_context(
    case: dict[str, Any],
    failure_reason: str | None,
    *,
    attempt_timestamps: tuple[dt.datetime, ...] = (),
    prior_failure_classes: tuple[FailureClass, ...] = (),
) -> DiagnosisContext:
    """SPEC §5.3 rule 1's permitted fields only — invariant 3 by construction.

    `attempt_timestamps` and `prior_failure_classes` are supplied by the
    caller (from `audit` and `decisions` respectively) rather than defaulted
    empty here — spec-guardian: an earlier version hardcoded both to `()`
    unconditionally, so arm C's LLM was always diagnosing from a poorer
    prompt than SPEC §5.3 rule 1 describes ("attempt history (count +
    timestamps + prior classes)"), silently. `prior_successful_charge_dates`
    stays `()` — this phase has no data source for it at all (only
    `payment.failed` is processed; a successful-charge history would need
    `subscription.charged`/`invoice.paid` handling, out of scope), documented
    in LIMITATIONS.md rather than left unexplained.
    """
    return DiagnosisContext(
        failure_reason=failure_reason,
        attempt_count=len(attempt_timestamps),
        attempt_timestamps=attempt_timestamps,
        prior_failure_classes=prior_failure_classes,
        mandate_type=case.get("mandate_type", "card"),
        prior_successful_charge_dates=(),
    )


def _dedupe_attempts_by_key(audit_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
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


async def _salvage_charge_attempts_in_window(
    case_id: Any, now_utc: dt.datetime, repos: Repositories
) -> tuple[dt.datetime, ...]:
    """Salvage-only `RETRY_NOW`/`RETRY_SCHEDULED` timestamps in the trailing
    14 days, derived from `audit` — the same source and window
    `build_gate_context()` uses for G4_BUDGET, reused here so
    `PolicyContext.attempt_count` and `DiagnosisContext.attempt_count`/
    `.attempt_timestamps` are the real count, not the case document's
    `salvage_attempt_count` field (seeded 0, never incremented — spec-guardian
    D5: this field being fed into policy meant POL-009's budget-exhaustion
    rule could never match in production, silently).
    """
    audit_rows = await repos.audit.by_case(case_id)
    attempt_rows = [{**r, "ts": _as_utc(r["ts"])} for r in _dedupe_attempts_by_key(audit_rows)]
    window_start = now_utc - SALVAGE_ATTEMPT_WINDOW
    charge_values = {a.value for a in CHARGE_ACTIONS}
    timestamps = sorted(
        r["ts"] for r in attempt_rows if r.get("action") in charge_values and r["ts"] > window_start
    )
    return tuple(timestamps)


async def _prior_failure_classes(case_id: Any, repos: Repositories) -> tuple[FailureClass, ...]:
    """Every previous decision cycle's effective diagnosis for this case, in
    order — SPEC §5.3 rule 1's "prior classes" input, sourced from
    `decisions` rather than left permanently empty.
    """
    prior_decisions = await repos.decisions.find({"case_id": case_id}, sort=[("created_at", 1)])
    classes: list[FailureClass] = []
    for doc in prior_decisions:
        raw = doc.get("diagnosis", {}).get("failure_class")
        if not raw:
            continue
        try:
            classes.append(FailureClass(raw))
        except ValueError:
            # A value that is not a member of the enum is bad data, and the
            # place to fix it is wherever wrote it — but refusing the whole
            # webhook over one unreadable historical row would tell Razorpay to
            # keep retrying an event this system will never accept. Skipped, so
            # the cycle proceeds on the classes it can read. (compliance-auditor
            # F4, day 6: the scheduler briefly wrote "SCHEDULED" here.)
            continue
    return tuple(classes)


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
    attempt_rows = [{**r, "ts": _as_utc(r["ts"])} for r in _dedupe_attempts_by_key(audit_rows)]

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
        {**r, "ts": _as_utc(r["ts"])}
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


async def _write_block_audit_rows(
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


async def process_payment_failed(
    payload: dict[str, Any],
    repos: Repositories,
    *,
    now_utc: dt.datetime | None = None,
    dry_run: bool | None = None,
    kill_switch_engaged: bool | None = None,
    disable_llm: bool | None = None,
    run_id: Any = None,
) -> dict[str, Any]:
    """`dry_run` and `kill_switch_engaged` default from `settings()` when not
    passed explicitly — never a bare `False`/`True` literal default. A safety
    flag whose default silently means "never engaged" is a bypass by omission
    the moment a future caller (the live webhook route) forgets to plumb it
    through; reading `settings()` here means there is no such omission to
    make. Same pattern api.diagnose.client.diagnose() already uses for
    `disable_llm` (Day 3).

    `run_id` partitions everything this call writes — the case, the decision,
    and every audit row (the audit chain is partitioned per run by design,
    api/db/repositories.py). `None` is the live partition. Before day 5 this
    was hardcoded `None` on the decision document and simply never passed to
    `execute_action()` or the block rows, so an eval batch would have written
    its synthetic rows into the live demo's hash chain.
    """
    cfg = settings()
    if dry_run is None:
        dry_run = cfg.dry_run
    if kill_switch_engaged is None:
        # The runtime switch, not only the configured one — POST /control/kill
        # engages it after startup, and reading `cfg.kill_switch` here would
        # mean a kill switch that changes what the UI says and nothing about
        # what the system does (FR-10: engaging it freezes pending actions).
        kill_switch_engaged = killswitch_engaged()

    now_utc = now_utc or _utcnow()
    payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})

    case = await find_or_create_case(payment_entity, repos, run_id=run_id, now_utc=now_utc)

    # Real, audit-derived attempt history — not the case document's
    # never-incremented `salvage_attempt_count` (spec-guardian D5). Feeds
    # both the diagnosis context (SPEC §5.3 rule 1) and the policy context
    # (POL-005/006/007's `attempt_count: {lte: 3}`, POL-009's `{gte: 4}`) so
    # the two agree on what "attempt count" means, and so POL-009's
    # budget-exhaustion rule can actually match in production.
    charge_attempt_timestamps = await _salvage_charge_attempts_in_window(
        case["_id"], now_utc, repos
    )
    prior_failure_classes = await _prior_failure_classes(case["_id"], repos)

    normalised = classify_payload(
        code=payment_entity.get("error_code"),
        reason=payment_entity.get("error_reason"),
        description=payment_entity.get("error_description"),
    )
    context = build_diagnosis_context(
        case,
        payment_entity.get("error_reason"),
        attempt_timestamps=charge_attempt_timestamps,
        prior_failure_classes=prior_failure_classes,
    )
    diagnosis = diagnose(normalised, context, disable_llm=disable_llm)

    policy_decision = evaluate(
        POLICY_RULES,
        PolicyContext(
            failure_class=diagnosis.failure_class,
            attempt_count=len(charge_attempt_timestamps),  # Salvage-only, never Razorpay's own
        ),
    )

    decision_id = str(uuid.uuid4())
    # Stable across repeated processing of the *same* underlying failed
    # payment — never a fresh decision_id (compliance-auditor F2: a
    # uuid4()-per-call key can never collide, so G10_IDEMPOTENT and the
    # executor's own insert-first DuplicateKeyError guard could never fire
    # from this caller; reprocessing the same payment would double-dispatch).
    # `id` is present on every real payment.failed entity captured so far
    # (NOTES.md's fixture log); order_id/case id are defensive fallbacks for
    # a payload shaped differently than anything seen yet.
    payment_key = (
        payment_entity.get("id") or payment_entity.get("order_id") or f"case-{case['_id']}"
    )
    amount_paise = int(payment_entity.get("amount") or 0)

    def idem_key_for(action: Action) -> str:
        return f"{case['_id']}:{payment_key}:{action.value}"

    async def gate(action: Action) -> ChainResult:
        ctx = await build_gate_context(
            case,
            action,
            now_utc,
            idempotency_key=idem_key_for(action),
            repos=repos,
            amount_paise=amount_paise,
            kill_switch_engaged=kill_switch_engaged,
        )
        return evaluate_chain(ctx)

    async def on_block(proposed_for_this_hop: Action, blocking: tuple[Any, ...]) -> None:
        await _write_block_audit_rows(
            list(blocking),
            case_id=case["_id"],
            decision_id=decision_id,
            proposed_action=proposed_for_this_hop,
            repos=repos,
            dry_run=dry_run,
            now_utc=now_utc,
            run_id=run_id,
        )

    # First pass, then every remediation hop re-gated before it can dispatch
    # (compliance-auditor F1) — shared with api.scheduler.runner.fire_due_jobs()
    # via api.gates.registry.resolve_with_remediation(), see that function's
    # docstring for the full reasoning (spec-guardian D2/G2/G3).
    chain = await gate(policy_decision.action)
    if chain.blocking:
        await on_block(policy_decision.action, chain.blocking)
    remediation = await resolve_with_remediation(
        chain, policy_decision.action, now_utc, gate, on_block=on_block
    )
    final = remediation.final
    dispatchable = remediation.dispatchable
    accumulated_verdicts = list(remediation.verdicts)

    # A charge the gates permit still has a *right time*, and until day 5
    # nothing applied one: policy rules carried `timing: NEXT_PAYDAY_WINDOW`
    # and `HOURS_24` from day 3 and the pipeline ignored them, so every
    # RETRY_SCHEDULED fired the instant it was allowed. That is the baseline's
    # timing, not Salvage's, and CLAUDE.md names this lever as the one not to
    # design away. The day-5 batch is what found it — arm A was beating arm B.
    #
    # This only ever delays, and what it delays is re-gated in full when it
    # fires. It cannot permit anything the chain refused.
    if dispatchable and final.verdict == VerdictKind.ALLOW and final.action in CHARGE_ACTIONS:
        anchor = (
            max(charge_attempt_timestamps)
            if charge_attempt_timestamps
            else _case_opened_at(case, now_utc)
        )
        earliest = earliest_attempt_at(
            timing=policy_decision.timing,
            timing_hint=diagnosis.timing_hint,
            anchor=anchor,
            now_utc=now_utc,
            max_delay_minutes=policy_decision.max_delay_minutes,
        )
        if earliest > now_utc:
            final = dataclasses.replace(final, verdict=VerdictKind.REPLAN, at=earliest)

    decision_doc: dict[str, Any] = {
        # Also written to every audit row for this decision cycle (pre/post/
        # block phases alike) — the join key a Decision Tape renderer needs.
        # spec-guardian D3: an earlier version generated this uuid and used
        # it on every audit row, but never stored it on the decision document
        # itself, so the join was structurally impossible despite comments
        # claiming otherwise.
        "decision_id": decision_id,
        "case_id": case["_id"],
        # The amount this decision's gates were evaluated against, taken from
        # the payload that triggered it. The case document is written once at
        # find-or-create and never updated, so it is NOT a safe source for a
        # later cycle — G3_AFA would be re-evaluated against the first failure's
        # figure. Anything re-gating this decision later (api/scheduler/live.py)
        # reads it from here, and refuses the job if it is absent.
        "amount_paise": amount_paise,
        "run_id": run_id,
        "created_at": now_utc,
        "diagnosis": {
            "failure_class": diagnosis.failure_class.value,
            "confidence": diagnosis.confidence,
            "is_recoverable": diagnosis.is_recoverable,
            "evidence": list(diagnosis.evidence),
            "timing_hint": diagnosis.timing_hint.value if diagnosis.timing_hint else None,
            "source": diagnosis.source,
        },
        "llm_raw": diagnosis.llm_raw,
        "policy": {
            "rule_id": policy_decision.rule_id,
            "proposed_action": policy_decision.action.value,
            "proposed_at": now_utc,
        },
        # Every hop's verdicts, not just the first pass's (spec-guardian D2)
        # — the Decision Tape needs to show *why* the originally proposed
        # action was blocked *and* why the action that actually dispatched
        # was found compliant; recording only the first pass left zero gate
        # verdicts on file for the action that ran.
        "gate_results": [
            {
                "gate": v.gate,
                "verdict": v.kind.value,
                "clause_ref": v.clause_ref,
                "detail": v.detail,
                "evaluated_at": v.evaluated_at,
            }
            for v in accumulated_verdicts
        ],
        # PRD §8.2's decisions.final.verdict is documented lowercase
        # ("allow | block | replan"); VerdictKind's own values are uppercase
        # (matching the gate verdicts' own convention) — lowercased only at
        # this one boundary, to match the documented contract exactly.
        "final": {
            "action": final.action.value,
            "at": final.at,
            "verdict": final.verdict.value.lower(),
        },
        "suppressed": list(final.suppressed),
    }
    await repos.decisions.insert(decision_doc)

    execute_result = None
    if dispatchable and final.verdict != VerdictKind.REPLAN:
        # Same key `gate()` used to build the last-checked GateContext for
        # this action — G10_IDEMPOTENT's "have I seen this key" check and the
        # executor's own insert-first guard must agree on what the key *is*.
        execute_result = await execute_action(
            case_id=case["_id"],
            decision_id=decision_id,
            action=final.action,
            payload={"category": case.get("category"), "mandate_type": case.get("mandate_type")},
            idempotency_key=idem_key_for(final.action),
            dry_run=dry_run,
            audit=repos.audit,
            kill_switch_engaged=kill_switch_engaged,
            run_id=run_id,
            now_utc=now_utc,
        )

    # `execute_result` is attached to the returned dict for the caller's
    # convenience only — it is NOT part of PRD §8.2's `decisions` shape and
    # is not written back to the persisted document (compliance-auditor
    # F11b: the earlier version implied it was, by mutating this dict after
    # the Mongo insert already happened). The durable record of whether
    # `final.action` executed lives in `audit`, joined on `decision_id` —
    # that's what a Decision Tape renderer should query, not this field.
    decision_doc["execute_result"] = execute_result
    return decision_doc
