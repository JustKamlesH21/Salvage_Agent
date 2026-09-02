"""Deterministic, priority-ordered, first-match-wins policy engine. SPEC §5.4,
PRD FR-06. No LLM involvement — the LLM's diagnosis has already been folded
into `PolicyContext.failure_class` by the time this runs (invariant 2).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from api.policy.types import Action, PolicyContext, PolicyDecision, Rule

RULES_DIR = Path(__file__).resolve().parent / "rules"

_SUPPORTED_ATTEMPT_COUNT_OPS = frozenset({"lte", "gte"})


def evaluate(rules: list[Rule], context: PolicyContext) -> PolicyDecision:
    """Lower `priority` number is evaluated first. First rule whose `when`
    matches wins. Raises if nothing matches — a rule set without a reachable
    catch-all (`when: {}` at the lowest priority) is a broken rule set, not a
    case to route silently.
    """
    for rule in sorted(rules, key=lambda r: (r.priority, r.id)):
        if _matches(rule.when, context):
            return PolicyDecision(
                rule_id=rule.id,
                action=rule.action,
                because=rule.because,
                cites=rule.cites,
                timing=rule.timing,
                stop_all_retries=rule.stop_all_retries,
                channel_note=rule.channel_note,
                max_delay_minutes=rule.max_delay_minutes,
            )
    raise RuntimeError(
        "no policy rule matched, including no catch-all. A rule set must always have a "
        "when:{} rule at the lowest priority (SPEC §5.4) — a case matching nothing must "
        "land on CLOSE_NO_ACTION with a reason, never fall through silently."
    )


def _matches(when: dict[str, Any], context: PolicyContext) -> bool:
    for key, condition in when.items():
        if key == "failure_class":
            allowed = condition if isinstance(condition, list) else [condition]
            if context.failure_class.value not in allowed:
                return False
        elif key == "attempt_count":
            if not _attempt_count_matches(condition, context.attempt_count):
                return False
        else:
            raise ValueError(f"policy rule uses unsupported condition key {key!r}")
    return True


def _attempt_count_matches(condition: dict[str, int], attempt_count: int) -> bool:
    for op, bound in condition.items():
        if op not in _SUPPORTED_ATTEMPT_COUNT_OPS:
            raise ValueError(f"unsupported attempt_count operator {op!r}")
        if op == "lte" and not attempt_count <= bound:
            return False
        if op == "gte" and not attempt_count >= bound:
            return False
    return True


def load_rules(rules_dir: Path = RULES_DIR) -> list[Rule]:
    """The one I/O boundary. Reads every *.yaml/*.yml in api/policy/rules/,
    same file-discovery pattern scripts/check_clauses.py already uses.
    """
    rules: list[Rule] = []
    files = sorted(rules_dir.glob("*.yaml")) + sorted(rules_dir.glob("*.yml"))
    for path in files:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        entries = loaded.get("rules", []) if isinstance(loaded, dict) else loaded
        for entry in entries:
            rules.append(_parse_rule(entry))
    return rules


def _parse_rule(entry: dict[str, Any]) -> Rule:
    then = entry["then"]
    return Rule(
        id=entry["id"],
        priority=entry["priority"],
        when=entry.get("when") or {},
        action=Action(then["action"]),
        because=entry["because"],
        cites=tuple(entry["cites"]),
        timing=then.get("timing"),
        stop_all_retries=bool(then.get("stop_all_retries", False)),
        channel_note=then.get("channel_note"),
        max_delay_minutes=then.get("max_delay_minutes"),
    )
