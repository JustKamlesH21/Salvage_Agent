"""The batch harness produces every number this submission reports.

`audit_compliance` gets the most attention here because it is the basis of the
"zero compliance violations" claim, and a compliance auditor that cannot
detect a violation would report zero for the most reassuring possible reason.
Each test below feeds it a dispatch that *is* a violation and requires it to
say so.
"""

from __future__ import annotations

import datetime as dt

from api.normalise.types import FailureClass
from eval.cohort import CLASS_MIX, generate_cohort
from eval.harness import CaseResult, Dispatch, audit_compliance
from sim.simulator import IST


def ist(day: int, hour: int) -> dt.datetime:
    return dt.datetime(2026, 9, day, hour, 0, tzinfo=IST).astimezone(dt.UTC)


def result(**kwargs: object) -> CaseResult:
    base: dict[str, object] = {
        "case_ref": "c1",
        "true_failure_class": "TEMPORARY_LIQUIDITY",
        "diagnosed_class": "TEMPORARY_LIQUIDITY",
        "amount_paise": 100000,
        "category": "standard",
        "mandate_type": "card",
        "outcome": "recovered",
        "recovered_paise": 100000,
        "charge_attempts": 1,
        "contacts": 0,
        "escalated": False,
        "decision_cycles": 1,
    }
    base.update(kwargs)
    return CaseResult(**base)  # type: ignore[arg-type]


class TestComplianceAuditCanActuallyDetectViolations:
    def test_a_contact_inside_the_window_is_clean(self) -> None:
        clean = result(dispatches=[Dispatch(action="NUDGE", at=ist(10, 12))])
        assert audit_compliance([clean]) == {
            "contacts_outside_0800_1900_ist": 0,
            "cases_over_attempt_budget": 0,
            "dispatches_without_an_allow_verdict": 0,
        }

    def test_a_contact_after_1900_ist_is_caught(self) -> None:
        late = result(dispatches=[Dispatch(action="NUDGE", at=ist(10, 19))])
        assert audit_compliance([late])["contacts_outside_0800_1900_ist"] == 1

    def test_a_contact_before_0800_ist_is_caught(self) -> None:
        early = result(dispatches=[Dispatch(action="SEND_PRE_DEBIT_NOTICE", at=ist(10, 7))])
        assert audit_compliance([early])["contacts_outside_0800_1900_ist"] == 1

    def test_the_window_is_read_in_ist_not_utc(self) -> None:
        # 09:00 IST is 03:30 UTC. Audited in UTC this compliant contact would
        # be reported as a small-hours violation, and the baseline's real
        # violations would be miscounted in the other direction.
        compliant = result(dispatches=[Dispatch(action="NUDGE", at=ist(10, 9))])
        assert compliant.dispatches[0].at.astimezone(dt.UTC).hour == 3
        assert audit_compliance([compliant])["contacts_outside_0800_1900_ist"] == 0

    def test_the_baselines_dunning_email_counts_as_a_contact(self) -> None:
        # Arm A has no gates, so if this action were not in the audited set the
        # baseline would score a perfect compliance record by having no
        # machinery to check.
        blind = result(dispatches=[Dispatch(action="BASELINE_DUNNING_EMAIL", at=ist(10, 2))])
        assert audit_compliance([blind])["contacts_outside_0800_1900_ist"] == 1

    def test_a_charge_is_not_counted_as_a_contact(self) -> None:
        # G1_WINDOW governs contact, not silent auto-debit (PRD §9). A retry at
        # midnight is not a window violation.
        midnight_retry = result(dispatches=[Dispatch(action="RETRY_NOW", at=ist(10, 0))])
        assert audit_compliance([midnight_retry])["contacts_outside_0800_1900_ist"] == 0

    def test_four_attempts_in_fourteen_days_is_within_budget(self) -> None:
        within = result(
            dispatches=[Dispatch(action="RETRY_NOW", at=ist(day, 12)) for day in (1, 4, 8, 12)]
        )
        assert audit_compliance([within])["cases_over_attempt_budget"] == 0

    def test_five_attempts_in_fourteen_days_is_caught(self) -> None:
        over = result(
            dispatches=[Dispatch(action="RETRY_NOW", at=ist(day, 12)) for day in (1, 4, 8, 12, 14)]
        )
        assert audit_compliance([over])["cases_over_attempt_budget"] == 1

    def test_five_attempts_spread_past_the_window_is_within_budget(self) -> None:
        # The budget is four per rolling 14 days, not four ever.
        spread = [Dispatch(action="RETRY_NOW", at=ist(day, 12)) for day in (1, 4, 8, 12)]
        spread.append(Dispatch(action="RETRY_NOW", at=ist(1, 12) + dt.timedelta(days=20)))
        assert audit_compliance([result(dispatches=spread)])["cases_over_attempt_budget"] == 0

    def test_a_dispatch_without_an_allow_verdict_is_surfaced(self) -> None:
        assert (
            audit_compliance([result(verdict_violations=1)])["dispatches_without_an_allow_verdict"]
            == 1
        )


