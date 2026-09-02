"""Fixture 029 (a real payment.failed capture) through normalise -> diagnose
-> policy, asserting a cited action comes out the other end.

DISABLE_LLM=true keeps this test offline and deterministic — legitimately so,
not as a shortcut: fixture 029's reason (payment_failed) classifies to a real
FailureClass (CHANNEL_DOWNTIME), so invariant 2's "normaliser wins whenever
it isn't UNKNOWN" means the LLM's content can never change this test's
outcome anyway. diagnose()'s LLM-calling path has its own dedicated,
network-mocked tests in tests/diagnose/.

No production pipeline.py glues these three modules together yet (Day 4's
gates need a decision to evaluate too, so that's where the real orchestration
lands) — this test wires normalise -> diagnose -> policy itself.
"""

from __future__ import annotations

import json
from pathlib import Path

from api.diagnose.client import diagnose
from api.diagnose.schema import DiagnosisContext
from api.normalise import classify_payload
from api.policy import RULES as POLICY_RULES
from api.policy.engine import evaluate
from api.policy.types import Action, PolicyContext

FIXTURE_029 = Path(__file__).resolve().parent / "fixtures" / "029-payment_failed.raw.json"


def test_fixture_029_through_the_full_phase3_pipeline() -> None:
    payload = json.loads(FIXTURE_029.read_text(encoding="utf-8"))
    payment = payload["payload"]["payment"]["entity"]

    normalised = classify_payload(
        code=payment["error_code"],
        reason=payment["error_reason"],
        description=payment["error_description"],
    )
    # Sanity on the fixture itself, so a future fixture change fails loudly
    # here rather than silently changing what this test actually proves.
    assert payment["error_reason"] == "payment_failed"

    context = DiagnosisContext(
        failure_reason=payment["error_reason"],
        attempt_count=1,
        attempt_timestamps=(),
        prior_failure_classes=(),
        mandate_type="card",
        prior_successful_charge_dates=(),
    )
    diagnosis = diagnose(normalised, context, disable_llm=True)
    assert diagnosis.source == "deterministic_override"

    decision = evaluate(
        POLICY_RULES, PolicyContext(failure_class=diagnosis.failure_class, attempt_count=1)
    )

    assert decision.action is Action.RETRY_NOW
    assert decision.rule_id == "POL-008"
    assert decision.because.strip()
    assert len(decision.cites) > 0
