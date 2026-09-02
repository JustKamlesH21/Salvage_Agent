"""LLM diagnosis. PRD FR-05, SPEC §5.3. Diagnoses only — never chooses a
money action (invariant 2): the normaliser's result wins whenever it isn't
UNKNOWN, and this is enforced in api.diagnose.client, not left to callers.
"""

from __future__ import annotations

from api.diagnose.client import build_prompt, diagnose
from api.diagnose.schema import Diagnosis, DiagnosisContext, RootCause, TimingHint

__all__ = [
    "Diagnosis",
    "DiagnosisContext",
    "RootCause",
    "TimingHint",
    "build_prompt",
    "diagnose",
]
