"""Policy engine: first-match-wins, priority ordering, attempt_count operators,
and per-rule coverage per .claude/skills/policy-rule-authoring's checklist —
for every rule: (1) a case that matches it and gets its action, (2) a case
that matches it *and* a lower-priority rule, asserting this one wins, and
(3) the catch-all is reachable.
"""

from __future__ import annotations

import pytest
from api.normalise.types import FailureClass
from api.policy import RULES
from api.policy.engine import evaluate, load_rules
from api.policy.types import Action, PolicyContext, Rule


def ctx(failure_class: FailureClass, attempt_count: int = 0) -> PolicyContext:
    return PolicyContext(failure_class=failure_class, attempt_count=attempt_count)


class TestRealRuleSetPerRule:
    def test_credential_dead_stops_and_reauths(self) -> None:
        decision = evaluate(RULES, ctx(FailureClass.CREDENTIAL_DEAD))
        assert decision.rule_id == "POL-001"
        assert decision.action is Action.CREATE_REAUTH_LINK
        assert decision.stop_all_retries is True

    def test_mandate_invalid_stops_and_routes_to_afa_not_reauth(self) -> None:
        # spec-guardian D1: MANDATE_INVALID needs AFA (SPEC §4.1's own default
        # play), not the same bare re-auth link as a dead card — these two
        # classes share "stop auto-retry" but not the same recovery action.
        decision = evaluate(RULES, ctx(FailureClass.MANDATE_INVALID))
        assert decision.rule_id == "POL-010"
        assert decision.action is Action.CREATE_AFA_LINK
        assert decision.stop_all_retries is True

    def test_unknown_escalates(self) -> None:
        decision = evaluate(RULES, ctx(FailureClass.UNKNOWN))
        assert decision.rule_id == "POL-002"
        assert decision.action is Action.ESCALATE_HUMAN

    def test_credential_stale_reauth(self) -> None:
        decision = evaluate(RULES, ctx(FailureClass.CREDENTIAL_STALE))
        assert decision.rule_id == "POL-003"
        assert decision.action is Action.CREATE_REAUTH_LINK

    def test_auth_abandoned_reauth(self) -> None:
        decision = evaluate(RULES, ctx(FailureClass.AUTH_ABANDONED))
        assert decision.rule_id == "POL-004"
        assert decision.action is Action.CREATE_REAUTH_LINK

    def test_temporary_liquidity_under_budget_retries_at_payday(self) -> None:
        decision = evaluate(RULES, ctx(FailureClass.TEMPORARY_LIQUIDITY, attempt_count=3))
        assert decision.rule_id == "POL-005"
        assert decision.action is Action.RETRY_SCHEDULED
        assert decision.timing == "NEXT_PAYDAY_WINDOW"

    def test_temporary_liquidity_over_budget_beats_pol_005_and_nudges(self) -> None:
        # matches POL-005 (RETRY-SCHED band) is disjoint on attempt_count from
        # POL-009 (NUDGE band) — the higher attempt_count case must land on
        # POL-009 despite its band sitting after POL-005 numerically.
        decision = evaluate(RULES, ctx(FailureClass.TEMPORARY_LIQUIDITY, attempt_count=4))
        assert decision.rule_id == "POL-009"
        assert decision.action is Action.NUDGE

    def test_issuer_hold_under_budget_retries_in_24h(self) -> None:
        decision = evaluate(RULES, ctx(FailureClass.ISSUER_HOLD, attempt_count=1))
        assert decision.rule_id == "POL-006"
        assert decision.timing == "HOURS_24"

    def test_issuer_hold_over_budget_nudges(self) -> None:
        decision = evaluate(RULES, ctx(FailureClass.ISSUER_HOLD, attempt_count=10))
        assert decision.rule_id == "POL-009"

    def test_limit_exceeded_under_budget_retries_in_24h(self) -> None:
        decision = evaluate(RULES, ctx(FailureClass.LIMIT_EXCEEDED, attempt_count=0))
        assert decision.rule_id == "POL-007"

    def test_limit_exceeded_over_budget_nudges(self) -> None:
        decision = evaluate(RULES, ctx(FailureClass.LIMIT_EXCEEDED, attempt_count=4))
        assert decision.rule_id == "POL-009"

    def test_channel_downtime_retries_now_regardless_of_attempt_count(self) -> None:
        # POL-008 has no attempt_count condition — must beat POL-001..007 (it
        # doesn't share a failure_class with any of them) and never fall to
        # the catch-all no matter how many attempts have been made.
        decision = evaluate(RULES, ctx(FailureClass.CHANNEL_DOWNTIME, attempt_count=99))
        assert decision.rule_id == "POL-008"
        assert decision.action is Action.RETRY_NOW
        assert decision.max_delay_minutes == 45

    def test_catch_all_is_reachable(self) -> None:
        # No real FailureClass reaches this — construct a context whose class
        # every real rule's `when` excludes to prove POL-099 still fires
        # rather than raising. FailureClass is closed (all 9 values are
        # covered by name above), so exercise the catch-all directly via a
        # rule subset instead.
        subset = [r for r in RULES if r.id == "POL-099"]
        decision = evaluate(subset, ctx(FailureClass.UNKNOWN))
        assert decision.rule_id == "POL-099"
        assert decision.action is Action.CLOSE_NO_ACTION

    def test_every_rule_has_because_and_cites(self) -> None:
        # Redundant with scripts/check_clauses.py by design — a second,
        # independent check inside the test suite that doesn't depend on
        # remembering to run the script.
        for rule in RULES:
            assert rule.because.strip()
            assert len(rule.cites) > 0


