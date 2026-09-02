"""ActionResult — what execute_action() returns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Outcome = Literal[
    "executed", "blocked_kill_switch", "blocked_not_armed", "in_flight", "already_executed"
]


@dataclass(frozen=True)
class ActionResult:
    outcome: Outcome
    idempotency_key: str
    response: dict[str, Any] | None = None
    dry_run: bool | None = None
