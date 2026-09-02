from __future__ import annotations

from api.gates import g6_optout
from api.gates.types import VerdictKind
from api.policy.types import Action

from tests.gates.conftest import base_context


def ctx(**overrides: object):  # type: ignore[no-untyped-def]
    return base_context(**overrides)


class TestAllow:
    def test_not_opted_out_allows(self) -> None:
        verdict = g6_optout.evaluate(ctx(opted_out=False, action=Action.RETRY_NOW))
        assert verdict.kind is VerdictKind.ALLOW

    def test_close_no_action_allowed_even_when_opted_out(self) -> None:
        verdict = g6_optout.evaluate(ctx(opted_out=True, action=Action.CLOSE_NO_ACTION))
        assert verdict.kind is VerdictKind.ALLOW

    def test_suppress_allowed_even_when_opted_out(self) -> None:
        verdict = g6_optout.evaluate(ctx(opted_out=True, action=Action.SUPPRESS))
        assert verdict.kind is VerdictKind.ALLOW


class TestBlock:
    def test_opted_out_blocks_every_other_action(self) -> None:
        for action in Action:
            if action in (Action.CLOSE_NO_ACTION, Action.SUPPRESS):
                continue
            verdict = g6_optout.evaluate(ctx(opted_out=True, action=action))
            assert verdict.kind is VerdictKind.BLOCK, action
            assert verdict.clause_ref == g6_optout.CLAUSE_REF


class TestReplan:
    def test_never_replans(self) -> None:
        verdict = g6_optout.evaluate(ctx(opted_out=True, action=Action.RETRY_NOW))
        assert verdict.kind is not VerdictKind.REPLAN


class TestBoundaryAndFireTime:
    def test_reflects_current_opt_out_state_not_a_cached_one(self) -> None:
        before = g6_optout.evaluate(ctx(opted_out=False, action=Action.NUDGE))
        after = g6_optout.evaluate(ctx(opted_out=True, action=Action.NUDGE))
        assert before.kind is VerdictKind.ALLOW
        assert after.kind is VerdictKind.BLOCK


class TestMalformedInput:
    def test_opted_out_is_a_bool_nothing_further_to_malform(self) -> None:
        verdict = g6_optout.evaluate(ctx(opted_out=True, action=Action.RETRY_NOW))
        assert verdict.kind is VerdictKind.BLOCK