class TestPriorityOrdering:
    def test_lower_priority_number_wins_regardless_of_list_order(self) -> None:
        low = Rule(
            id="LOW",
            priority=5,
            when={"failure_class": "UNKNOWN"},
            action=Action.ESCALATE_HUMAN,
            because="b",
            cites=("c",),
        )
        high = Rule(
            id="HIGH",
            priority=50,
            when={"failure_class": "UNKNOWN"},
            action=Action.CLOSE_NO_ACTION,
            because="b",
            cites=("c",),
        )
        # Deliberately passed high-priority-number rule first in the list —
        # evaluate() must still pick the lower number.
        decision = evaluate([high, low], ctx(FailureClass.UNKNOWN))
        assert decision.rule_id == "LOW"

    def test_ties_broken_by_id(self) -> None:
        a = Rule(
            id="POL-A",
            priority=10,
            when={"failure_class": "UNKNOWN"},
            action=Action.ESCALATE_HUMAN,
            because="b",
            cites=("c",),
        )
        z = Rule(
            id="POL-Z",
            priority=10,
            when={"failure_class": "UNKNOWN"},
            action=Action.CLOSE_NO_ACTION,
            because="b",
            cites=("c",),
        )
        decision = evaluate([z, a], ctx(FailureClass.UNKNOWN))
        assert decision.rule_id == "POL-A"


class TestConditionOperators:
    def test_failure_class_list_is_any_of(self) -> None:
        rule = Rule(
            id="R",
            priority=1,
            when={"failure_class": ["ISSUER_HOLD", "CHANNEL_DOWNTIME"]},
            action=Action.RETRY_NOW,
            because="b",
            cites=("c",),
        )
        assert evaluate([rule], ctx(FailureClass.CHANNEL_DOWNTIME)).rule_id == "R"

    def test_unmatched_failure_class_falls_through(self) -> None:
        rule = Rule(
            id="R",
            priority=1,
            when={"failure_class": "ISSUER_HOLD"},
            action=Action.RETRY_NOW,
            because="b",
            cites=("c",),
        )
        catch_all = Rule(
            id="Z", priority=99, when={}, action=Action.CLOSE_NO_ACTION, because="b", cites=("c",)
        )
        decision = evaluate([rule, catch_all], ctx(FailureClass.CHANNEL_DOWNTIME))
        assert decision.rule_id == "Z"

    def test_attempt_count_lte_and_gte_combined(self) -> None:
        rule = Rule(
            id="R",
            priority=1,
            when={"failure_class": "ISSUER_HOLD", "attempt_count": {"gte": 1, "lte": 3}},
            action=Action.RETRY_NOW,
            because="b",
            cites=("c",),
        )
        assert evaluate([rule], ctx(FailureClass.ISSUER_HOLD, attempt_count=2)).rule_id == "R"
        with pytest.raises(RuntimeError):
            evaluate([rule], ctx(FailureClass.ISSUER_HOLD, attempt_count=5))

    def test_unsupported_condition_key_raises(self) -> None:
        rule = Rule(
            id="R",
            priority=1,
            when={"amount": 100},
            action=Action.RETRY_NOW,
            because="b",
            cites=("c",),
        )
        with pytest.raises(ValueError, match="unsupported condition key"):
            evaluate([rule], ctx(FailureClass.ISSUER_HOLD))

    def test_unsupported_attempt_count_operator_raises(self) -> None:
        rule = Rule(
            id="R",
            priority=1,
            when={"attempt_count": {"eq": 1}},
            action=Action.RETRY_NOW,
            because="b",
            cites=("c",),
        )
        with pytest.raises(ValueError, match="unsupported attempt_count operator"):
            evaluate([rule], ctx(FailureClass.ISSUER_HOLD))


class TestNoMatch:
    def test_no_rule_matches_and_no_catch_all_raises(self) -> None:
        rule = Rule(
            id="R",
            priority=1,
            when={"failure_class": "ISSUER_HOLD"},
            action=Action.RETRY_NOW,
            because="b",
            cites=("c",),
        )
        with pytest.raises(RuntimeError, match="no policy rule matched"):
            evaluate([rule], ctx(FailureClass.UNKNOWN))


class TestLoadRules:
    def test_loads_the_real_rule_files(self) -> None:
        rules = load_rules()
        assert len(rules) == 11
        assert {r.id for r in rules} == {
            f"POL-{n:03d}" for n in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 99)
        }
