from __future__ import annotations

from api.gates import g9_halted
from api.gates.types import VerdictKind
from api.policy.types import Action

from tests.gates.conftest import base_context


def ctx(**overrides: object):  # type: ignore[no-untyped-def]
    defaults: dict[str, object] = {"action": Action.RETRY_NOW}
    defaults.update(overrides)
    return base_context(**defaults)


class TestAllow:
    def test_active_subscription_allows(self) -> None:
        verdict = g9_halted.evaluate(ctx(subscription_state="active"))
        assert verdict.kind is VerdictKind.ALLOW

    def test_non_charge_action_allowed_even_when_halted(self) -> None:
        # Recovery from halted is a re-auth/AFA link, not a further silent
        # debit — those must stay reachable while halted.
        for action in (Action.CREATE_REAUTH_LINK, Action.CREATE_AFA_LINK):
            verdict = g9_halted.evaluate(ctx(action=action, subscription_state="halted"))
            assert verdict.kind is VerdictKind.ALLOW, action


class TestBlock:
    def test_halted_subscription_blocks_auto_charge(self) -> None:
        verdict = g9_halted.evaluate(ctx(subscription_state="halted"))
        assert verdict.kind is VerdictKind.BLOCK
        assert verdict.clause_ref == g9_halted.CLAUSE_REF


class TestReplan:
    def test_never_replans(self) -> None:
        verdict = g9_halted.evaluate(ctx(subscription_state="halted"))
        assert verdict.kind is not VerdictKind.REPLAN


class TestBoundaryAndFireTime:
    def test_reflects_current_subscription_state_not_a_cached_one(self) -> None:
        active = g9_halted.evaluate(ctx(subscription_state="active"))
        halted = g9_halted.evaluate(ctx(subscription_state="halted"))
        assert active.kind is VerdictKind.ALLOW
        assert halted.kind is VerdictKind.BLOCK


class TestMalformedInput:
    def test_unrecognised_state_blocks_rather_than_assumes_active(self) -> None:
        verdict = g9_halted.evaluate(ctx(subscription_state="not_a_real_state"))
        assert verdict.kind is VerdictKind.BLOCK
        assert "malformed" in verdict.detail
