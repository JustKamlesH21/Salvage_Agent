"""Test helpers for faking the Groq HTTP call via httpx.MockTransport. Not a
test module itself — no test_ prefix, so pytest does not collect it.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx


def openai_response(content: str) -> dict[str, object]:
    return {"choices": [{"message": {"content": content}}]}


def valid_root_cause_json(
    failure_class: str = "TEMPORARY_LIQUIDITY",
    confidence: float = 0.8,
    is_recoverable: bool = True,
    timing_hint: str = "PAYDAY",
) -> str:
    return json.dumps(
        {
            "failure_class": failure_class,
            "confidence": confidence,
            "is_recoverable": is_recoverable,
            "evidence": ["attempt 1 of budget"],
            "suggested_timing_hint": timing_hint,
        }
    )


def client_with_handler(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))
