"""Out-of-order webhook delivery — SPEC §9 failure test #2.

`subscription.halted` arriving before the `payment.failed` that caused it. The
state machine must handle it rather than crash, and must not open a second case
for the same subscription.

**The fixture landed on day 1, not day ~4, and not from the subscription
planned for it.** The original plan was `sub_halt_clock`, waiting out
Razorpay's real T+1/T+2/T+3 over three calendar days. What actually produced
`021-subscription_halted.raw.json` was `sub_business`, via the Dashboard's
"Charge this now" test trigger: `active` → one forced early charge, rejected
for violating the mandate's billing frequency → `halted`, 8 seconds later, with
**zero `subscription.pending` in between**. That contradicts the T+1/T+2/T+3,
four-attempt model stated in PRD §1/§11 and SPEC §1 — see NOTES.md, day 1
close entry, for the two live hypotheses and the follow-up investigation to
distinguish them.

That open question is exactly why this test still cannot be implemented for
real: it needs to know what "out of order" *means* for this state machine
before asserting what recovering from it looks like, and the answer may depend
on which of NOTES.md's two hypotheses is correct. `sub_halt_clock` remains
unauthenticated and is still available if a *natural* T+1/T+2/T+3 sequence
(with `subscription.pending` in it) turns out to be needed for comparison.

The fixture-existence check below is no longer xfail — the fixture is real and
committed. The behavior test stays xfail(strict), now blocked only on
api/ingest/ (day 2) and on resolving the halt-sequence question, not on
capturing anything further.
"""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def fixture_exists(event: str) -> bool:
    return any(FIXTURES.glob(f"*-{event.replace('.', '_')}.raw.json"))


def test_halted_fixture_is_available() -> None:
    assert fixture_exists("subscription.halted"), (
        "tests/fixtures/ has no subscription.halted capture. This test passed as of "
        "the day-1 close commit (021-subscription_halted.raw.json) — if it's failing "
        "again, that fixture was removed or renamed; check git history before "
        "recapturing from scratch."
    )


@pytest.mark.xfail(
    strict=True,
    reason="Blocked on api/ingest/ (day 2) AND on resolving which of NOTES.md's two "
    "halt-sequence hypotheses is correct — this test cannot assert correct "
    "out-of-order behavior until it knows what the in-order behavior actually is. "
    "This is SPEC §9 failure test #2.",
)
def test_halted_before_payment_failed_does_not_crash() -> None:
    """halted delivered first, then the payment.failed that preceded it.

    Asserts: no exception, exactly one case for the subscription, and the case
    lands in a terminal state rather than being reopened by the late event.
    """
    raise AssertionError(
        "Not implemented: needs api/ingest/ (day 2) and a resolved understanding of "
        "the halt sequence (NOTES.md day 1 close). Implement against both, then "
        "delete this raise."
    )
