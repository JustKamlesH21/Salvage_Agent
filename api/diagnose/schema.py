"""RootCause (SPEC §5.3's LLM output contract), DiagnosisContext (the only
input the prompt builder ever sees — invariant 3 is enforced by this type
having no amount/name/contact/email field to leak), and Diagnosis (the
combined result PRD §8.2's decisions.diagnosis shape).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from api.normalise.types import FailureClass


class TimingHint(StrEnum):
    NOW = "NOW"
    HOURS_24 = "HOURS_24"
    PAYDAY = "PAYDAY"
    NEVER = "NEVER"


class RootCause(BaseModel):
    """SPEC §5.3's exact contract. `extra="forbid"` so a model that hallucinates
    fields fails validation and triggers the repair retry rather than silently
    passing with unexpected data.
    """

    model_config = ConfigDict(extra="forbid")

    failure_class: FailureClass
    confidence: float = Field(ge=0.0, le=1.0)
    is_recoverable: bool
    evidence: list[str] = Field(default_factory=list, max_length=3)
    suggested_timing_hint: TimingHint

    @field_validator("evidence")
    @classmethod
    def _evidence_items_are_bounded(cls, value: list[str]) -> list[str]:
        for item in value:
            if len(item) > 120:
                raise ValueError(f"evidence item exceeds 120 chars: {item!r}")
        return value


@dataclass(frozen=True)
class DiagnosisContext:
    """The ONLY input to the prompt builder. Invariant 3: no name, contact,
    email or amount field exists on this type — leaking one is a type error
    at the call site, not a possible oversight inside the prompt builder.
    SPEC §5.3 rule 1 names exactly these fields as permitted.
    """

    failure_reason: str | None
    attempt_count: int
    attempt_timestamps: tuple[dt.datetime, ...]
    prior_failure_classes: tuple[FailureClass, ...]
    mandate_type: str  # "card" | "upi_autopay"
    prior_successful_charge_dates: tuple[dt.date, ...]


@dataclass(frozen=True)
class Diagnosis:
    """PRD §8.2's decisions.diagnosis shape. `source` is the invariant-2
    record: "llm" only when the normaliser returned UNKNOWN and the LLM's
    class was actually used; "deterministic_override" whenever the
    normaliser's non-UNKNOWN result won, or the LLM was skipped entirely
    (DISABLE_LLM). `llm_raw` is stored verbatim whenever the LLM was called,
    even when its output didn't drive the decision — that's what makes the
    arm-C (LLM-disabled) comparison possible later.
    """

    failure_class: FailureClass
    confidence: float
    is_recoverable: bool
    evidence: tuple[str, ...]
    timing_hint: TimingHint | None
    source: Literal["llm", "deterministic_override"]
    llm_raw: str | None
