"""fire_due_jobs() — the fire-time gate re-evaluation SPEC §5.5 and CLAUDE.md
invariant 5 require. Pure and fully tested; a live APScheduler background
thread calling this on a tick is deferred (no payoff this compressed cycle —
no live demo mandate exists yet to schedule against). See the approved plan
for this phase.

The load-bearing property: `gate_context_builder` is called fresh for every
job, with the *current* `now_utc` — nothing about the gate context is
computed once at schedule time and reused. A job scheduled at a compliant
moment that fires at a non-compliant one is re-evaluated against reality,
not against the world as it looked when it was queued.

Remediation substitutes are re-gated before dispatch, same as
api.pipeline.process_payment_failed() — compliance-auditor F1/F10: a remedy
action (e.g. G2_PREDEBIT's BLOCK converting to SEND_PRE_DEBIT_NOTICE) is
itself compliance-relevant and was never checked against the chain on its
own account. `job.idempotency_key` is trusted only when the dispatched
action still matches `job.action`; a remedy gets its own key, keyed on the
action actually being sent.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Awaitable, Callable

from api.executor.types import ActionResult
from api.gates.registry import (
    ChainResult,
    FinalAction,
    evaluate_chain,
    resolve_with_remediation,
)
from api.gates.types import GateContext, VerdictKind
from api.policy.types import Action
from api.scheduler.types import FireResult, ScheduledJob

# `build_gate_context()` (api/pipeline.py) does DB I/O (audit history,
# customer opt-out/channel lookup) to build a context that reflects the
# current world, so the builder here must be async too — a synchronous
# signature would force the caller to pre-fetch and cache exactly the state
# fire-time re-evaluation exists to *not* cache.
GateContextBuilder = Callable[[ScheduledJob, dt.datetime], Awaitable[GateContext]]
Executor = Callable[[ScheduledJob, FinalAction], Awaitable[ActionResult]]


async def fire_due_jobs(
    jobs: list[ScheduledJob],
    now_utc: dt.datetime,
    gate_context_builder: GateContextBuilder,
    execute: Executor,
) -> list[FireResult]:
    due = [job for job in jobs if job.scheduled_for <= now_utc]
    results: list[FireResult] = []
    for job in due:
        first_ctx = await gate_context_builder(job, now_utc)  # rebuilt fresh — invariant 5
        first_chain: ChainResult = evaluate_chain(first_ctx)

        # `dispatch_job` tracks whichever job (original or remedy) was most
        # recently gated, so `execute()` below always dispatches with the
        # idempotency key that matches what was actually verified — the
        # remediation loop itself (api.gates.registry.resolve_with_remediation,
        # shared with api.pipeline.process_payment_failed) re-gates every
        # substitute action before it can dispatch (compliance-auditor F1)
        # and never dispatches on hop-bound exhaustion (spec-guardian G2/G3).
        dispatch_job = job

        async def gate_fn(action: Action, _job: ScheduledJob = job) -> ChainResult:
            nonlocal dispatch_job
            # The builder derives its GateContext.action from job.action, so
            # a remedy must be gated via a job that actually carries it — not
            # the original job, and not a bare action re-check.
            # The key carries the occasion, not just the case and the action.
            # Without `decision_id` two scheduled jobs on the same case months
            # apart that both need CREATE_AFA_LINK present the SAME key: the
            # second is refused by G10_IDEMPOTENT, G10 has no remediation, and
            # the loop settles on CLOSE_NO_ACTION — a recoverable case closed
            # because a remedy of that kind was issued once, ever.
            # (compliance-auditor F7, day 6.) Still stable across retries of
            # the same occasion, which is what idempotency actually needs.
            remedy_job = dataclasses.replace(
                _job,
                action=action,
                idempotency_key=f"{_job.case_id}:{_job.decision_id}:remedy:{action.value}",
            )
            dispatch_job = remedy_job
            remedy_ctx = await gate_context_builder(remedy_job, now_utc)
            return evaluate_chain(remedy_ctx)

        remediation = await resolve_with_remediation(first_chain, job.action, now_utc, gate_fn)
        final = remediation.final
        # `chain.verdicts` now covers every hop; `chain.aggregate`/`.replan_at`
        # still reflect the FIRST pass only (not recomputed from the combined
        # verdicts) — `final` is the authoritative outcome, this is the trail.
        chain = dataclasses.replace(first_chain, verdicts=remediation.verdicts)

        if not remediation.dispatchable or final.verdict == VerdictKind.REPLAN:
            results.append(FireResult(job=job, chain=chain, final=final, executed=None))
            continue

        executed = await execute(dispatch_job, final)
        results.append(FireResult(job=job, chain=chain, final=final, executed=executed))
    return results
