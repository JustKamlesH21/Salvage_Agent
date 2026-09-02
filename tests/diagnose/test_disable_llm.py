"""DISABLE_LLM (arm C, SPEC §5.10): zero HTTP calls, normaliser output passed
straight through, source=deterministic_override.
"""

from __future__ import annotations

import httpx
import pytest
from api.diagnose.client import diagnose
from api.normalise.types import FailureClass


class TestDisableLlm:
    def test_zero_http_calls_are_made(self, classified_result, diagnosis_context) -> None:
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            raise AssertionError("DISABLE_LLM must not reach the HTTP transport at all")

        diagnose(
            classified_result,
            diagnosis_context,
            disable_llm=True,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        assert calls == []

    def test_normaliser_output_passes_straight_through(
        self, classified_result, diagnosis_context
    ) -> None:
        result = diagnose(classified_result, diagnosis_context, disable_llm=True)
        assert result.failure_class is FailureClass.TEMPORARY_LIQUIDITY
        assert result.source == "deterministic_override"
        assert result.llm_raw is None
        assert result.confidence == 1.0

    def test_unknown_stays_unknown_with_no_llm_to_disambiguate(
        self, unknown_result, diagnosis_context
    ) -> None:
        result = diagnose(unknown_result, diagnosis_context, disable_llm=True)
        assert result.failure_class is FailureClass.UNKNOWN
        assert result.source == "deterministic_override"
        assert result.is_recoverable is False

    def test_disable_llm_true_never_constructs_a_client_at_all(
        self, classified_result, diagnosis_context, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _fail_if_constructed(*args: object, **kwargs: object) -> httpx.Client:
            raise AssertionError("DISABLE_LLM must not construct an httpx.Client")

        monkeypatch.setattr(httpx, "Client", _fail_if_constructed)
        result = diagnose(classified_result, diagnosis_context, disable_llm=True)
        assert result.source == "deterministic_override"

    def test_settings_default_is_used_when_disable_llm_not_passed_explicitly(
        self, classified_result, diagnosis_context, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from api.config import settings

        settings.cache_clear()
        monkeypatch.setenv("DISABLE_LLM", "true")
        try:
            result = diagnose(classified_result, diagnosis_context)
            assert result.source == "deterministic_override"
            assert result.llm_raw is None
        finally:
            settings.cache_clear()
