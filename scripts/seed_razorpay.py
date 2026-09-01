#!/usr/bin/env python3
"""Create test-mode plans, subscriptions and mandates in Razorpay.

Day 1 (PRD §11). Deliberately not implemented on Day 0: seeding touches the
live test-mode API and consumes budgeted resources.

Constraints this script must respect when it is written (SPEC §5.8):
  * Card tokens are valid 3 days in test mode. Seed close to the demo.
  * 30 Payment Links per business, ever. Never create one from a batch run.
  * A test charge on a subscription can only be triggered from the Dashboard.

This file lives outside the SDK boundary scan on purpose: it is setup, not a
runtime path. It must never be imported by anything under api/.
"""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "seed: not implemented. Razorpay test-mode seeding lands on day 1 (PRD §11).\n"
        "      Until then, capture real webhook payloads by hand and commit them to\n"
        "      tests/fixtures/ — those fixtures are day 1's gate to advance."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
