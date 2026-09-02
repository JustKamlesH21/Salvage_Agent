"""The simulator decides whether money moved in every batch number this
submission reports. If it is wrong, every number downstream is wrong in a way
no amount of care in the harness can recover.

Two properties matter more than the rest and are tested first: that hard
declines can never recover on a retry (a simulator that lets them would make
Salvage's whole hard-decline story free), and that the day-of-month and hour
modifiers are evaluated in IST rather than the server's UTC (the timezone bug
CLAUDE.md predicts for exactly this kind of code).
"""

from __future__ import annotations

import datetime as dt
import random
from pathlib import Path

import pytest
import yaml
from api.normalise.types import FailureClass
from sim.simulator import (
    CALIBRATION_PATH,
    IST,
    Calibration,
    sample_success,
    success_probability,
)


@pytest.fixture(scope="module")
def cal() -> Calibration:
    return Calibration.load()


def at_ist(year: int, month: int, day: int, hour: int, minute: int = 0) -> dt.datetime:
    """A moment expressed in IST, handed over as the UTC the caller would
    really pass. Tests read in the timezone the rules are written in.
    """
    return dt.datetime(year, month, day, hour, minute, tzinfo=IST).astimezone(dt.UTC)


# A neutral reference moment: mid-month but not a payday day, a weekday,
# daytime IST. Every modifier is off, so a probability equals the base rate.
NEUTRAL = at_ist(2026, 9, 10, 12)  # Thursday 10 Sep 2026, 12:00 IST


class TestHardDeclinesNeverRecoverOnRetry:
    @pytest.mark.parametrize(
        "failure_class",
        [FailureClass.CREDENTIAL_STALE, FailureClass.CREDENTIAL_DEAD, FailureClass.MANDATE_INVALID],
    )
    def test_probability_is_zero_regardless_of_timing_or_attempt(
        self, cal: Calibration, failure_class: FailureClass
    ) -> None:
        for attempt_no in (1, 2, 3, 9):
            for moment in (NEUTRAL, at_ist(2026, 9, 1, 10), at_ist(2026, 9, 28, 23)):
                assert success_probability(cal, failure_class, attempt_no, moment) == 0.0

    def test_no_seed_ever_produces_a_success(self, cal: Calibration) -> None:
        # A zero probability that still succeeds once in 10,000 draws would be
        # a sampler bug, not a rounding detail.
        for seed in range(200):
            rng = random.Random(seed)
            for _ in range(50):
                assert not sample_success(cal, FailureClass.CREDENTIAL_DEAD, 1, NEUTRAL, rng)


class TestTimezone:
    def test_day_of_month_is_read_in_ist_not_utc(self, cal: Calibration) -> None:
        # 30 Sep 2026, 02:00 IST is 29 Sep 2026, 20:30 UTC. Both fall in the
        # pre-payday window, so pick the boundary that actually separates them:
        # 1 Oct 2026, 03:00 IST is 30 Sep 2026, 21:30 UTC — payday in IST,
        # pre-payday in UTC. Reading this in UTC applies a 0.72 penalty to a
        # charge the customer experiences on payday.
        moment = at_ist(2026, 10, 1, 3)
        assert moment.astimezone(dt.UTC).day == 30  # the trap this test exists for

        ist_probability = success_probability(cal, FailureClass.TEMPORARY_LIQUIDITY, 1, moment)
        base = cal.base_rates[FailureClass.TEMPORARY_LIQUIDITY]
        # Payday (1.0) and night (03:00 IST) — no pre-payday penalty.
        assert ist_probability == pytest.approx(base * cal.night_modifier)

    def test_hour_is_read_in_ist_not_utc(self, cal: Calibration) -> None:
        # 09:00 IST is 03:30 UTC — daytime in IST, deep night in UTC.
        daytime_ist = at_ist(2026, 9, 10, 9)
        base = cal.base_rates[FailureClass.TEMPORARY_LIQUIDITY]
        assert success_probability(
            cal, FailureClass.TEMPORARY_LIQUIDITY, 1, daytime_ist
        ) == pytest.approx(base)

    def test_a_naive_datetime_is_rejected_rather_than_assumed(self, cal: Calibration) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            success_probability(
                cal, FailureClass.TEMPORARY_LIQUIDITY, 1, dt.datetime(2026, 9, 10, 12)
            )


