from __future__ import annotations

from api.gates import g8_postdebit
from api.gates.types import VerdictKind
from api.policy.types import Action

from tests.gates.conftest import base_context


def ctx(**overrides: object):  # type: ignore[no-untyped-def]
    defaults: dict[str, object] = {"action": Action.RETRY_NOW}
    defaults.update(overrides)
    return base_context(**defaults)


class TestAllow:
    def test_confirmation_channel_present_allows(self) -> None:
        verdict = g8_postdebit.evaluate(ctx(has_confirmation_channel=True))
        assert verdict.kind is VerdictKind.ALLOW

    def test_non_charge_action_always_allows(self) -> None:
        verdict = g8_postdebit.evaluate(ctx(action=Action.NUDGE, has_confirmation_channel=False))
        assert verdict.kind is VerdictKind.ALLOW


class TestBlock:
    def test_no_confirmation_channel_blocks_with_clause(self) -> None:
        verdict = g8_postdebit.evaluate(ctx(has_confirmation_channel=False))
        assert verdict.kind is VerdictKind.BLOCK
        assert verdict.clause_ref == g8_postdebit.CLAUSE_REF


class TestReplan:
    def test_never_replans(self) -> None:
        verdict = g8_postdebit.evaluate(ctx(has_confirmation_channel=False))
        assert verdict.kind is not VerdictKind.REPLAN


class TestBoundaryAndFireTime:
    def test_reflects_current_channel_state_not_a_cached_one(self) -> None:
        before = g8_postdebit.evaluate(ctx(has_confirmation_channel=False))
        after = g8_postdebit.evaluate(ctx(has_confirmation_channel=True))
        assert before.kind is VerdictKind.BLOCK
        assert after.kind is VerdictKind.ALLOW


class TestMalformedInput:
    def test_has_confirmation_channel_is_a_bool_nothing_further_to_malform(self) -> None:
        verdict = g8_postdebit.evaluate(ctx(has_confirmation_channel=False))
        assert verdict.kind is VerdictKind.BLOCK
