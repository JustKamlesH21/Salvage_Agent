"""Deterministic failure normaliser. PRD FR-04, SPEC §5.2. Pure lookup, no LLM.

`classify()` is pure and takes an explicit rule list — that's what gives it
100% branch coverage without file I/O in every test. `RULES` is the one
module-level I/O: the taxonomy is read once, at import time, and reused.
"""

from __future__ import annotations

from api.normalise.classifier import classify, load_taxonomy
from api.normalise.types import FailureClass, NormaliseResult, TaxonomyRule

RULES: list[TaxonomyRule] = load_taxonomy()


def classify_payload(
    *, code: str | None, reason: str | None, description: str | None
) -> NormaliseResult:
    """Convenience entry point against the loaded taxonomy. Callers that want
    to test against a specific rule set (or an empty one) should call
    classify() directly instead.
    """
    return classify(RULES, code=code, reason=reason, description=description)


__all__ = [
    "RULES",
    "FailureClass",
    "NormaliseResult",
    "TaxonomyRule",
    "classify",
    "classify_payload",
    "load_taxonomy",
]
