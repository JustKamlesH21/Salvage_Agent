from __future__ import annotations

import dataclasses
import datetime as dt

import pytest
from api.gates.types import GateContext
from api.policy.types import Action

# 2026-09-03 10:00 IST == 04:30 UTC. A fixed, comfortably-in-window instant
# used as the default "now" so individual tests only need to override what
# they're actually testing.
COMPLIANT_NOW_UTC = dt.datetime(2026, 9, 3, 4, 30, tzinfo=dt.UTC)


def base_context(**overrides: object) -> GateContext:
    defaults: dict[str, object] = {
        "now_utc": COMPLIANT_NOW_UTC,
        "action": Action.RETRY_NOW,
        "kill_switch_engaged": False,
        "amount_paise": 5_00_00,  # ₹5,000
        "category": "standard",
        "mandate_type": "card",
        "subscription_state": "active",
        "salvage_attempts_in_window": (),
        "contacts_in_7d": (),
        "last_contact_at": None,
        "pre_debit_notice_sent_at": None,
        "opted_out": False,
        "contact_target": "account_holder",
        "has_confirmation_channel": True,
        "idempotency_key": "case1:decision1:RETRY_NOW",
        "idempotency_key_exists": False,
    }
    defaults.update(overrides)
    return GateContext(**defaults)  # type: ignore[arg-type]


def replace(ctx: GateContext, **overrides: object) -> GateContext:
    return dataclasses.replace(ctx, **overrides)  # type: ignore[arg-type]


@pytest.fixture
def ctx() -> GateContext:
    return base_context()
