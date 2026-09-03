"""The live tick: what turns `fire_due_jobs()` from a tested function into an
agent that acts on its own.

Until now nothing outside the tests and the batch harness called it, so a case
that replanned to 08:00 tomorrow sat at 08:00 tomorrow forever. The compliance
story this product tells — *gates are re-evaluated when the action fires, not
when it was scheduled* — was true of the code and unobservable in the running
system.

Four decisions worth stating, because each had a plausible alternative:

**Scheduled work is not a collection.** A job is the latest decision on a case
whose `final.verdict` is `replan` and whose `final.at` has passed. A `jobs`
collection would be a second record of what is pending, and the two would
disagree the first time a write failed between them. The decision document is
already the authoritative statement that an action is owed at a time; reading
it back is cheaper than keeping a copy honest.

**Only the latest decision per case is eligible.** Firing writes a new decision,
which becomes the latest, which is how a fired job stops being due. Without
this the same replan would re-fire on every tick.

**The kill switch freezes the tick; it does not run it.** Letting the chain run
under a kill would work — G0_KILL blocks first, always — but a BLOCK is
terminal, so the scheduled action would be *cancelled* rather than *held*, and
releasing the switch would not bring it back. CLAUDE.md's wording is "freezes
all pending and scheduled actions", so the tick returns before it reads
anything.

**Off unless asked.** `SCHEDULER_TICK_SECONDS` defaults to 0 and no loop
starts. A background process that dispatches money actions should not begin
because someone ran the server, for the same reason `DRY_RUN` defaults true.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
from typing import Any

from api.config import settings
from api.db.repositories import Repositories
from api.executor import execute_action
from api.executor.killswitch import engaged as killswitch_engaged
from api.executor.types import ActionResult
from api.gates.registry import FinalAction
from api.gates.types import GateContext, VerdictKind
from api.pipeline import _write_block_audit_rows, build_gate_context
from api.policy.types import Action
from api.scheduler.runner import fire_due_jobs
from api.scheduler.types import FireResult, ScheduledJob

log = logging.getLogger(__name__)

# The states in which a case can still be owed an action. Anything else has
# settled and must not be charged or contacted again.
_LIVE_CASE_STATES = frozenset({"open", "recovering"})


def _job_from_decision(decision: dict[str, Any]) -> ScheduledJob | None:
    """A replan decision, read back as the job it represents.

    The idempotency key is keyed on the decision that scheduled the work, so
    firing the same replan twice presents the same key twice — which
    G10_IDEMPOTENT refuses and the executor's unique index refuses again. The
    key deliberately does not include a timestamp: two different attempts at
    the same scheduled action must collide, not slip past each other.
    """
    final = decision.get("final") or {}
    if final.get("verdict") != "replan":
        return None
    try:
        action = Action(final["action"])
    except (KeyError, ValueError):
        return None
    at = final.get("at")
    if not isinstance(at, dt.datetime):
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=dt.UTC)

    case_id = str(decision["case_id"])
    decision_id = str(decision["decision_id"])
    return ScheduledJob(
        case_id=case_id,
        decision_id=decision_id,
        action=action,
        scheduled_for=at,
        payload={},
        idempotency_key=f"{case_id}:sched:{decision_id}:{action.value}",
    )


async def _due(
    repos: Repositories, now_utc: dt.datetime, *, run_id: Any
) -> list[tuple[ScheduledJob, dict[str, Any]]]:
    """Due jobs paired with the decision each came from.

    The decision is carried along because `ScheduledJob.case_id` is a string —
    an identity for keys and display — while the database wants the case id in
    the type it was written with, which for live traffic is an ObjectId. Every
    read and every write below uses `decision["case_id"]` verbatim, never the
    stringified copy. Getting this wrong does not merely fail to find the case:
    it writes audit rows under a key the gates do not read back, so a dispatched
    attempt becomes invisible to G4_BUDGET and G5_COOLDOWN.
    """
    # Only decisions that could possibly be due. The latest-per-case rule still
    # has to be applied over ALL of a case's decisions, not just the replans —
    # a case whose replan was superseded by an ALLOW must not fire — so the
    # narrowing is done in two steps rather than one query.
    candidates = await repos.decisions.find(
        {"run_id": run_id, "final.verdict": "replan", "final.at": {"$lte": now_utc}},
        sort=[("created_at", 1)],
    )
    if not candidates:
        return []

    due: list[tuple[ScheduledJob, dict[str, Any]]] = []
    for candidate in candidates:
        case_id = candidate["case_id"]
        latest = await repos.decisions.find(
            {"case_id": case_id}, sort=[("created_at", -1)], limit=1
        )
        if not latest or latest[0].get("decision_id") != candidate.get("decision_id"):
            continue  # superseded — this is what stops a fired job re-firing
        case = await repos.cases.get(case_id)
        if case is None:
            continue
        # A case that already settled is owed nothing. No gate covers "already
        # paid": G9_HALTED reads subscription_status off the case document,
        # which is written once at find-or-create and never updated, and Salvage
        # only ever hears about failures (FR-28, LIMITATIONS.md). Without this
        # the tick would charge an invoice Razorpay's own retries already
        # recovered. (compliance-auditor F6.)
        if str(case.get("state") or "open") not in _LIVE_CASE_STATES:
            continue
        job = _job_from_decision(candidate)
        if job is not None:
            due.append((job, candidate))
    return due


async def due_jobs(
    repos: Repositories, now_utc: dt.datetime, *, run_id: Any = None
) -> list[ScheduledJob]:
    """Every case whose most recent decision owes an action that is now due."""
    return [job for job, _ in await _due(repos, now_utc, run_id=run_id)]


async def tick(
    repos: Repositories, now_utc: dt.datetime | None = None, *, run_id: Any = None
) -> list[FireResult]:
    """One pass. Returns what fired, for the caller to log or a test to assert.

    Every job gets its GateContext rebuilt from the database at `now_utc` —
    the same `build_gate_context()` the pipeline uses, not a cached copy — so
    a job scheduled inside the contact window that comes due outside it is
    refused at fire time by the gate that governs it.
    """
    now = now_utc or dt.datetime.now(dt.UTC)

    # Freeze, before reading anything. See the module docstring.
    if killswitch_engaged():
        return []

    pairs = await _due(repos, now, run_id=run_id)
    if not pairs:
        return []

    cfg = settings()
    dry_run = cfg.dry_run

    # Cases are fetched once per tick and reused across the builder calls for
    # that job. The gate-relevant history (audit rows, opt-out, channels) is
    # NOT cached — build_gate_context re-reads it every call, which is the
    # property fire-time re-evaluation depends on.
    cfg_dry_run = dry_run
    results: list[FireResult] = []

    for job, decision in pairs:
        case = await repos.cases.get(decision["case_id"])
        if case is None:
            continue

        # F5: the amount the original decision was gated against, from the
        # decision record. The case document holds only the FIRST failure's
        # figure, so re-gating from it would cite a threshold comparison against
        # a number that is not the one being charged. No amount, no job.
        amount_paise = decision.get("amount_paise")
        if not isinstance(amount_paise, int):
            log.warning(
                "scheduler: %s carries no amount_paise; refusing to re-gate on the "
                "case document's figure",
                decision.get("decision_id"),
            )
            continue

        async def context_builder(
            j: ScheduledJob, at: dt.datetime, _case: dict[str, Any] = case, _amt: int = amount_paise
        ) -> GateContext:
            return await build_gate_context(
                _case,
                j.action,
                at,
                idempotency_key=j.idempotency_key,
                repos=repos,
                amount_paise=_amt,
                kill_switch_engaged=killswitch_engaged(),
            )

        async def execute(
            j: ScheduledJob, final: FinalAction, _case: dict[str, Any] = case
        ) -> ActionResult:
            return await execute_action(
                # The case document's own id, in its own type — the audit rows
                # this writes are read back by build_gate_context() keyed on it.
                case_id=_case["_id"],
                decision_id=j.decision_id,
                action=final.action,
                payload={
                    "category": _case.get("category"),
                    "mandate_type": _case.get("mandate_type"),
                },
                idempotency_key=j.idempotency_key,
                dry_run=cfg_dry_run,
                audit=repos.audit,
                kill_switch_engaged=killswitch_engaged(),
                actor="scheduler",
                run_id=run_id,
                now_utc=now,
            )

        # One job at a time. A job that raises must not discard the results of
        # jobs that already dispatched — without this, a poison job starves
        # every later one and leaves a dispatch with no decision recording it.
        # (compliance-auditor F8.)
        try:
            fired = await fire_due_jobs([job], now, context_builder, execute)
        except Exception:
            log.exception("scheduler: job %s failed", job.idempotency_key)
            await repos.exceptions.insert(
                {
                    "case_id": case["_id"],
                    "reason_code": "SCHEDULER_JOB_FAILED",
                    "detail": f"{job.action.value} scheduled for {job.scheduled_for.isoformat()}",
                    "ts": now,
                    "resolved_by": None,
                }
            )
            continue

        for result in fired:
            # F2: the switch may have been engaged after the tick's opening
            # check — these are awaits. Recording now would persist a terminal
            # BLOCK as the case's latest decision, which cancels the scheduled
            # action rather than freezing it, and releasing the switch would not
            # bring it back. Leave the replan standing instead.
            if killswitch_engaged():
                log.info("scheduler: kill engaged mid-tick; leaving %s owed", job.idempotency_key)
                return results
            await _record(repos, result, case, decision, now, run_id=run_id, dry_run=cfg_dry_run)
            results.append(result)

    return results


async def _record(
    repos: Repositories,
    result: FireResult,
    case: dict[str, Any],
    source: dict[str, Any],
    now_utc: dt.datetime,
    *,
    run_id: Any,
    dry_run: bool,
) -> None:
    """The fire is its own decision, and gets its own document.

    Writing it as a decision rather than a log line is what puts the fire-time
    re-evaluation on the Decision Tape — the eleven verdicts that ran at 08:00
    are as much a part of the record as the eleven that ran at 19:40, and the
    tape is how anyone sees that they were different.

    `diagnosis` and `policy` are carried forward from the decision that
    scheduled the work, verbatim. An earlier version invented them — a
    `failure_class` of "SCHEDULED", which is not a member of the enum
    (`FailureClass`, api/normalise/types.py) and would have raised on the next
    webhook for the case, and a `rule_id` of "SCHEDULER", which resolves to
    nothing in the rulebook the tape looks citations up in. Neither was true:
    the diagnosis has not changed and the rule that chose this action is still
    the rule that chose it. What changed is the gate verdicts, which is exactly
    what this document exists to record. (compliance-auditor F4, F9.)
    """
    final = result.final

    # SPEC §5.5: every BLOCK writes an audit row carrying its clause. Without
    # this, a refusal at fire time — the refusals this whole feature exists to
    # demonstrate — is invisible to the deck's `blocked_by_compliance` counter,
    # which counts phase="block" rows. (compliance-auditor F3.)
    blocking = [v for v in result.chain.verdicts if v.kind == VerdictKind.BLOCK]
    decision_id = f"{result.job.decision_id}:fired:{int(now_utc.timestamp())}"
    if blocking:
        await _write_block_audit_rows(
            blocking,
            case_id=case["_id"],
            decision_id=decision_id,
            proposed_action=result.job.action,
            repos=repos,
            dry_run=dry_run,
            now_utc=now_utc,
            run_id=run_id,
        )

    await repos.decisions.insert(
        {
            "decision_id": decision_id,
            # Same type the pipeline writes, so /cases/{id} and by_case() find it.
            "case_id": case["_id"],
            "run_id": run_id,
            "created_at": now_utc,
            "fired_from": result.job.decision_id,
            "scheduled_for": result.job.scheduled_for,
            "amount_paise": source.get("amount_paise"),
            "diagnosis": source.get("diagnosis", {}),
            "llm_raw": None,
            "policy": source.get("policy", {}),
            "gate_results": [
                {
                    "gate": v.gate,
                    "verdict": v.kind.value,
                    "clause_ref": v.clause_ref,
                    "detail": v.detail,
                    "evaluated_at": v.evaluated_at,
                }
                for v in result.chain.verdicts
            ],
            "final": {
                "action": final.action.value,
                "at": final.at,
                "verdict": final.verdict.value.lower(),
            },
            "suppressed": list(final.suppressed),
        }
    )


async def run_forever(repos: Repositories, interval_seconds: int) -> None:
    """The loop. Cancelled on shutdown by the lifespan that started it."""
    log.info("scheduler: ticking every %ss", interval_seconds)
    while True:
        try:
            fired = await tick(repos)
            if fired:
                log.info("scheduler: fired %s job(s)", len(fired))
        except asyncio.CancelledError:
            raise
        except Exception:
            # One bad tick must not end the loop — the next one rebuilds
            # everything from the database anyway.
            log.exception("scheduler: tick failed")
        await asyncio.sleep(interval_seconds)


@contextlib.asynccontextmanager
async def scheduler(repos: Repositories, interval_seconds: int) -> Any:
    """Start the loop for the life of the app, or do nothing if it is off."""
    if interval_seconds <= 0:
        log.info("scheduler: disabled (SCHEDULER_TICK_SECONDS=0)")
        yield None
        return
    task = asyncio.create_task(run_forever(repos, interval_seconds))
    try:
        yield task
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
