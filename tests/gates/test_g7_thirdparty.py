from __future__ import annotations

from api.gates import g7_thirdparty
from api.gates.types import VerdictKind

from tests.gates.conftest import base_context


def ctx(**overrides: object):  # type: ignore[no-untyped-def]
    return base_context(**overrides)


class TestAllow:
    def test_account_holder_allows(self) -> None:
        verdict = g7_thirdparty.evaluate(ctx(contact_target="account_holder"))
        assert verdict.kind is VerdictKind.ALLOW


class TestBlock:
    def test_third_party_blocks_with_clause(self) -> None:
        # No code path in this build ever produces this today — this proves
        # the safety net actually fires if one ever does (same shape as
        # G0_KILL/G9_HALTED).
        verdict = g7_thirdparty.evaluate(ctx(contact_target="third_party"))
        assert verdict.kind is VerdictKind.BLOCK
        assert verdict.clause_ref == g7_thirdparty.CLAUSE_REF


class TestReplan:
    def test_never_replans(self) -> None:
        verdict = g7_thirdparty.evaluate(ctx(contact_target="third_party"))
        assert verdict.kind is not VerdictKind.REPLAN


class TestBoundaryAndFireTime:
    def test_reflects_current_contact_target_not_a_cached_one(self) -> None:
        holder = g7_thirdparty.evaluate(ctx(contact_target="account_holder"))
        third = g7_thirdparty.evaluate(ctx(contact_target="third_party"))
        assert holder.kind is VerdictKind.ALLOW
        assert third.kind is VerdictKind.BLOCK


class TestMalformedInput:
    def test_unrecognised_target_blocks_rather_than_assumes_account_holder(self) -> None:
        verdict = g7_thirdparty.evaluate(ctx(contact_target="something_else"))
        assert verdict.kind is VerdictKind.BLOCK
        assert "malformed" in verdict.detail

    def test_empty_string_target_blocks(self) -> None:
        verdict = g7_thirdparty.evaluate(ctx(contact_target=""))
        assert verdict.kind is VerdictKind.BLOCK
