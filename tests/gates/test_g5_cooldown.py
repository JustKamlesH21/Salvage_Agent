from __future__ import annotations

import datetime as dt

from api.gates import g5_cooldown
from api.gates.types import VerdictKind
from api.policy.types import Action

from tests.gates.conftest import base_context, replace

NOW = dt.datetime(2026, 9, 3, 4, 30, tzinfo=dt.UTC)


def ctx(**overrides: object):  # type: ignore[no-untyped-def]
    defaults: dict[str, object] = {"action": Action.NUDGE, "now_utc": NOW}
    defaults.update(overrides)
    return base_context(**defaults)


class TestAllow:
    def test_no_prior_contact_allows(self) -> None:
        verdict = g5_cooldown.evaluate(ctx(last_contact_at=None, contacts_in_7d=()))
        assert verdict.kind is VerdictKind.ALLOW

    def test_last_contact_over_48h_ago_and_under_3_in_7d_allows(self) -> None:
        verdict = g5_cooldown.evaluate(
            ctx(
                last_contact_at=NOW - dt.timedelta(hours=49),
                contacts_in_7d=(NOW - dt.timedelta(days=6),),
            )
        )
        assert verdict.kind is VerdictKind.ALLOW

    def test_non_contact_action_always_allows(self) -> None:
        verdict = g5_cooldown.evaluate(
            ctx(action=Action.RETRY_NOW, last_contact_at=NOW, contacts_in_7d=(NOW, NOW, NOW))
        )
        assert verdict.kind is VerdictKind.ALLOW


class TestReplan:
    def test_under_48h_gap_replans_to_the_48h_mark(self) -> None:
        last = NOW - dt.timedelta(hours=10)
        verdict = g5_cooldown.evaluate(ctx(last_contact_at=last, contacts_in_7d=(last,)))
        assert verdict.kind is VerdictKind.REPLAN
        assert verdict.replan_at == last + dt.timedelta(hours=48)

    def test_3_in_7d_replans_to_oldest_plus_7d(self) -> None:
        oldest = NOW - dt.timedelta(days=6)
        contacts = (oldest, NOW - dt.timedelta(days=4), NOW - dt.timedelta(days=3))
        verdict = g5_cooldown.evaluate(ctx(last_contact_at=contacts[-1], contacts_in_7d=contacts))
        assert verdict.kind is VerdictKind.REPLAN
        assert verdict.replan_at == oldest + dt.timedelta(days=7)

    def test_replan_takes_the_later_of_both_constraints(self) -> None:
        # 48h gap clears in 8h; the 7-day count (oldest of 3 in-window
        # contacts) clears in 1 day — the later of the two. REPLAN must wait
        # for the 7-day constraint here, or the 3-per-7-days rule would be
        # violated the moment the 48h gap alone was satisfied.
        last = NOW - dt.timedelta(hours=40)  # 48h gap clears at NOW+8h
        oldest = NOW - dt.timedelta(days=6)  # 7d window clears at NOW+1d — the later one
        contacts = (oldest, NOW - dt.timedelta(days=3), last)
        verdict = g5_cooldown.evaluate(ctx(last_contact_at=last, contacts_in_7d=contacts))
        assert verdict.kind is VerdictKind.REPLAN
        assert verdict.replan_at == oldest + dt.timedelta(days=7)
        assert verdict.replan_at > last + dt.timedelta(hours=48)


class TestBlock:
    def test_g5_never_blocks(self) -> None:
        verdict = g5_cooldown.evaluate(ctx(last_contact_at=NOW, contacts_in_7d=(NOW, NOW, NOW)))
        assert verdict.kind is not VerdictKind.BLOCK


class TestBoundaryValues:
    def test_exactly_48h_allows(self) -> None:
        last = NOW - dt.timedelta(hours=48)
        verdict = g5_cooldown.evaluate(ctx(last_contact_at=last, contacts_in_7d=(last,)))
        assert verdict.kind is VerdictKind.ALLOW

    def test_48h_minus_one_second_replans(self) -> None:
        last = NOW - dt.timedelta(hours=48) + dt.timedelta(seconds=1)
        verdict = g5_cooldown.evaluate(ctx(last_contact_at=last, contacts_in_7d=(last,)))
        assert verdict.kind is VerdictKind.REPLAN

    def test_exactly_2_in_7d_allows(self) -> None:
        contacts = (NOW - dt.timedelta(days=5), NOW - dt.timedelta(days=3))
        verdict = g5_cooldown.evaluate(ctx(last_contact_at=contacts[-1], contacts_in_7d=contacts))
        assert verdict.kind is VerdictKind.ALLOW

    def test_exactly_3_in_7d_replans(self) -> None:
        contacts = (
            NOW - dt.timedelta(days=5),
            NOW - dt.timedelta(days=3),
            NOW - dt.timedelta(days=1),
        )
        verdict = g5_cooldown.evaluate(ctx(last_contact_at=contacts[-1], contacts_in_7d=contacts))
        assert verdict.kind is VerdictKind.REPLAN


class TestFireTimeReEvaluation:
    def test_cooldown_clears_between_schedule_and_fire_time(self) -> None:
        last = NOW - dt.timedelta(hours=47)
        at_schedule = g5_cooldown.evaluate(
            ctx(now_utc=NOW, last_contact_at=last, contacts_in_7d=(last,))
        )
        at_fire = g5_cooldown.evaluate(
            ctx(now_utc=NOW + dt.timedelta(hours=2), last_contact_at=last, contacts_in_7d=(last,))
        )
        assert at_schedule.kind is VerdictKind.REPLAN
        assert at_fire.kind is VerdictKind.ALLOW


class TestMalformedInput:
    def test_naive_last_contact_at_blocks(self) -> None:
        naive = replace(ctx(), last_contact_at=dt.datetime(2026, 9, 2, 0, 0))
        verdict = g5_cooldown.evaluate(naive)
        assert verdict.kind is VerdictKind.BLOCK
        assert "malformed" in verdict.detail

    def test_naive_contact_in_window_blocks(self) -> None:
        naive = replace(ctx(), contacts_in_7d=(dt.datetime(2026, 9, 2, 0, 0),))
        verdict = g5_cooldown.evaluate(naive)
        assert verdict.kind is VerdictKind.BLOCK
