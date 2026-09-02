from __future__ import annotations

from api.gates import g10_idempotent
from api.gates.types import VerdictKind

from tests.gates.conftest import base_context


def ctx(**overrides: object):  # type: ignore[no-untyped-def]
    return base_context(**overrides)


class TestAllow:
    def test_unseen_key_allows(self) -> None:
        verdict = g10_idempotent.evaluate(
            ctx(idempotency_key="fresh-key", idempotency_key_exists=False)
        )
        assert verdict.kind is VerdictKind.ALLOW


class TestBlock:
    def test_existing_key_blocks_with_clause(self) -> None:
        verdict = g10_idempotent.evaluate(
            ctx(idempotency_key="seen-key", idempotency_key_exists=True)
        )
        assert verdict.kind is VerdictKind.BLOCK
        assert verdict.clause_ref == g10_idempotent.CLAUSE_REF


class TestReplan:
    def test_never_replans(self) -> None:
        verdict = g10_idempotent.evaluate(
            ctx(idempotency_key="seen-key", idempotency_key_exists=True)
        )
        assert verdict.kind is not VerdictKind.REPLAN


class TestBoundaryAndFireTime:
    def test_reflects_current_existence_state_not_a_cached_one(self) -> None:
        before = g10_idempotent.evaluate(ctx(idempotency_key="k", idempotency_key_exists=False))
        after = g10_idempotent.evaluate(ctx(idempotency_key="k", idempotency_key_exists=True))
        assert before.kind is VerdictKind.ALLOW
        assert after.kind is VerdictKind.BLOCK


class TestMalformedInput:
    def test_empty_key_blocks(self) -> None:
        verdict = g10_idempotent.evaluate(ctx(idempotency_key="", idempotency_key_exists=False))
        assert verdict.kind is VerdictKind.BLOCK
        assert "malformed" in verdict.detail

    def test_whitespace_only_key_blocks(self) -> None:
        verdict = g10_idempotent.evaluate(ctx(idempotency_key="   ", idempotency_key_exists=False))
        assert verdict.kind is VerdictKind.BLOCK
