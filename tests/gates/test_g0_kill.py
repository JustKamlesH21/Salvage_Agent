from __future__ import annotations

from api.gates import g0_kill
from api.gates.types import VerdictKind

from tests.gates.conftest import base_context


class TestAllow:
    def test_switch_off_allows(self) -> None:
        verdict = g0_kill.evaluate(base_context(kill_switch_engaged=False))
        assert verdict.kind is VerdictKind.ALLOW


class TestBlock:
    def test_switch_engaged_blocks_with_clause(self) -> None:
        verdict = g0_kill.evaluate(base_context(kill_switch_engaged=True))
        assert verdict.kind is VerdictKind.BLOCK
        assert verdict.clause_ref == g0_kill.CLAUSE_REF


class TestReplan:
    def test_never_replans(self) -> None:
        for engaged in (True, False):
            verdict = g0_kill.evaluate(base_context(kill_switch_engaged=engaged))
            assert verdict.kind is not VerdictKind.REPLAN


class TestBoundaryAndFireTime:
    def test_engaged_blocks_every_action_type(self) -> None:
        from api.policy.types import Action

        for action in Action:
            verdict = g0_kill.evaluate(base_context(kill_switch_engaged=True, action=action))
            assert verdict.kind is VerdictKind.BLOCK, action

    def test_reflects_current_switch_state_not_a_cached_one(self) -> None:
        # Same "moment", switch flips between calls -> gate re-evaluates
        # fresh each time (invariant 5's spirit applies to every input, not
        # only the clock).
        off = g0_kill.evaluate(base_context(kill_switch_engaged=False))
        on = g0_kill.evaluate(base_context(kill_switch_engaged=True))
        assert off.kind is VerdictKind.ALLOW
        assert on.kind is VerdictKind.BLOCK


class TestMalformedInput:
    def test_no_malformed_case_exists_for_a_bare_bool_but_never_raises(self) -> None:
        # G0 has nothing to malform beyond a bool; documented rather than
        # skipped, per the skill's mandatory template.
        verdict = g0_kill.evaluate(base_context(kill_switch_engaged=False))
        assert verdict.kind is VerdictKind.ALLOW
