"""Malformed output -> one repair retry -> UNKNOWN. Transport failures follow
the same path. Normaliser-not-UNKNOWN always wins regardless of what the
(mocked) LLM returns — invariant 2, tested against the real HTTP seam.
"""

from __future__ import annotations

import json

import httpx
import pytest
from api.diagnose.client import diagnose
from api.diagnose.schema import TimingHint
from api.normalise.types import FailureClass

from tests.diagnose.llm_fakes import client_with_handler, openai_response, valid_root_cause_json


class TestValidFirstAttempt:
    def test_valid_output_on_first_call_is_used_directly(
        self, unknown_result, diagnosis_context
    ) -> None:
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(200, json=openai_response(valid_root_cause_json()))

        result = diagnose(
            unknown_result,
            diagnosis_context,
            disable_llm=False,
            client=client_with_handler(handler),
        )
        assert result.failure_class is FailureClass.TEMPORARY_LIQUIDITY
        assert result.source == "llm"
        assert result.timing_hint is TimingHint.PAYDAY
        assert len(calls) == 1


class TestRepairRetry:
    def test_malformed_json_then_valid_succeeds_via_repair(
        self, unknown_result, diagnosis_context
    ) -> None:
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            content = "not json at all" if attempts["n"] == 1 else valid_root_cause_json()
            return httpx.Response(200, json=openai_response(content))

        result = diagnose(
            unknown_result,
            diagnosis_context,
            disable_llm=False,
            client=client_with_handler(handler),
        )
        assert result.failure_class is FailureClass.TEMPORARY_LIQUIDITY
        assert result.source == "llm"
        assert attempts["n"] == 2
        # SPEC §5.3 rule 3: "Log both attempts" — the malformed first attempt
        # must survive in llm_raw, not just the successful repair.
        assert result.llm_raw is not None
        assert "not json at all" in result.llm_raw
        assert "TEMPORARY_LIQUIDITY" in result.llm_raw

    def test_schema_violation_also_triggers_repair(self, unknown_result, diagnosis_context) -> None:
        # Valid JSON, but confidence is out of the 0..1 range — a schema
        # violation, not a parse failure. Must take the same repair path.
        bad = json.dumps(
            {
                "failure_class": "TEMPORARY_LIQUIDITY",
                "confidence": 5.0,
                "is_recoverable": True,
                "evidence": [],
                "suggested_timing_hint": "NOW",
            }
        )
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            content = bad if attempts["n"] == 1 else valid_root_cause_json()
            return httpx.Response(200, json=openai_response(content))

        result = diagnose(
            unknown_result,
            diagnosis_context,
            disable_llm=False,
            client=client_with_handler(handler),
        )
        assert result.source == "llm"
        assert attempts["n"] == 2

    def test_malformed_twice_falls_back_to_unknown(self, unknown_result, diagnosis_context) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=openai_response("still not json"))

        result = diagnose(
            unknown_result,
            diagnosis_context,
            disable_llm=False,
            client=client_with_handler(handler),
        )
        assert result.failure_class is FailureClass.UNKNOWN
        assert result.confidence == 0.0
        assert result.source == "llm"
        # SPEC §5.3 rule 3: "Log both attempts" — both, not just the last one.
        # (The Pydantic error text also echoes the invalid input, so the
        # literal string can appear more than twice — count is not asserted.)
        assert result.llm_raw is not None
        assert result.llm_raw.count("still not json") >= 2
        assert "[attempt 1]" in result.llm_raw
        assert "[attempt 2]" in result.llm_raw