class TestCohort:
    def test_counts_sum_to_the_requested_size(self) -> None:
        for size in (30, 37, 40, 50):
            assert len(generate_cohort(size, seed=1, run_id="t")) == size

    def test_every_class_in_the_mix_is_reachable(self) -> None:
        classes = {c.true_failure_class for c in generate_cohort(50, seed=1, run_id="t")}
        assert classes == set(CLASS_MIX)

    def test_a_seed_reproduces_its_cohort_exactly(self) -> None:
        assert generate_cohort(40, seed=7, run_id="t") == generate_cohort(40, seed=7, run_id="t")

    def test_identifiers_are_namespaced_by_run(self) -> None:
        # Two runs must never collide on a case identity — find_or_create_case
        # matches on subscription_id within a partition.
        a = {c.subscription_id for c in generate_cohort(40, seed=7, run_id="run_a")}
        b = {c.subscription_id for c in generate_cohort(40, seed=7, run_id="run_b")}
        assert a.isdisjoint(b)

    def test_the_cohort_reaches_above_the_afa_threshold(self) -> None:
        # SPEC §5.10 asks for a deliberate slice above ₹15,000 so G3_AFA is
        # exercised rather than merely present.
        cohort = generate_cohort(40, seed=11, run_id="t")
        assert any(c.amount_paise > 15_000_00 for c in cohort)

    def test_the_cohort_reaches_the_enhanced_category_branch(self) -> None:
        cohort = generate_cohort(50, seed=11, run_id="t")
        assert any(c.category in ("insurance", "mf", "cc_bill") for c in cohort)

    def test_amounts_stay_inside_the_stated_bounds(self) -> None:
        for case in generate_cohort(50, seed=3, run_id="t"):
            assert 199_00 <= case.amount_paise <= 95_000_00

    def test_reason_strings_come_from_the_real_taxonomy(self) -> None:
        # The pipeline has to classify these with the real normaliser; an
        # invented reason string would make the cohort measure nothing.
        from api.normalise import classify_payload

        for case in generate_cohort(50, seed=5, run_id="t"):
            classified = classify_payload(
                code=case.error_code, reason=case.error_reason, description=None
            )
            assert classified.failure_class is case.true_failure_class

    def test_customers_carry_a_contact_channel(self) -> None:
        # Without one, G8_POSTDEBIT correctly blocks every charge and the whole
        # batch reports Salvage recovering nothing — which is exactly what the
        # first run did.
        for case in generate_cohort(40, seed=5, run_id="t"):
            document = case.customer_document()
            assert document["contact_hash"]
            assert document["_id"] == case.customer_id

    def test_some_customers_have_opted_out(self) -> None:
        cohort = generate_cohort(50, seed=11, run_id="t")
        assert any(c.opted_out for c in cohort), "G6_OPTOUT would never be exercised"


class TestHardDeclineClassesAreNotSilentlyRecoverable:
    def test_the_cohort_contains_classes_that_can_never_recover(self) -> None:
        cohort = generate_cohort(50, seed=11, run_id="t")
        hard = {
            FailureClass.CREDENTIAL_STALE,
            FailureClass.CREDENTIAL_DEAD,
            FailureClass.MANDATE_INVALID,
        }
        assert {c.true_failure_class for c in cohort} & hard