class TestModifiers:
    def test_neutral_moment_returns_the_bare_base_rate(self, cal: Calibration) -> None:
        for failure_class, base in cal.base_rates.items():
            if failure_class in cal.hard_decline_classes:
                continue
            assert success_probability(cal, failure_class, 1, NEUTRAL) == pytest.approx(base)

    def test_pre_payday_lowers_the_probability(self, cal: Calibration) -> None:
        pre_payday = at_ist(2026, 9, 28, 12)  # Monday 28 Sep, daytime
        base = cal.base_rates[FailureClass.TEMPORARY_LIQUIDITY]
        assert success_probability(
            cal, FailureClass.TEMPORARY_LIQUIDITY, 1, pre_payday
        ) == pytest.approx(base * cal.pre_payday_modifier)

    def test_payday_carries_no_penalty_and_no_bonus(self, cal: Calibration) -> None:
        payday = at_ist(2026, 9, 15, 12)  # Tuesday 15 Sep, daytime
        base = cal.base_rates[FailureClass.TEMPORARY_LIQUIDITY]
        assert success_probability(
            cal, FailureClass.TEMPORARY_LIQUIDITY, 1, payday
        ) == pytest.approx(base)

    def test_night_window_wraps_midnight(self, cal: Calibration) -> None:
        # 23:00 and 02:00 IST are both night; 12:00 is not. A range test
        # written as `19 <= hour < 8` would call 02:00 daytime.
        base = cal.base_rates[FailureClass.TEMPORARY_LIQUIDITY]
        for hour in (19, 23, 0, 2, 7):
            moment = at_ist(2026, 9, 10, hour)
            assert success_probability(
                cal, FailureClass.TEMPORARY_LIQUIDITY, 1, moment
            ) == pytest.approx(base * cal.night_modifier)
        for hour in (8, 12, 18):
            moment = at_ist(2026, 9, 10, hour)
            assert success_probability(
                cal, FailureClass.TEMPORARY_LIQUIDITY, 1, moment
            ) == pytest.approx(base)

    def test_weekend_lowers_the_probability(self, cal: Calibration) -> None:
        saturday = at_ist(2026, 9, 12, 12)
        assert saturday.astimezone(IST).weekday() == 5
        base = cal.base_rates[FailureClass.TEMPORARY_LIQUIDITY]
        assert success_probability(
            cal, FailureClass.TEMPORARY_LIQUIDITY, 1, saturday
        ) == pytest.approx(base * cal.weekend_modifier)

    def test_attempt_decay_starts_at_the_third_attempt_and_compounds(
        self, cal: Calibration
    ) -> None:
        base = cal.base_rates[FailureClass.TEMPORARY_LIQUIDITY]
        first = success_probability(cal, FailureClass.TEMPORARY_LIQUIDITY, 1, NEUTRAL)
        second = success_probability(cal, FailureClass.TEMPORARY_LIQUIDITY, 2, NEUTRAL)
        third = success_probability(cal, FailureClass.TEMPORARY_LIQUIDITY, 3, NEUTRAL)
        fourth = success_probability(cal, FailureClass.TEMPORARY_LIQUIDITY, 4, NEUTRAL)

        assert first == second == pytest.approx(base)
        assert third == pytest.approx(base * cal.attempt_decay)
        assert fourth == pytest.approx(base * cal.attempt_decay**2)
        assert fourth < third < second

    def test_attempt_numbers_are_one_based(self, cal: Calibration) -> None:
        with pytest.raises(ValueError, match="1-based"):
            success_probability(cal, FailureClass.TEMPORARY_LIQUIDITY, 0, NEUTRAL)


class TestCalibrationFile:
    def test_every_failure_class_has_a_base_rate(self, cal: Calibration) -> None:
        # A class silently missing from calibration.yaml would raise a KeyError
        # deep inside a batch run, or worse, be quietly skipped by a `.get()`.
        assert set(cal.base_rates) == set(FailureClass)

    def test_a_missing_class_is_rejected_at_load_with_a_useful_message(
        self, tmp_path: Path
    ) -> None:
        source = yaml.safe_load(CALIBRATION_PATH.read_text())
        del source["base_rates"]["TEMPORARY_LIQUIDITY"]
        path = tmp_path / "partial.yaml"
        path.write_text(yaml.safe_dump(source))

        with pytest.raises(ValueError, match="TEMPORARY_LIQUIDITY"):
            Calibration.load(path)

    def test_every_probability_stays_inside_zero_and_one(self, cal: Calibration) -> None:
        for failure_class in FailureClass:
            for attempt_no in range(1, 13):
                for day in (1, 15, 28):
                    moment = at_ist(2026, 9, day, 3)
                    p = success_probability(cal, failure_class, attempt_no, moment)
                    assert 0.0 <= p <= 1.0


class TestPerturbation:
    def test_scaling_up_raises_every_recoverable_probability(self, cal: Calibration) -> None:
        hotter = cal.perturbed(1.2)
        p_base = success_probability(cal, FailureClass.TEMPORARY_LIQUIDITY, 1, NEUTRAL)
        p_hot = success_probability(hotter, FailureClass.TEMPORARY_LIQUIDITY, 1, NEUTRAL)
        assert p_hot > p_base

    def test_scaling_never_resurrects_a_hard_decline(self, cal: Calibration) -> None:
        # The one property the sensitivity sweep must not be able to break.
        for factor in (0.8, 1.2, 5.0):
            perturbed = cal.perturbed(factor)
            assert success_probability(perturbed, FailureClass.CREDENTIAL_DEAD, 1, NEUTRAL) == 0.0

    def test_perturbing_returns_a_new_calibration_and_leaves_the_original_alone(
        self, cal: Calibration
    ) -> None:
        before = cal.base_rates[FailureClass.TEMPORARY_LIQUIDITY]
        cal.perturbed(1.2)
        assert cal.base_rates[FailureClass.TEMPORARY_LIQUIDITY] == before


class TestSampling:
    def test_a_seed_reproduces_its_own_sequence(self, cal: Calibration) -> None:
        def draw() -> list[bool]:
            rng = random.Random(1234)
            return [
                sample_success(cal, FailureClass.TEMPORARY_LIQUIDITY, 1, NEUTRAL, rng)
                for _ in range(100)
            ]

        assert draw() == draw()

    def test_observed_frequency_tracks_the_stated_probability(self, cal: Calibration) -> None:
        rng = random.Random(7)
        p = success_probability(cal, FailureClass.TEMPORARY_LIQUIDITY, 1, NEUTRAL)
        draws = [
            sample_success(cal, FailureClass.TEMPORARY_LIQUIDITY, 1, NEUTRAL, rng)
            for _ in range(20_000)
        ]
        assert abs(sum(draws) / len(draws) - p) < 0.02
