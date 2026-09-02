"""FailureClass and the normaliser's result shape. SPEC §4.1, PRD FR-04."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FailureClass(StrEnum):
    """The 9 canonical classes, SPEC §4.1 verbatim. String enum so it
    serialises directly into Mongo documents and YAML comparisons without a
    converter.
    """

    TEMPORARY_LIQUIDITY = "TEMPORARY_LIQUIDITY"
    ISSUER_HOLD = "ISSUER_HOLD"
    CHANNEL_DOWNTIME = "CHANNEL_DOWNTIME"
    AUTH_ABANDONED = "AUTH_ABANDONED"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    CREDENTIAL_STALE = "CREDENTIAL_STALE"
    CREDENTIAL_DEAD = "CREDENTIAL_DEAD"
    MANDATE_INVALID = "MANDATE_INVALID"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class TaxonomyRule:
    """One entry from taxonomy/failure_classes.yaml.

    `reason`, `description_contains` and `code` are match keys — unset (None)
    means wildcard on that field. `classify()` requires every set key on a
    rule to match; an unset key never excludes a candidate.
    """

    failure_class: FailureClass
    source: str
    reason: str | None = None
    description_contains: str | None = None
    code: str | None = None
    note: str | None = None
    fixture: tuple[str, ...] = ()


@dataclass(frozen=True)
class NormaliseResult:
    """What classify() returns. `matched_rule` carries the citation through
    to the decision record even when the class is UNKNOWN — an UNKNOWN with
    no explanation is a worse audit trail than one that says why.
    """

    failure_class: FailureClass
    source: str | None
    note: str | None
