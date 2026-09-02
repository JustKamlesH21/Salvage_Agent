from __future__ import annotations

import pytest
from api.diagnose.schema import DiagnosisContext
from api.normalise.types import FailureClass, NormaliseResult


@pytest.fixture
def diagnosis_context() -> DiagnosisContext:
    return DiagnosisContext(
        failure_reason="insufficient_funds",
        attempt_count=1,
        attempt_timestamps=(),
        prior_failure_classes=(),
        mandate_type="card",
        prior_successful_charge_dates=(),
    )


@pytest.fixture
def unknown_result() -> NormaliseResult:
    return NormaliseResult(failure_class=FailureClass.UNKNOWN, source=None, note="no match")


@pytest.fixture
def classified_result() -> NormaliseResult:
    return NormaliseResult(failure_class=FailureClass.TEMPORARY_LIQUIDITY, source="s", note=None)
