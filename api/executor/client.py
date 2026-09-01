"""The ONLY module in the codebase permitted to import the Razorpay SDK.

CLAUDE.md invariant 1. scripts/check_boundaries.py AST-walks every top-level
Python directory in the repository and fails the build if `razorpay` is
imported anywhere else. No exemptions, including setup scripts. Everything reaches Razorpay
through `execute_action()` in api/executor/, which audits before it dispatches.

This is the architectural answer to "how do we know your agent can't go rogue":
there is exactly one door, and the build breaks if someone cuts a second one.

Day 0: the door exists and is closed. Dispatch lands on Day 6 (PRD §11).
"""

from __future__ import annotations

from typing import Any

import razorpay

from api.config import settings

_client: razorpay.Client | None = None


def get_razorpay_client() -> razorpay.Client:
    """Construct the SDK client lazily, so importing this module needs no keys."""
    global _client
    if _client is None:
        cfg = settings()
        if not cfg.razorpay_key_id or not cfg.razorpay_key_secret:
            raise RuntimeError(
                "RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET are unset. Copy .env.example "
                "to .env and fill in your test-mode keys."
            )
        _client = razorpay.Client(auth=(cfg.razorpay_key_id, cfg.razorpay_key_secret))
    return _client


def dispatch(action_id: str, payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
    """Send one bounded action to Razorpay. Called only by execute_action().

    Not implemented on Day 0 by design: nothing can reach the network until the
    executor, its pre-audit row and the gate chain exist (PRD §11 day 6).
    """
    raise NotImplementedError(
        f"Razorpay dispatch is not wired yet (action {action_id}, key {idempotency_key}). "
        f"It lands with the executor on day 6. Until then every path runs dry-run."
    )
