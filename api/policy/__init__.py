"""Deterministic, priority-ordered policy engine. PRD FR-06, SPEC §5.4.

`evaluate()` is pure and takes an explicit rule list. `RULES` is the one
module-level I/O: every YAML file in api/policy/rules/ is read once, at
import time, and reused.
"""

from __future__ import annotations

from api.policy.engine import evaluate, load_rules
from api.policy.types import Action, PolicyContext, PolicyDecision, Rule

RULES: list[Rule] = load_rules()

__all__ = [
    "RULES",
    "Action",
    "PolicyContext",
    "PolicyDecision",
    "Rule",
    "evaluate",
    "load_rules",
]
