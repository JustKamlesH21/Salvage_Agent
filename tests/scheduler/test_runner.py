"""fire_due_jobs(): the literal fire-time gate re-evaluation test — a job
scheduled for a compliant moment (10:00 IST) that fires at a non-compliant
one (19:30 IST) must be blocked (for G1_WINDOW specifically: replanned, not
executed — see tests/gates/test_g1_window.py's module docstring for why G1
itself never returns BLOCK), not executed as if schedule-time compliance
still held. SPEC §5.5, CLAUDE.md invariant 5.
"""

from __future__ import annotations

import datetime as dt

from api.executor.executor import execute_action
from api.gates.registry import FinalAction, VerdictKind, evaluate_chain
from api.gates.types import GateContext
from api.policy.types import Action
from api.scheduler.runner import fire_due_jobs
from api.scheduler.types import ScheduledJob

from tests.gates.conftest import base_context

SCHEDULED_FOR_10_00_IST = dt.datetime(2026, 9, 3, 4, 30, tzinfo=dt.UTC)
FIRES_AT_19_30_IST = dt.datetime(2026, 9, 3, 14, 0, tzinfo=dt.UTC)


async def _context_builder(job: ScheduledJob, now_utc: dt.datetime) -> GateContext:
    """Rebuilt fresh every call — nothing here remembers the 10:00 snapshot.
    A real pipeline would call api.pipeline.build_gate_context() (also
    async, since it reads audit/customer state); this test only needs the
    clock to matter, so it constructs the context directly.
    """
    return base_context(now_utc=now_utc, action=job.action, has_confirmation_channel=True)


class TestFireTimeReEvaluation:
    async def test_job_scheduled_compliant_fires_outside_window_and_is_replanned_not_executed(
        self, repos
    ) -> None:
        job = ScheduledJob(
            case_id="case1",
            decision_id="dec1",
            action=Action.NUDGE,  # a contact action — the one G1 actually governs
            scheduled_for=SCHEDULED_FOR_10_00_IST,
            payload={},
            idempotency_key="case1:dec1:NUDGE",
        )

        async def execute(job: ScheduledJob, final: FinalAction):  # type: ignore[no-untyped-def]
            return await execute_action(
                case_id=job.case_id,
                decision_id=job.decision_id,
                action=final.action,
                payload=job.payload,
                idempotency_key=job.idempotency_key,
                dry_run=True,
                audit=repos.audit,
                kill_switch_engaged=False,
            )

        # Sanity: at schedule time, this job would have been ALLOWed.
        schedule_time_ctx = await _context_builder(job, SCHEDULED_FOR_10_00_IST)
        assert evaluate_chain(schedule_time_ctx).aggregate is VerdictKind.ALLOW

        # Fire at 19:30 IST — the scheduler re-evaluates against reality.
        results = await fire_due_jobs([job], FIRES_AT_19_30_IST, _context_builder, execute)

        assert len(results) == 1
        result = results[0]
        assert result.chain.aggregate is VerdictKind.REPLAN
        assert result.final.verdict is VerdictKind.REPLAN
        assert result.executed is None  # nothing dispatched — rescheduled instead

        # And nothing was written to audit for this job.
        rows = await repos.audit.by_case("case1")
        assert rows == []


class TestRemediationThroughTheScheduler:
    """Spec-guardian G1: the remediation loop (compliance-auditor F1/F10) had
    zero coverage through `fire_due_jobs()` — every existing test's job
    either executed directly or replanned directly, never hit a BLOCK a
    remedy exists for. This fires a `RETRY_NOW` job with no pre-debit notice
    on record — G2_PREDEBIT blocks, the scheduler must re-gate the remedy
    (SEND_PRE_DEBIT_NOTICE) via the same `gate_context_builder`, not dispatch
    it on the first pass's authority.
    """

    async def test_blocked_job_remediates_and_dispatches_the_verified_remedy(self, repos) -> None:  # type: ignore[no-untyped-def]
        job = ScheduledJob(
            case_id="case1",
            decision_id="dec1",
            action=Action.RETRY_NOW,
            scheduled_for=SCHEDULED_FOR_10_00_IST,
            payload={},
            idempotency_key="case1:dec1:RETRY_NOW",
        )

        async def execute(job: ScheduledJob, final: FinalAction):  # type: ignore[no-untyped-def]
            return await execute_action(
                case_id=job.case_id,
                decision_id=job.decision_id,
                action=final.action,
                payload=job.payload,
                idempotency_key=job.idempotency_key,
                dry_run=True,
                audit=repos.audit,
                kill_switch_engaged=False,
            )

        # Sanity: at schedule time this is genuinely blocked (no notice).
        schedule_ctx = await _context_builder(job, SCHEDULED_FOR_10_00_IST)
        assert evaluate_chain(schedule_ctx).aggregate is VerdictKind.BLOCK

        results = await fire_due_jobs([job], SCHEDULED_FOR_10_00_IST, _context_builder, execute)

        assert len(results) == 1
        result = results[0]
        assert result.final.action is Action.SEND_PRE_DEBIT_NOTICE
        assert result.final.verdict is VerdictKind.ALLOW  # the remedy's OWN, re-verified verdict
        assert result.executed is not None
        assert result.executed.outcome == "executed"
        # Both hops' verdicts are on record (spec-guardian D2).
        assert len(result.chain.verdicts) == 22


class TestDueFiltering:
    async def test_jobs_not_yet_due_are_skipped(self, repos) -> None:  # type: ignore[no-untyped-def]
        future_job = ScheduledJob(
            case_id="case1",
            decision_id="dec1",
            action=Action.CLOSE_NO_ACTION,
            scheduled_for=FIRES_AT_19_30_IST + dt.timedelta(days=1),
            payload={},
            idempotency_key="case1:dec1:CLOSE_NO_ACTION",
        )

        async def execute(job: ScheduledJob, final: FinalAction):  # type: ignore[no-untyped-def]
            raise AssertionError("should never be called for a job that isn't due yet")

        results = await fire_due_jobs([future_job], FIRES_AT_19_30_IST, _context_builder, execute)
        assert results == []

    async def test_compliant_job_at_fire_time_executes(self, repos) -> None:  # type: ignore[no-untyped-def]
        job = ScheduledJob(
            case_id="case1",
            decision_id="dec1",
            action=Action.CLOSE_NO_ACTION,
            scheduled_for=SCHEDULED_FOR_10_00_IST,
            payload={},
            idempotency_key="case1:dec1:CLOSE_NO_ACTION",
        )

        async def execute(job: ScheduledJob, final: FinalAction):  # type: ignore[no-untyped-def]
            return await execute_action(
                case_id=job.case_id,
                decision_id=job.decision_id,
                action=final.action,
                payload=job.payload,
                idempotency_key=job.idempotency_key,
                dry_run=True,
                audit=repos.audit,
                kill_switch_engaged=False,
            )

        results = await fire_due_jobs([job], SCHEDULED_FOR_10_00_IST, _context_builder, execute)
        assert len(results) == 1
        assert results[0].executed is not None
        assert results[0].executed.outcome == "executed"
