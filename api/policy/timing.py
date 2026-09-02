"""When a permitted charge may actually be attempted.

Policy rules have carried `timing: NEXT_PAYDAY_WINDOW` / `timing: HOURS_24` /
`max_delay_minutes` since day 3, and `PolicyDecision` has carried them through
to the pipeline ever since — where they were dropped on the floor. Nothing
converted a timing intent into a time, so every `RETRY_SCHEDULED` was
dispatched the instant the gates allowed it, which is the same timing the
baseline uses and none of the timing Salvage's whole argument rests on.

CLAUDE.md is explicit that this is the product's key lever and must not be
accidentally designed away. It had been. The day-5 batch found it: arm A beat
arm B, because arm A at least waited a day between attempts.

This module is deliberately not a gate. Gates decide whether something is
*permitted*; this decides when a permitted thing is *worth doing*, which is
policy's job (SPEC §5.4). It can only ever push an attempt later, never
earlier and never past a gate — whatever it schedules is re-gated in full when
it fires.

Anchoring matters more than it looks. A target computed as "now + 24h" is
recomputed on every decision cycle, so it walks forward forever and the
attempt never happens. Targets here are anchored to the case's own history —
its last charge attempt, or its opening — so they are fixed points that the
clock eventually reaches.
"""

from __future__ import annotations

import datetime as dt

from api.diagnose.schema import TimingHint

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# SPEC §5.9's payday window: balances are highest at month-start and mid-month.
PAYDAY_DAYS = (1, 2, 3, 15, 16)

# Where in the day a scheduled attempt lands. Inside G1_WINDOW's 08:00—19:00
# so a contact action remediated from it is not immediately replanned, and
# late enough that a salary credited that morning has arrived.
PAYDAY_HOUR_IST = 10

HOURS_24 = dt.timedelta(hours=24)


def earliest_attempt_at(
    *,
    timing: str | None,
    timing_hint: TimingHint | None,
    anchor: dt.datetime,
    now_utc: dt.datetime,
    max_delay_minutes: int | None = None,
) -> dt.datetime:
    """The earliest moment this charge should be attempted.

    `timing` is the policy rule's stated intent and wins. `timing_hint` is the
    diagnosis's suggestion and is consulted only where the rule has no opinion
    — invariant 2's spirit: the deterministic rule outranks the model.

    `anchor` is the case's own reference point (its last charge attempt, or
    when it opened), never `now_utc`. See the module docstring.

    `max_delay_minutes` caps how far past `now_utc` the result may sit. It is
    how POL-008 says "retry a gateway blip now, and if something wants to
    delay it, not by more than 45 minutes."
    """
    target = _target_for(timing, timing_hint, anchor)

    if max_delay_minutes is not None:
        cap = now_utc + dt.timedelta(minutes=max_delay_minutes)
        target = min(target, cap)

    return max(target, now_utc)


def _target_for(
    timing: str | None, timing_hint: TimingHint | None, anchor: dt.datetime
) -> dt.datetime:
    effective = timing or _from_hint(timing_hint)

    if effective == "HOURS_24":
        return anchor + HOURS_24
    if effective == "NEXT_PAYDAY_WINDOW":
        return next_payday_window(anchor)
    # "NOW", None, and anything unrecognised: no delay. An unknown timing
    # string must not silently postpone a charge indefinitely — the failure
    # mode of a typo should be "acts immediately", which is visible, not
    # "never acts", which is not.
    return anchor


def _from_hint(timing_hint: TimingHint | None) -> str | None:
    if timing_hint is TimingHint.HOURS_24:
        return "HOURS_24"
    if timing_hint is TimingHint.PAYDAY:
        return "NEXT_PAYDAY_WINDOW"
    # NOW and NEVER both mean "do not delay this here". NEVER is a statement
    # that the case is unrecoverable, which is the policy engine's call to
    # make by proposing a terminal action — not something to express by
    # scheduling a charge infinitely far away.
    return None


def next_payday_window(after: dt.datetime) -> dt.datetime:
    """The next payday-window moment strictly after `after`, in UTC.

    Evaluated in IST: payday is a fact about the customer's month, not the
    server's timezone (CLAUDE.md — most timezone bugs in this codebase will
    be here).
    """
    local = after.astimezone(IST)
    candidate = local.replace(hour=PAYDAY_HOUR_IST, minute=0, second=0, microsecond=0)

    # 40 days covers the longest gap between windows (the 16th to the 1st)
    # from any starting point, with room for month lengths.
    for _ in range(40):
        if candidate > local and candidate.day in PAYDAY_DAYS:
            return candidate.astimezone(dt.UTC)
        candidate += dt.timedelta(days=1)

    raise RuntimeError(
        f"no payday window found within 40 days of {after.isoformat()} — PAYDAY_DAYS "
        f"is empty or malformed, which would silently postpone every scheduled retry."
    )
