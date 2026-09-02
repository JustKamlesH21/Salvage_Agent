from __future__ import annotations

import datetime as dt

from api.gates import g2_predebit
from api.gates.types import VerdictKind
from api.policy.types import Action

from tests.gates.conftest import base_context, replace

NOW = dt.datetime(2026, 9, 3, 4, 30, tzinfo=dt.UTC)


def ctx(**overrides: object):  # type: ignore[no-untyped-def]
    defaults: dict[str, object] = {"action": Action.RETRY_NOW, "now_utc": NOW}
    defaults.update(overrides)
    return base_context(**defaults)


class TestAllow:
    def test_notice_sent_over_24h_ago_allows(self) -> None:
        verdict = g2_predebit.evaluate(ctx(pre_debit_notice_sent_at=NOW - dt.timedelta(hours=25)))
        assert verdict.kind is VerdictKind.ALLOW

    def test_non_charge_action_always_allows(self) -> None:
        verdict = g2_predebit.evaluate(
            ctx(action=Action.CREATE_REAUTH_LINK, pre_debit_notice_sent_at=None)
        )
        assert verdict.kind is VerdictKind.ALLOW


class TestBlock:
    def test_no_notice_at_all_blocks_with_clause(self) -> None:
        verdict = g2_predebit.evaluate(ctx(pre_debit_notice_sent_at=None))
        assert verdict.kind is VerdictKind.BLOCK
        assert verdict.clause_ref == g2_predebit.CLAUSE_REF


class TestReplan:
    def test_notice_sent_under_24h_ago_replans_to_the_24h_mark(self) -> None:
        sent_at = NOW - dt.timedelta(hours=10)
        verdict = g2_predebit.evaluate(ctx(pre_debit_notice_sent_at=sent_at))
        assert verdict.kind is VerdictKind.REPLAN
        assert verdict.replan_at == sent_at + dt.timedelta(hours=24)


class TestBoundaryValues:
    def test_exactly_24h_allows(self) -> None:
        verdict = g2_predebit.evaluate(ctx(pre_debit_notice_sent_at=NOW - dt.timedelta(hours=24)))
        assert verdict.kind is VerdictKind.ALLOW

    def test_24h_minus_one_second_replans(self) -> None:
        sent_at = NOW - dt.timedelta(hours=24) + dt.timedelta(seconds=1)
        verdict = g2_predebit.evaluate(ctx(pre_debit_notice_sent_at=sent_at))
        assert verdict.kind is VerdictKind.REPLAN


class TestFireTimeReEvaluation:
    def test_scheduled_with_stale_notice_blocks_at_fire_time_if_notice_never_actually_landed(
        self,
    ) -> None:
        # The context is rebuilt fresh at fire time from the durable record
        # (audit), not cached from schedule time — a job scheduled assuming a
        # notice would be sent, where the notice never actually landed,
        # blocks when re-evaluated, exactly as a fresh context should show.
        scheduled_snapshot = g2_predebit.evaluate(ctx(pre_debit_notice_sent_at=None))
        fired = g2_predebit.evaluate(
            ctx(now_utc=NOW + dt.timedelta(hours=9), pre_debit_notice_sent_at=None)
        )
        assert scheduled_snapshot.kind is VerdictKind.BLOCK
        assert fired.kind is VerdictKind.BLOCK


class TestMalformedInput:
    def test_notice_in_the_future_blocks(self) -> None:
        verdict = g2_predebit.evaluate(ctx(pre_debit_notice_sent_at=NOW + dt.timedelta(hours=1)))
        assert verdict.kind is VerdictKind.BLOCK
        assert "malformed" in verdict.detail

    def test_naive_notice_timestamp_blocks(self) -> None:
        naive = replace(ctx(), pre_debit_notice_sent_at=dt.datetime(2026, 9, 2, 12, 0))
        verdict = g2_predebit.evaluate(naive)
        assert verdict.kind is VerdictKind.BLOCK
        assert "malformed" in verdict.detail

    def test_naive_now_utc_blocks(self) -> None:
        naive = replace(ctx(), now_utc=dt.datetime(2026, 9, 3, 10, 0))
        verdict = g2_predebit.evaluate(naive)
        assert verdict.kind is VerdictKind.BLOCK