class TestTransportFailure:
    def test_request_error_on_both_attempts_falls_back_to_unknown(
        self, unknown_result, diagnosis_context
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("simulated network failure", request=request)

        result = diagnose(
            unknown_result,
            diagnosis_context,
            disable_llm=False,
            client=client_with_handler(handler),
        )
        assert result.failure_class is FailureClass.UNKNOWN
        assert result.source == "llm"
        assert result.llm_raw is None

    def test_first_call_errors_second_call_succeeds(
        self, unknown_result, diagnosis_context
    ) -> None:
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise httpx.ConnectError("simulated network failure", request=request)
            return httpx.Response(200, json=openai_response(valid_root_cause_json()))

        result = diagnose(
            unknown_result,
            diagnosis_context,
            disable_llm=False,
            client=client_with_handler(handler),
        )
        assert result.failure_class is FailureClass.TEMPORARY_LIQUIDITY
        assert result.source == "llm"

    def test_http_5xx_status_triggers_repair_path(self, unknown_result, diagnosis_context) -> None:
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            if attempts["n"] == 1:
                return httpx.Response(500, json={"error": "boom"})
            return httpx.Response(200, json=openai_response(valid_root_cause_json()))

        result = diagnose(
            unknown_result,
            diagnosis_context,
            disable_llm=False,
            client=client_with_handler(handler),
        )
        assert result.source == "llm"
        assert attempts["n"] == 2


class TestNormaliserAlwaysWins:
    def test_normaliser_wins_even_when_llm_returns_a_different_class(
        self, classified_result, diagnosis_context
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            disagreement = valid_root_cause_json(
                failure_class="CREDENTIAL_DEAD", timing_hint="NEVER"
            )
            return httpx.Response(200, json=openai_response(disagreement))

        result = diagnose(
            classified_result,
            diagnosis_context,
            disable_llm=False,
            client=client_with_handler(handler),
        )
        assert result.failure_class is FailureClass.TEMPORARY_LIQUIDITY
        assert result.source == "deterministic_override"

    def test_llm_is_still_called_and_raw_output_stored_even_though_unused(
        self, classified_result, diagnosis_context
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=openai_response(valid_root_cause_json()))

        result = diagnose(
            classified_result,
            diagnosis_context,
            disable_llm=False,
            client=client_with_handler(handler),
        )
        assert result.source == "deterministic_override"
        assert result.llm_raw is not None  # stored for the arm-C comparison, even though unused

    def test_normaliser_wins_even_when_the_llm_call_fails_outright(
        self, classified_result, diagnosis_context
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("simulated network failure", request=request)

        result = diagnose(
            classified_result,
            diagnosis_context,
            disable_llm=False,
            client=client_with_handler(handler),
        )
        assert result.failure_class is FailureClass.TEMPORARY_LIQUIDITY
        assert result.source == "deterministic_override"


class TestOwnedClientLifecycle:
    """Every other test in this module injects `client=`. diagnose()'s
    `client=None` branch — where it constructs and closes its own
    httpx.Client(timeout=LLM_TIMEOUT_SECONDS) — had no test at all
    (spec-guardian G4). Real network access still isn't exercised (that
    would make the suite non-deterministic and slow); httpx.Client itself is
    monkeypatched to accept a transport kwarg pointed at the same
    MockTransport fake, so the *owned-client* code path — construct, use,
    close via `with` — actually runs.
    """

    def test_owned_client_is_constructed_used_and_closed(
        self, unknown_result, diagnosis_context, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # httpx.Client's `with` block closes via __exit__ directly closing the
        # transport(s) — it never calls self.close() — so `is_closed` after
        # the fact is the real signal, not a patched close() call count.
        constructed: list[httpx.Client] = []
        real_client_cls = httpx.Client

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=openai_response(valid_root_cause_json()))

        def fake_client(*args: object, **kwargs: object) -> httpx.Client:
            client = real_client_cls(transport=httpx.MockTransport(handler))
            constructed.append(client)
            return client

        monkeypatch.setattr(httpx, "Client", fake_client)

        result = diagnose(unknown_result, diagnosis_context, disable_llm=False)

        assert result.failure_class is FailureClass.TEMPORARY_LIQUIDITY
        assert len(constructed) == 1
        assert constructed[0].is_closed is True
