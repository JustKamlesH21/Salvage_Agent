"""api/policy/timing.py — when a permitted charge is actually worth making.

The property that matters most here is termination. A target computed from
`now` rather than from the case's own history walks forward on every decision
cycle and the attempt never happens — the case waits forever while looking, at
every individual step, like it is doing the right thing. `test_anchoring`
below is the one that would catch that.
"""

from __future__ import annotations

import datetime as dt

import pytest
from api.diagnose.schema import TimingHint
from api.policy.timing import IST, earliest_attempt_at, next_payday_window


def ist(year: int, month: int, day: int, hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime(year, month, day, hour, minute, tzinfo=IST).astimezone(dt.UTC)


class TestNoTiming:
    def test_no_timing_and_no_hint_means_now(self) -> None:
        now = ist(2026, 9, 10, 12)
        assert earliest_attempt_at(timing=None, timing_hint=None, anchor=now, now_utc=now) == now

    def test_an_unrecognised_timing_acts_immediately_rather_than_never(self) -> None:
        # A typo in a rule must fail toward "visible now", not "silently never".
        now = ist(2026, 9, 10, 12)
        assert (
            earliest_attempt_at(timing="NEXT_FULL_MOON", timing_hint=None, anchor=now, now_utc=now)
            == now
        )

    def test_a_target_in_the_past_never_returns_the_past(self) -> None:
        anchor = ist(2026, 9, 1, 12)
        now = ist(2026, 9, 10, 12)
        assert (
            earliest_attempt_at(timing="HOURS_24", timing_hint=None, anchor=anchor, now_utc=now)
            == now
        )


class TestHours24:
    def test_twenty_four_hours_after_the_anchor(self) -> None:
        anchor = ist(2026, 9, 10, 12)
        now = anchor
        assert earliest_attempt_at(
            timing="HOURS_24", timing_hint=None, anchor=anchor, now_utc=now
        ) == ist(2026, 9, 11, 12)


class TestPaydayWindow:
    @pytest.mark.parametrize(
        ("from_ist", "expected_ist"),
        [
            ((2026, 9, 10, 12), (2026, 9, 15, 10)),  # mid-month window is next
            ((2026, 9, 15, 12), (2026, 9, 16, 10)),  # already past 10:00 on the 15th
            ((2026, 9, 16, 12), (2026, 10, 1, 10)),  # roll into next month
            ((2026, 9, 28, 12), (2026, 10, 1, 10)),  # pre-payday waits for month-start
            ((2026, 9, 30, 23), (2026, 10, 1, 10)),  # hours before the window opens
        ],
    )
    def test_finds_the_next_window(
        self, from_ist: tuple[int, int, int, int], expected_ist: tuple[int, int, int, int]
    ) -> None:
        assert next_payday_window(ist(*from_ist)) == ist(*expected_ist)

    def test_the_window_is_computed_in_ist_not_utc(self) -> None:
        # 1 Oct 2026 03:00 IST is 30 Sep 21:30 UTC. Read in UTC, the "next"
        # window would be a day later than the customer's own payday.
        moment = ist(2026, 10, 1, 3)
        assert moment.astimezone(dt.UTC).day == 30
        assert next_payday_window(moment) == ist(2026, 10, 1, 10)

    def test_lands_inside_the_contact_window(self) -> None:
        # G1_WINDOW allows customer contact 08:00—19:00 IST. A retry scheduled
        # at 03:00 would have any remediated contact action immediately
        # replanned, which wastes a cycle for no reason.
        scheduled = next_payday_window(ist(2026, 9, 10, 12)).astimezone(IST)
        assert 8 <= scheduled.hour < 19


class TestHintsOnlyApplyWhenTheRuleIsSilent:
    def test_the_rules_timing_outranks_the_models_hint(self) -> None:
        anchor = ist(2026, 9, 10, 12)
        # The rule says 24 hours; the model suggests waiting for payday.
        # Invariant 2's spirit: the deterministic rule wins.
        assert earliest_attempt_at(
            timing="HOURS_24", timing_hint=TimingHint.PAYDAY, anchor=anchor, now_utc=anchor
        ) == ist(2026, 9, 11, 12)

    def test_the_hint_is_used_when_the_rule_has_none(self) -> None:
        anchor = ist(2026, 9, 10, 12)
        assert earliest_attempt_at(
            timing=None, timing_hint=TimingHint.PAYDAY, anchor=anchor, now_utc=anchor
        ) == ist(2026, 9, 15, 10)

    @pytest.mark.parametrize("hint", [TimingHint.NOW, TimingHint.NEVER, None])
    def test_now_and_never_do_not_delay(self, hint: TimingHint | None) -> None:
        # NEVER is a claim that the case is unrecoverable. That is the policy
        # engine's call, expressed by proposing a terminal action — not
        # something to express by scheduling a charge infinitely far away.
        anchor = ist(2026, 9, 10, 12)
        assert (
            earliest_attempt_at(timing=None, timing_hint=hint, anchor=anchor, now_utc=anchor)
            == anchor
        )


class TestMaxDelay:
    def test_caps_how_far_out_a_target_may_sit(self) -> None:
        # POL-008: retry a gateway blip now, and if something wants to delay
        # it, not by more than 45 minutes.
        now = ist(2026, 9, 10, 12)
        assert earliest_attempt_at(
            timing="HOURS_24",
            timing_hint=None,
            anchor=now,
            now_utc=now,
            max_delay_minutes=45,
        ) == now + dt.timedelta(minutes=45)

    def test_a_cap_does_not_push_an_immediate_attempt_later(self) -> None:
        now = ist(2026, 9, 10, 12)
        assert (
            earliest_attempt_at(
                timing=None, timing_hint=None, anchor=now, now_utc=now, max_delay_minutes=45
            )
            == now
        )


class TestAnchoring:
    def test_a_scheduled_retry_terminates_instead_of_walking_forward(self) -> None:
        """The bug this module exists to not have.

        Anchor on `now` and every cycle recomputes "24 hours from now", so the
        clock never catches up and the charge is never made — while each
        individual decision looks perfectly reasonable. Anchored on the case's
        own history, the target is a fixed point the clock reaches.
        """
        opened = ist(2026, 9, 10, 12)
        now = opened
        for _ in range(5):
            target = earliest_attempt_at(
                timing="HOURS_24", timing_hint=None, anchor=opened, now_utc=now
            )
            if target <= now:
                break
            now = target
        else:
            pytest.fail("a scheduled retry never became due — the target is walking forward")

        assert now == ist(2026, 9, 11, 12)
