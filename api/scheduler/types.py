"""Scheduled job and fire-result shapes."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from api.executor.types import ActionResult
from api.gates.registry import ChainResult, FinalAction
from api.policy.types import Action


@dataclass(frozen=True)
class ScheduledJob:
    case_id: str
    decision_id: str
    action: Action
    scheduled_for: dt.datetime  # UTC — the moment this job becomes eligible to fire
    payload: dict[str, Any]
    idempotency_key: str


@dataclass(frozen=True)
class FireResult:
    job: ScheduledJob
    chain: ChainResult
    final: FinalAction
    executed: ActionResult | None  # None when the outcome was REPLAN — nothing dispatched
