"""webhook -> normalise -> diagnose -> policy -> gates -> executor -> audit,
end to end, against SPEC §9's failure list — the subset reachable at this
phase (duplicate webhook is Phase 2's own event-level dedup, already proven
in tests/ingest/; the pieces this phase adds are covered here).
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
from api.config import settings
from api.db.repositories import Repositories
from api.executor import arm, disarm
from api.executor.executor import execute_action
from api.gates.registry import VerdictKind, evaluate_chain
from api.pipeline import find_or_create_case, process_payment_failed
from api.policy.types import Action
from api.scheduler.runner import fire_due_jobs
from api.scheduler.types import ScheduledJob

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NOW_10_00_IST = dt.datetime(2026, 9, 3, 4, 30, tzinfo=dt.UTC)
NOW_19_30_IST = dt.datetime(2026, 9, 3, 14, 0, tzinfo=dt.UTC)


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class TestFullPipelineOnARealFixture:
    """Fixture 029: a real payment.failed capture, reason=payment_failed ->
    CHANNEL_DOWNTIME -> POL-008 -> RETRY_NOW, then gates route it through the
    remediation map (no pre-debit notice on record yet).

    The remedy (SEND_PRE_DEBIT_NOTICE) is itself re-gated before dispatch
    (compliance-auditor F1) — at 10:00 IST, with no prior contact/opt-out on
    this fresh case, it passes its OWN gate check cleanly, so the persisted
    `final.verdict` is "allow", not "block": the remedy genuinely is
    compliant, not merely substituted in on the original block's authority.
    """

    async def test_fixture_029_produces_a_cited_decision_and_a_verified_remedy(
        self, repos: Repositories
    ) -> None:
        payload = _load("029-payment_failed.raw.json")
        decision = await process_payment_failed(
            payload, repos, now_utc=NOW_10_00_IST, dry_run=True, disable_llm=True
        )

        assert decision["diagnosis"]["failure_class"] == "CHANNEL_DOWNTIME"
        assert decision["policy"]["rule_id"] == "POL-008"
        assert decision["policy"]["proposed_action"] == "RETRY_NOW"
        # 22 = 11 (first pass, against RETRY_NOW) + 11 (remedy re-gate pass,
        # against the chosen SEND_PRE_DEBIT_NOTICE/CREATE_AFA_LINK) —
        # spec-guardian D2: the Decision Tape needs gate verdicts for the
        # action that actually dispatched, not only the one that didn't.
        assert len(decision["gate_results"]) == 22
        assert {g["gate"] for g in decision["gate_results"]} == {
            f"G{n}_{slug}"
            for n, slug in enumerate(
                [
                    "KILL",
                    "WINDOW",
                    "PREDEBIT",
                    "AFA",
                    "BUDGET",
                    "COOLDOWN",
                    "OPTOUT",
                    "THIRDPARTY",
                    "POSTDEBIT",
                    "HALTED",
                    "IDEMPOTENT",
                ]
            )
        }
        assert decision["decision_id"]  # spec-guardian D3: must be present to join against audit
        assert decision["final"]["verdict"] == "allow"  # the remedy's OWN, re-verified verdict
        assert decision["final"]["action"] in {"SEND_PRE_DEBIT_NOTICE", "CREATE_AFA_LINK"}
        assert decision["suppressed"]  # the original RETRY_NOW proposal is still on record
        assert decision["execute_result"] is not None
        assert decision["execute_result"].outcome == "executed"
        assert decision["execute_result"].dry_run is True

        # spec-guardian D3: the join must actually work, not just exist.
        audit_rows = await repos.audit.by_case(decision["case_id"])
        assert all(r["decision_id"] == decision["decision_id"] for r in audit_rows)
        assert any(r["phase"] == "post" for r in audit_rows)

    async def test_the_remedy_itself_gets_rescheduled_when_it_is_not_actually_compliant(
        self, repos: Repositories
    ) -> None:
        # Same fixture, but now with 3 contacts already in the last 7 days —
        # G5_COOLDOWN must REPLAN the SEND_PRE_DEBIT_NOTICE remedy on its own
        # re-gated pass (G5 never BLOCKs, only reschedules), proving the
        # remedy is genuinely re-checked rather than dispatched just because
        # *some* remedy exists for the original block.
        payload = _load("029-payment_failed.raw.json")
        payment_entity = payload["payload"]["payment"]["entity"]
        case = await find_or_create_case(payment_entity, repos)
        for i in range(3):
            await execute_action(
                case_id=case["_id"],
                decision_id=f"prior-contact-{i}",
                action=Action.NUDGE,
                payload={},
                idempotency_key=f"{case['_id']}:prior-{i}:NUDGE",
                dry_run=True,
                audit=repos.audit,
                kill_switch_engaged=False,
            )

        decision = await process_payment_failed(
            payload, repos, now_utc=NOW_10_00_IST, dry_run=True, disable_llm=True
        )
        assert decision["final"]["verdict"] == "replan"
        assert decision["final"]["action"] == "SEND_PRE_DEBIT_NOTICE"
        assert decision["execute_result"] is None  # rescheduled, not dispatched


class TestDuplicateWebhookCaseLevelContinuity:
    """Full duplicate-*delivery* protection is Phase 2's event-level unique
    index (rz_event_id) — proven in tests/ingest/test_webhook_ingest.py — and
    is what stops process_payment_failed from ever being invoked twice for
    one real delivery. This phase's own contribution: reprocessing the same
    underlying case (however it gets triggered) finds the existing case
    rather than opening a duplicate one.
    """

    async def test_same_payment_entity_reuses_the_existing_case(self, repos: Repositories) -> None:
        payload = _load("029-payment_failed.raw.json")
        entity = payload["payload"]["payment"]["entity"]

        first = await find_or_create_case(entity, repos)
        second = await find_or_create_case(entity, repos)

        assert first["_id"] == second["_id"]
        all_cases = await repos.cases.find({})
        assert len(all_cases) == 1


class TestMalformedDiagnoseOutputReachesEscalateHuman:
    """The specific mechanism (repair retry exhausted -> UNKNOWN) is proven
    against a mocked LLM in tests/diagnose/test_repair_and_fallback.py. This
    test proves the OTHER half: once diagnosis is UNKNOWN, it flows correctly
    through policy (POL-002 -> ESCALATE_HUMAN) and the full gate chain to a
    dispatched action — using DISABLE_LLM to reach UNKNOWN deterministically,
    since normaliser-UNKNOWN + LLM-disabled is the same end state as
    normaliser-UNKNOWN + LLM-exhausted for everything downstream of diagnose().
    """

    async def test_unrecognised_reason_escalates_to_a_human_not_a_guess(
        self, repos: Repositories
    ) -> None:
        payload = {
            "payload": {
                "payment": {
                    "entity": {
                        "error_code": "BAD_REQUEST_ERROR",
                        "error_reason": "totally_unrecognised_reason_xyz",
                        "error_description": "not a real Razorpay reason",
                        "invoice_id": "inv_test_unknown",
                        "amount": 500000,
                        "currency": "INR",
                        "method": "card",
                    }
                }
            }
        }
        decision = await process_payment_failed(
            payload, repos, now_utc=NOW_10_00_IST, dry_run=True, disable_llm=True
        )
        assert decision["diagnosis"]["failure_class"] == "UNKNOWN"
        assert decision["diagnosis"]["source"] == "deterministic_override"
        assert decision["policy"]["rule_id"] == "POL-002"
        assert decision["policy"]["proposed_action"] == "ESCALATE_HUMAN"
        assert decision["final"]["action"] == "ESCALATE_HUMAN"
        assert decision["final"]["verdict"] == "allow"
        assert decision["execute_result"].outcome == "executed"


class TestSchedulerDriftEndToEnd:
    """10:00 IST: process a real case, gates route the remediation action
    (SEND_PRE_DEBIT_NOTICE) — compliant to *dispatch* right now since G1 only
    gates contact actions at evaluation time, and 10:00 is within the window.
    Simulate that this got queued instead of dispatched immediately (a
    realistic variant: the scheduler backlog itself is the drift), and fire
    it at 19:30 IST — outside the window. G1 must replan it, not send it.
    """

    async def test_notice_scheduled_at_10_00_drifts_to_19_30_and_g1_blocks_it(
        self, repos: Repositories
    ) -> None:
        payload = _load("029-payment_failed.raw.json")
        decision = await process_payment_failed(
            payload, repos, now_utc=NOW_10_00_IST, dry_run=True, disable_llm=True
        )
        remedy_action = Action(decision["final"]["action"])
        case_id = decision["case_id"]

        job = ScheduledJob(
            case_id=case_id,
            decision_id="drift-test-decision",
            action=remedy_action,
            scheduled_for=NOW_10_00_IST,
            payload={},
            idempotency_key=f"{case_id}:drift-test-decision:{remedy_action.value}",
        )

        payment_amount = payload["payload"]["payment"]["entity"]["amount"]

        async def context_builder(job: ScheduledJob, now_utc: dt.datetime):  # type: ignore[no-untyped-def]
            from api.pipeline import build_gate_context

            case = await repos.cases.get(case_id)
            return await build_gate_context(
                case,
                job.action,
                now_utc,
                idempotency_key=job.idempotency_key,
                repos=repos,
                amount_paise=payment_amount,
                kill_switch_engaged=False,
            )

        async def execute(job: ScheduledJob, final):  # type: ignore[no-untyped-def]
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

        # Sanity: at 10:00 the notice would have been fine on G1's account.
        ctx_at_schedule = await context_builder(job, NOW_10_00_IST)
        assert evaluate_chain(ctx_at_schedule).aggregate is not VerdictKind.BLOCK

        # Fire at 19:30 IST.
        results = await fire_due_jobs([job], NOW_19_30_IST, context_builder, execute)

        assert len(results) == 1
        result = results[0]
        if remedy_action.value == "SEND_PRE_DEBIT_NOTICE":
            # G1 governs contact actions -> replanned, not sent.
            assert result.chain.aggregate is VerdictKind.REPLAN
            assert result.executed is None
        else:
            # remedy chosen was CREATE_AFA_LINK (also a contact action) — same story.
            assert result.chain.aggregate is VerdictKind.REPLAN
            assert result.executed is None


class TestSafetyDefaultsComeFromSettingsNotAHardcodedLiteral:
    """`dry_run`/`kill_switch_engaged` must never default to a bare
    True/False literal in process_payment_failed()'s own signature — that
    default is a bypass by omission the moment a future caller (the live
    webhook route) forgets to plumb settings() through explicitly. Both are
    resolved from settings() internally when not passed.
    """

    async def test_kill_switch_engaged_in_settings_is_honoured_without_the_caller_passing_it(
        self, repos: Repositories, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings.cache_clear()
        monkeypatch.setenv("KILL_SWITCH", "true")
        try:
            payload = _load("029-payment_failed.raw.json")
            decision = await process_payment_failed(
                payload, repos, now_utc=NOW_10_00_IST, disable_llm=True
            )
            assert decision["execute_result"].outcome == "blocked_kill_switch"
        finally:
            settings.cache_clear()

    async def test_dry_run_false_in_settings_without_arming_is_blocked_not_armed(
        self, repos: Repositories, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # DRY_RUN=false alone must not be enough to go live (invariant 8,
        # compliance-auditor F4) — it's honoured (the setting is read, not
        # silently defaulted to True), but execute_action() still refuses
        # without an explicit arm() call.
        settings.cache_clear()
        monkeypatch.setenv("DRY_RUN", "false")
        try:
            payload = _load("029-payment_failed.raw.json")
            decision = await process_payment_failed(
                payload, repos, now_utc=NOW_10_00_IST, disable_llm=True
            )
            assert decision["execute_result"].outcome == "blocked_not_armed"
        finally:
            settings.cache_clear()

    async def test_dry_run_false_in_settings_once_armed_reaches_the_live_dispatch_stub(
        self, repos: Repositories, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Once genuinely armed, DRY_RUN=false reaches the real
        # client.dispatch() stub, which raises NotImplementedError (live
        # dispatch/F20 is out of scope this phase) — that raise, not a
        # silent dry-run, is proof the setting was actually honoured.
        settings.cache_clear()
        monkeypatch.setenv("DRY_RUN", "false")
        monkeypatch.setenv("ARM_CONFIRM_TOKEN", "test-token")
        try:
            settings.cache_clear()
            assert arm("test-token") is True
            payload = _load("029-payment_failed.raw.json")
            with pytest.raises(NotImplementedError):
                await process_payment_failed(
                    payload, repos, now_utc=NOW_10_00_IST, disable_llm=True
                )
        finally:
            disarm()
            settings.cache_clear()
