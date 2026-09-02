"""The outcome simulator — SPEC §5.9's `P(success | ...)` model.

Pure and seeded. Nothing here reaches a network, a database or a clock: the
caller supplies the attempt time and the RNG, so a run is reproducible from
its seed alone. That is what makes the sensitivity analysis meaningful — the
only thing that changes between the baseline run and a perturbed one is the
calibration.

Two deliberate departures from SPEC §5.9's stated model signature
`P(success | failure_class, attempt_no, days_since_fail, hour_of_day, dom,
mandate_type)`, both stated here rather than left to be discovered:

  * `mandate_type` is not a parameter. Nothing in the sources consulted gives
    a basis for card-versus-UPI-autopay success rates, and inventing a
    modifier to match a signature would be a fabricated number in the one file
    where fabricated numbers do the most damage.
  * `days_since_fail` is not a parameter either. It is fully expressed by the
    attempt's own timestamp (which carries the day-of-month and hour the
    modifiers actually key on) together with `attempt_no`. Passing it
    separately would let a caller describe an attempt that is temporally
    impossible.

All day-of-month and hour logic is evaluated in **IST**. Payday and "night"
are facts about the customer's life, not about the server's timezone
(CLAUDE.md: most timezone bugs in this codebase will be here).
"""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml
from api.normalise.types import FailureClass

CALIBRATION_PATH = Path(__file__).resolve().parent / "calibration.yaml"

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


@dataclass(frozen=True)
class Calibration:
    """Everything sim/calibration.yaml declares, already validated.

    Frozen so a run cannot mutate its own parameters partway through, and so
    `perturbed()` has to produce a new object rather than editing the shared
    one — the sensitivity sweep runs many variants and a shared mutable
    calibration would silently contaminate them.
    """

    base_rates: dict[FailureClass, float]
    payday_days: frozenset[int]
    payday_modifier: float
    pre_payday_days: frozenset[int]
    pre_payday_modifier: float
    night_modifier: float
    night_from_hour_ist: int
    night_to_hour_ist: int
    weekend_modifier: float
    hard_decline_classes: frozenset[FailureClass]
    attempt_decay: float
    attempt_decay_from: int
    baseline_retry_offsets_days: tuple[int, ...]

    @classmethod
    def load(cls, path: Path = CALIBRATION_PATH) -> Calibration:
        raw: dict[str, Any] = yaml.safe_load(path.read_text())
        base_rates_raw = raw["base_rates"]

        missing = {c.value for c in FailureClass} - set(base_rates_raw)
        if missing:
            raise ValueError(
                f"calibration.yaml is missing a base rate for {sorted(missing)}. Every "
                f"FailureClass needs one — add it with a source, or the simulator will "
                f"silently treat that class as unreachable."
            )

        mods = raw["modifiers"]
        return cls(
            base_rates={FailureClass(k): float(v["value"]) for k, v in base_rates_raw.items()},
            payday_days=frozenset(mods["payday_window"]["days_of_month"]),
            payday_modifier=float(mods["payday_window"]["value"]),
            pre_payday_days=frozenset(mods["pre_payday"]["days_of_month"]),
            pre_payday_modifier=float(mods["pre_payday"]["value"]),
            night_modifier=float(mods["night"]["value"]),
            night_from_hour_ist=int(mods["night"]["from_hour_ist"]),
            night_to_hour_ist=int(mods["night"]["to_hour_ist"]),
            weekend_modifier=float(mods["weekend"]["value"]),
            hard_decline_classes=frozenset(
                FailureClass(c) for c in mods["hard_decline_retry"]["applies_to"]
            ),
            attempt_decay=float(mods["attempt_decay"]["value"]),
            attempt_decay_from=int(mods["attempt_decay"]["applies_from_attempt"]),
            baseline_retry_offsets_days=tuple(raw["baseline"]["retry_offsets_days"]),
        )

    def perturbed(self, factor: float) -> Calibration:
        """This calibration with every rate and modifier scaled by `factor`.

        The sensitivity analysis's only tool. `hard_decline_retry` is a
        zeroing rule rather than a rate, so scaling it changes nothing — 0.0
        stays 0.0 at any factor, which is correct: "hard declines never
        recover on retry" is not a number we are uncertain about by ±20%, it
        is a categorical claim, and pretending to perturb it would overstate
        how much of the model the sweep actually covers. CALIBRATION.md says
        so plainly.
        """
        return replace(
            self,
            base_rates={k: _clamp(v * factor) for k, v in self.base_rates.items()},
            payday_modifier=self.payday_modifier * factor,
            pre_payday_modifier=self.pre_payday_modifier * factor,
            night_modifier=self.night_modifier * factor,
            weekend_modifier=self.weekend_modifier * factor,
            attempt_decay=self.attempt_decay * factor,
        )


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _is_night(hour_ist: int, cal: Calibration) -> bool:
    """The night window wraps midnight (19:00 → 08:00), so this is an OR, not
    a range test. A plain `from <= hour < to` reads fine and is wrong for
    every hour that matters.
    """
    return hour_ist >= cal.night_from_hour_ist or hour_ist < cal.night_to_hour_ist


def success_probability(
    cal: Calibration,
    failure_class: FailureClass,
    attempt_no: int,
    at_utc: dt.datetime,
) -> float:
    """Probability that a charge attempt for this class, at this moment,
    succeeds. `attempt_no` is 1-based and counts charge attempts against this
    case only.
    """
    if attempt_no < 1:
        raise ValueError(f"attempt_no is 1-based; got {attempt_no}")
    if at_utc.tzinfo is None:
        raise ValueError(
            "at_utc must be timezone-aware — the day-of-month and hour modifiers "
            "are evaluated in IST, and a naive datetime silently means 'assume the "
            "server's zone', which is the bug this rejects."
        )

    if failure_class in cal.hard_decline_classes:
        return 0.0

    probability = cal.base_rates[failure_class]

    at_ist = at_utc.astimezone(IST)
    if at_ist.day in cal.pre_payday_days:
        probability *= cal.pre_payday_modifier
    elif at_ist.day in cal.payday_days:
        probability *= cal.payday_modifier

    if _is_night(at_ist.hour, cal):
        probability *= cal.night_modifier

    if at_ist.weekday() >= 5:  # Saturday, Sunday
        probability *= cal.weekend_modifier

    if attempt_no >= cal.attempt_decay_from:
        probability *= cal.attempt_decay ** (attempt_no - cal.attempt_decay_from + 1)

    return _clamp(probability)


def sample_success(
    cal: Calibration,
    failure_class: FailureClass,
    attempt_no: int,
    at_utc: dt.datetime,
    rng: random.Random,
) -> bool:
    """One Bernoulli draw. The RNG is the caller's, so a whole run is
    reproducible from its seed.
    """
    return rng.random() < success_probability(cal, failure_class, attempt_no, at_utc)
