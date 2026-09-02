"""Action catalog (SPEC §4.2) and the policy engine's input/output shapes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from api.normalise.types import FailureClass


class Action(StrEnum):
    """SPEC §4.2's exact catalog. Anything not in this list cannot execute —
    Day 4's executor validates against this same enum before dispatch.
    """

    RETRY_NOW = "RETRY_NOW"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    SEND_PRE_DEBIT_NOTICE = "SEND_PRE_DEBIT_NOTICE"
    CREATE_REAUTH_LINK = "CREATE_REAUTH_LINK"
    CREATE_AFA_LINK = "CREATE_AFA_LINK"
    NUDGE = "NUDGE"
    PAUSE_SUBSCRIPTION = "PAUSE_SUBSCRIPTION"
    ESCALATE_HUMAN = "ESCALATE_HUMAN"
    CLOSE_NO_ACTION = "CLOSE_NO_ACTION"
    SUPPRESS = "SUPPRESS"


@dataclass(frozen=True)
class PolicyContext:
    """What a rule's `when` block is matched against. Deliberately narrow —
    the policy engine proposes based on diagnosis and attempt history only;
    amount-based routing (AFA threshold, etc.) is the gate chain's job
    (G3_AFA, Day 4), not policy's.
    """

    failure_class: FailureClass
    attempt_count: int


@dataclass(frozen=True)
class Rule:
    """One parsed entry from api/policy/rules/*.yaml."""

    id: str
    priority: int
    when: dict[str, object]
    action: Action
    because: str
    cites: tuple[str, ...]
    timing: str | None = None
    stop_all_retries: bool = False
    channel_note: str | None = None
    max_delay_minutes: int | None = None


@dataclass(frozen=True)
class PolicyDecision:
    """What evaluate() returns — SPEC §5.4/PRD §8.2's `decisions.policy` shape."""

    rule_id: str
    action: Action
    because: str
    cites: tuple[str, ...]
    timing: str | None = None
    stop_all_retries: bool = False
    channel_note: str | None = None
    max_delay_minutes: int | None = None
