"""Compliance gate chain G0-G10. PRD §9, SPEC §5.5, CLAUDE.md invariant 4.

The LLM cannot see these, cannot call them, and cannot override them. Every
gate exports CLAUSE_REF and RULE_TEXT (scripts/check_clauses.py fails the
build if either is missing) and returns ALLOW/BLOCK/REPLAN against a
GateContext built fresh per call — nothing here is cached from schedule time.
"""

from __future__ import annotations

from api.gates.registry import (
    GATES_IN_ORDER,
    MAX_REMEDIATION_HOPS,
    REMEDIATION,
    ChainResult,
    FinalAction,
    RemediationResult,
    evaluate_chain,
    resolve_final_action,
    resolve_with_remediation,
)
from api.gates.types import (
    CHARGE_ACTIONS,
    CONTACT_ACTIONS,
    OPT_OUT_EXEMPT_ACTIONS,
    GateContext,
    GateVerdict,
    VerdictKind,
)

__all__ = [
    "CHARGE_ACTIONS",
    "CONTACT_ACTIONS",
    "GATES_IN_ORDER",
    "MAX_REMEDIATION_HOPS",
    "OPT_OUT_EXEMPT_ACTIONS",
    "REMEDIATION",
    "ChainResult",
    "FinalAction",
    "GateContext",
    "GateVerdict",
    "RemediationResult",
    "VerdictKind",
    "evaluate_chain",
    "resolve_final_action",
    "resolve_with_remediation",
]
