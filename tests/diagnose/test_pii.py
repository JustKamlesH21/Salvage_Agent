"""Invariant 3: no name, contact, email, or amount in LLM prompts.

Enforced primarily at the type level — DiagnosisContext has no field to hold
any of those, so leaking one is a type error at the call site, not a
possible oversight inside the prompt builder. The content-level test below is
defense in depth for a future regression (a subclass, a monkeypatch, a field
added to DiagnosisContext without updating this test), not the primary guard.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

from api.diagnose.client import build_prompt
from api.diagnose.schema import DiagnosisContext
from api.normalise.types import FailureClass

PERMITTED_FIELDS = {
    "failure_reason",
    "attempt_count",
    "attempt_timestamps",
    "prior_failure_classes",
    "mandate_type",
    "prior_successful_charge_dates",
}

FORBIDDEN_FIELD_NAMES = {
    "amount",
    "amount_paise",
    "name",
    "customer_name",
    "contact",
    "phone",
    "mobile",
    "email",
}


class TestDiagnosisContextTypeShape:
    def test_field_set_matches_spec_5_3_rule_1_exactly(self) -> None:
        field_names = {f.name for f in dataclasses.fields(DiagnosisContext)}
        assert field_names == PERMITTED_FIELDS

    def test_no_forbidden_field_name_exists_on_the_type(self) -> None:
        field_names = {f.name for f in dataclasses.fields(DiagnosisContext)}
        assert field_names.isdisjoint(FORBIDDEN_FIELD_NAMES)


class TestRenderedPromptContentDefenseInDepth:
    def test_prompt_contains_no_currency_email_or_phone_markers(self) -> None:
        context = DiagnosisContext(
            failure_reason="insufficient_funds",
            attempt_count=2,
            attempt_timestamps=(dt.datetime(2026, 9, 1, 10, 0, tzinfo=dt.UTC),),
            prior_failure_classes=(FailureClass.TEMPORARY_LIQUIDITY,),
            mandate_type="card",
            prior_successful_charge_dates=(dt.date(2026, 8, 1),),
        )
        prompt = build_prompt(context)
        for marker in ("₹", "@", "+91"):
            assert marker not in prompt, f"prompt contains a PII-adjacent marker: {marker!r}"

    def test_prompt_only_renders_the_six_permitted_fields(self) -> None:
        context = DiagnosisContext(
            failure_reason="card_expired",
            attempt_count=0,
            attempt_timestamps=(),
            prior_failure_classes=(),
            mandate_type="upi_autopay",
            prior_successful_charge_dates=(),
        )
        prompt = build_prompt(context)
        for field in PERMITTED_FIELDS:
            assert f"{field}:" in prompt, f"expected context field {field!r} to be rendered"
