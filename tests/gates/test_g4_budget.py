from __future__ import annotations

import datetime as dt

from api.gates import g4_budget
from api.gates.types import VerdictKind
from api.policy.types import Action

from tests.gates.conftest import base_context, replace

NOW = dt.datetime(2026, 9, 3, 4, 30, tzinfo=dt.UTC)


def ctx(**overrides: object):  # type: ignore[no-untyped-def]
    defaults: dict[str, object] = {"action": Action.RETRY_SCHEDULED, "now_utc": NOW}
    defaults.update(overrides)
    return base_context(**defaults)


def attempts_ago(*days: float) -> tuple[dt.datetime, ...]:
    return tuple(NOW - dt.timedelta(days=d) for d in days)


class TestAllow:
    def test_under_budget_allows(self) -> None:
        verdict = g4_budget.evaluate(ctx(salvage_attempts_in_window=attempts_ago(1, 2, 3)))
        assert verdict.kind is VerdictKind.ALLOW

    def test_non_charge_action_never_consumes_budget(self) -> None:
        verdict = g4_budget.evaluate(
            ctx(action=Action.NUDGE, salvage_attempts_in_window=attempts_ago(1, 2, 3, 4, 5))
        )
        assert verdict.kind is VerdictKind.ALLOW


class TestReplan:
    def test_budget_exhausted_replans_to_oldest_plus_14_days(self) -> None:
        oldest = NOW - dt.timedelta(days=13)
        verdict = g4_budget.evaluate(
            ctx(
                salvage_attempts_in_window=(
                    oldest,
                    NOW - dt.timedelta(days=5),
                    NOW - dt.timedelta(days=1),
                    NOW,
                )
            )
        )
        assert verdict.kind is VerdictKind.REPLAN
        assert verdict.replan_at == oldest + dt.timedelta(days=14)


class TestBlock:
    def test_g4_never_blocks_it_always_replans_once_something_ages_out(self) -> None:
        # Any exhausted-budget case eventually clears by waiting, so there is
        # no permanent-BLOCK path — REPLAN carries this gate's "no" the way
        # G1/G2/G5 do.
        verdict = g4_budget.evaluate(ctx(salvage_attempts_in_window=attempts_ago(1, 2, 3, 4)))
        assert verdict.kind is not VerdictKind.BLOCK


class TestBoundaryValues:
    def test_exactly_3_attempts_allows(self) -> None:
        verdict = g4_budget.evaluate(ctx(salvage_attempts_in_window=attempts_ago(1, 2, 3)))
        assert verdict.kind is VerdictKind.ALLOW

    def test_exactly_4_attempts_replans(self) -> None:
        verdict = g4_budget.evaluate(ctx(salvage_attempts_in_window=attempts_ago(1, 2, 3, 4)))
        assert verdict.kind is VerdictKind.REPLAN

    def test_attempt_exactly_14_days_old_is_outside_the_window(self) -> None:
        exactly_14 = NOW - dt.timedelta(days=14)
        verdict = g4_budget.evaluate(
            ctx(salvage_attempts_in_window=(exactly_14, *attempts_ago(1, 2, 3)))
        )
        assert verdict.kind is VerdictKind.ALLOW  # only 3 count; the 14-day-old one has aged out

    def test_attempt_just_under_14_days_old_still_counts(self) -> None:
        just_under = NOW - dt.timedelta(days=13, hours=23, minutes=59)
        verdict = g4_budget.evaluate(
            ctx(salvage_attempts_in_window=(just_under, *attempts_ago(1, 2, 3)))
        )
        assert verdict.kind is VerdictKind.REPLAN  # 4 count


class TestFireTimeReEvaluation:
    def test_budget_frees_up_between_schedule_and_fire_time(self) -> None:
        oldest = NOW - dt.timedelta(days=13, hours=23)
        at_schedule = g4_budget.evaluate(
            ctx(now_utc=NOW, salvage_attempts_in_window=(oldest, *attempts_ago(1, 2, 3)))
        )
        # Two hours later, the oldest attempt has aged past 14 days.
        later = NOW + dt.timedelta(hours=2)
        at_fire = g4_budget.evaluate(
            ctx(now_utc=later, salvage_attempts_in_window=(oldest, *attempts_ago(1, 2, 3)))
        )
        assert at_schedule.kind is VerdictKind.REPLAN
        assert at_fire.kind is VerdictKind.ALLOW


class TestMalformedInput:
    def test_naive_attempt_timestamp_blocks(self) -> None:
        naive = replace(ctx(), salvage_attempts_in_window=(dt.datetime(2026, 9, 1, 0, 0),))
        verdict = g4_budget.evaluate(naive)
        assert verdict.kind is VerdictKind.BLOCK
        assert "malformed" in verdict.detail
