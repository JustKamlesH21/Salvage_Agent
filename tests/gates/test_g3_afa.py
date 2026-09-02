from __future__ import annotations

from api.gates import g3_afa
from api.gates.types import VerdictKind
from api.policy.types import Action

from tests.gates.conftest import base_context

STANDARD = g3_afa.STANDARD_THRESHOLD_PAISE
ENHANCED = g3_afa.ENHANCED_THRESHOLD_PAISE


def ctx(**overrides: object):  # type: ignore[no-untyped-def]
    defaults: dict[str, object] = {"action": Action.RETRY_SCHEDULED}
    defaults.update(overrides)
    return base_context(**defaults)


class TestAllow:
    def test_at_or_under_standard_threshold_allows(self) -> None:
        verdict = g3_afa.evaluate(ctx(amount_paise=STANDARD, category="standard"))
        assert verdict.kind is VerdictKind.ALLOW

    def test_non_charge_action_always_allows(self) -> None:
        verdict = g3_afa.evaluate(ctx(action=Action.CREATE_AFA_LINK, amount_paise=STANDARD * 10))
        assert verdict.kind is VerdictKind.ALLOW


class TestBlock:
    def test_over_standard_threshold_blocks_with_clause(self) -> None:
        verdict = g3_afa.evaluate(ctx(amount_paise=STANDARD + 1, category="standard"))
        assert verdict.kind is VerdictKind.BLOCK
        assert verdict.clause_ref == g3_afa.CLAUSE_REF

    def test_over_enhanced_threshold_blocks_even_for_insurance(self) -> None:
        verdict = g3_afa.evaluate(ctx(amount_paise=ENHANCED + 1, category="insurance"))
        assert verdict.kind is VerdictKind.BLOCK


class TestReplan:
    def test_never_replans(self) -> None:
        # gate-authoring skill: "waiting changes nothing" for G3_AFA.
        for amount in (STANDARD + 1, ENHANCED + 1):
            verdict = g3_afa.evaluate(ctx(amount_paise=amount, category="standard"))
            assert verdict.kind is not VerdictKind.REPLAN


class TestBoundaryValues:
    def test_15000_allows(self) -> None:
        verdict = g3_afa.evaluate(ctx(amount_paise=15_000 * 100, category="standard"))
        assert verdict.kind is VerdictKind.ALLOW

    def test_15001_blocks(self) -> None:
        verdict = g3_afa.evaluate(ctx(amount_paise=15_001 * 100, category="standard"))
        assert verdict.kind is VerdictKind.BLOCK

    def test_1_lakh_allows_for_each_enhanced_category(self) -> None:
        for category in ("insurance", "mf", "cc_bill"):
            verdict = g3_afa.evaluate(ctx(amount_paise=100_000 * 100, category=category))
            assert verdict.kind is VerdictKind.ALLOW, category

    def test_1_lakh_plus_1_blocks_for_each_enhanced_category(self) -> None:
        for category in ("insurance", "mf", "cc_bill"):
            verdict = g3_afa.evaluate(ctx(amount_paise=100_000 * 100 + 100, category=category))
            assert verdict.kind is VerdictKind.BLOCK, category

    def test_15001_blocks_even_though_it_would_pass_the_enhanced_threshold(self) -> None:
        # a standard-category case above 15k must not accidentally borrow the
        # enhanced ceiling.
        verdict = g3_afa.evaluate(ctx(amount_paise=15_001 * 100, category="standard"))
        assert verdict.kind is VerdictKind.BLOCK


class TestFireTimeReEvaluation:
    def test_reflects_the_current_amount_context_not_a_cached_one(self) -> None:
        under = g3_afa.evaluate(ctx(amount_paise=STANDARD, category="standard"))
        over = g3_afa.evaluate(ctx(amount_paise=STANDARD + 1, category="standard"))
        assert under.kind is VerdictKind.ALLOW
        assert over.kind is VerdictKind.BLOCK


class TestMalformedInput:
    def test_unknown_category_blocks(self) -> None:
        verdict = g3_afa.evaluate(ctx(amount_paise=100, category="bogus"))
        assert verdict.kind is VerdictKind.BLOCK
        assert "malformed" in verdict.detail

    def test_negative_amount_blocks(self) -> None:
        verdict = g3_afa.evaluate(ctx(amount_paise=-1, category="standard"))
        assert verdict.kind is VerdictKind.BLOCK
        assert "malformed" in verdict.detail
