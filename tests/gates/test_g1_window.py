"""G1_WINDOW never returns BLOCK — the gate-authoring skill's own worked
example says so explicitly ("G1_WINDOW outside 08:00-19:00 IST replans to
the next window open"): the same action becomes lawful purely by waiting, so
REPLAN is the correct verdict, not BLOCK. Test #2 in the skill's template
("Block...") is satisfied here by asserting the gate never blocks — the
regulatory "you can't contact them right now" already has a home in REPLAN.
"""

from __future__ import annotations

import datetime as dt

from api.gates import g1_window
from api.gates.types import VerdictKind
from api.policy.types import Action

from tests.gates.conftest import base_context, replace

IN_WINDOW = dt.datetime(2026, 9, 3, 4, 30, tzinfo=dt.UTC)  # 10:00 IST
BEFORE_WINDOW = dt.datetime(2026, 9, 3, 2, 0, tzinfo=dt.UTC)  # 07:30 IST
AFTER_WINDOW = dt.datetime(2026, 9, 3, 14, 0, tzinfo=dt.UTC)  # 19:30 IST
AT_OPEN = dt.datetime(2026, 9, 3, 2, 30, tzinfo=dt.UTC)  # exactly 08:00 IST
AT_CLOSE = dt.datetime(2026, 9, 3, 13, 30, tzinfo=dt.UTC)  # exactly 19:00 IST


def ctx(**overrides: object):  # type: ignore[no-untyped-def]
    defaults: dict[str, object] = {"action": Action.NUDGE}
    defaults.update(overrides)
    return base_context(**defaults)


class TestAllow:
    def test_within_window_allows(self) -> None:
        verdict = g1_window.evaluate(ctx(now_utc=IN_WINDOW))
        assert verdict.kind is VerdictKind.ALLOW

    def test_non_contact_action_always_allows_regardless_of_hour(self) -> None:
        for action in (Action.RETRY_NOW, Action.RETRY_SCHEDULED, Action.CLOSE_NO_ACTION):
            verdict = g1_window.evaluate(ctx(now_utc=AFTER_WINDOW, action=action))
            assert verdict.kind is VerdictKind.ALLOW, action


class TestBlock:
    def test_g1_never_blocks(self) -> None:
        for moment in (IN_WINDOW, BEFORE_WINDOW, AFTER_WINDOW):
            verdict = g1_window.evaluate(ctx(now_utc=moment))
            assert verdict.kind is not VerdictKind.BLOCK


class TestReplan:
    def test_before_window_replans_to_today_08_00(self) -> None:
        verdict = g1_window.evaluate(ctx(now_utc=BEFORE_WINDOW))
        assert verdict.kind is VerdictKind.REPLAN
        assert verdict.replan_at == dt.datetime(
            2026, 9, 3, 2, 30, tzinfo=dt.UTC
        )  # 08:00 IST same day

    def test_after_window_replans_to_tomorrow_08_00(self) -> None:
        verdict = g1_window.evaluate(ctx(now_utc=AFTER_WINDOW))
        assert verdict.kind is VerdictKind.REPLAN
        assert verdict.replan_at == dt.datetime(
            2026, 9, 4, 2, 30, tzinfo=dt.UTC
        )  # 08:00 IST next day


class TestBoundaryValues:
    def test_exactly_08_00_ist_allows(self) -> None:
        verdict = g1_window.evaluate(ctx(now_utc=AT_OPEN))
        assert verdict.kind is VerdictKind.ALLOW

    def test_one_minute_before_08_00_replans(self) -> None:
        verdict = g1_window.evaluate(ctx(now_utc=AT_OPEN - dt.timedelta(minutes=1)))
        assert verdict.kind is VerdictKind.REPLAN

    def test_exactly_19_00_ist_replans(self) -> None:
        # PRD: "08:00-19:00" — closed interval on the open end, half-open on
        # the close: 19:00 itself is outside the window (matches the skill's
        # boundary-test mandate: "19:00 and 19:01").
        verdict = g1_window.evaluate(ctx(now_utc=AT_CLOSE))
        assert verdict.kind is VerdictKind.REPLAN

    def test_one_minute_before_19_00_allows(self) -> None:
        verdict = g1_window.evaluate(ctx(now_utc=AT_CLOSE - dt.timedelta(minutes=1)))
        assert verdict.kind is VerdictKind.ALLOW


class TestFireTimeReEvaluation:
    def test_scheduled_compliant_fired_outside_window_no_longer_allows(self) -> None:
        # Scheduled-time snapshot: compliant.
        scheduled_snapshot = g1_window.evaluate(ctx(now_utc=IN_WINDOW))
        assert scheduled_snapshot.kind is VerdictKind.ALLOW

        # Same job, re-evaluated fresh at the ACTUAL fire time (19:30 IST) —
        # nothing about the 10:00 answer is cached or reused.
        fired_verdict = g1_window.evaluate(ctx(now_utc=AFTER_WINDOW))
        assert fired_verdict.kind is VerdictKind.REPLAN
        assert fired_verdict.kind is not VerdictKind.ALLOW


class TestMalformedInput:
    def test_naive_datetime_blocks_rather_than_fails_open(self) -> None:
        naive = replace(ctx(), now_utc=dt.datetime(2026, 9, 3, 10, 0))  # no tzinfo
        verdict = g1_window.evaluate(naive)
        assert verdict.kind is VerdictKind.BLOCK
        assert "malformed" in verdict.detail
