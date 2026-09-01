"""Out-of-order webhook delivery — SPEC §9 failure test #2.

`subscription.halted` arriving before the `payment.failed` that caused it. The
state machine must handle it rather than crash, and must not open a second case
for the same subscription.

Written now, on day 1, against a fixture that does not exist yet. The
subscription that will produce it is `sub_halt_clock` in the seed manifest.
**Its T+1/T+2/T+3 clock has not started**: it sits in `created` state with no
mandate, so it never charges and never fails. The three days begin only when
runbook step 5 authenticates it and forces the first failure. Until someone
runs that step, `subscription.halted` cannot arrive at all.

Marked xfail(strict) so the gap is visible in every test run and turns green on
its own the moment the fixture lands — the same pattern as
tests/test_coverage_floor.py. If it has not turned green by day 5, say so
rather than synthesising the payload.
"""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def fixture_exists(event: str) -> bool:
    return any(FIXTURES.glob(f"*-{event.replace('.', '_')}.raw.json"))


@pytest.mark.xfail(
    strict=True,
    reason="subscription.halted fixture not captured. sub_halt_clock has no "
    "authenticated mandate, so its T+1/T+2/T+3 clock has not started — three days "
    "from runbook step 5, not from day 1 (NOTES.md).",
)
def test_halted_fixture_is_available() -> None:
    assert fixture_exists("subscription.halted"), (
        "tests/fixtures/ has no subscription.halted capture. Run "
        "scripts/capture_webhooks.py and check whether sub_halt_clock has exhausted "
        "its retries — see tests/fixtures/README.md step 9."
    )


@pytest.mark.xfail(
    strict=True,
    reason="Ingest lands on day 2 and the halted fixture on ~day 4. This is SPEC §9 "
    "failure test #2 and it is written now so the gap cannot be forgotten.",
)
def test_halted_before_payment_failed_does_not_crash() -> None:
    """halted delivered first, then the payment.failed that preceded it.

    Asserts: no exception, exactly one case for the subscription, and the case
    lands in a terminal state rather than being reopened by the late event.
    """
    raise AssertionError(
        "Not implemented: needs api/ingest/ (day 2) and the subscription.halted "
        "fixture (~day 4). Implement against both, then delete this raise."
    )
