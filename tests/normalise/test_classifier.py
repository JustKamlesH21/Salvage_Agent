"""Branch coverage for api.normalise.classifier.classify(). Synthetic rules
only — decoupled from the real taxonomy content so it doesn't churn when
taxonomy/failure_classes.yaml gains or loses entries. tests/normalise/
test_taxonomy.py covers the real file.
"""

from __future__ import annotations

from api.normalise.classifier import classify
from api.normalise.types import FailureClass, TaxonomyRule

REASON_RULE = TaxonomyRule(
    failure_class=FailureClass.TEMPORARY_LIQUIDITY, source="src:reason", reason="insufficient_funds"
)
CODE_RULE = TaxonomyRule(
    failure_class=FailureClass.CHANNEL_DOWNTIME, source="src:code", code="GATEWAY_ERROR"
)
DESCRIPTION_RULE = TaxonomyRule(
    failure_class=FailureClass.UNKNOWN,
    source="src:description",
    description_contains="not as per frequency",
)


class TestReasonMatching:
    def test_exact_reason_hit(self) -> None:
        result = classify([REASON_RULE], code=None, reason="insufficient_funds", description=None)
        assert result.failure_class is FailureClass.TEMPORARY_LIQUIDITY
        assert result.source == "src:reason"

    def test_reason_case_and_whitespace_insensitive(self) -> None:
        result = classify(
            [REASON_RULE], code=None, reason="  Insufficient_Funds ", description=None
        )
        assert result.failure_class is FailureClass.TEMPORARY_LIQUIDITY

    def test_reason_mismatch_falls_through_to_unknown(self) -> None:
        result = classify([REASON_RULE], code=None, reason="card_expired", description=None)
        assert result.failure_class is FailureClass.UNKNOWN

    def test_reason_none_does_not_match_a_reason_rule(self) -> None:
        result = classify([REASON_RULE], code=None, reason=None, description=None)
        assert result.failure_class is FailureClass.UNKNOWN


class TestCodeMatching:
    def test_exact_code_hit(self) -> None:
        result = classify([CODE_RULE], code="GATEWAY_ERROR", reason=None, description=None)
        assert result.failure_class is FailureClass.CHANNEL_DOWNTIME

    def test_code_case_and_whitespace_insensitive(self) -> None:
        result = classify([CODE_RULE], code=" gateway_error ", reason=None, description=None)
        assert result.failure_class is FailureClass.CHANNEL_DOWNTIME

    def test_code_mismatch_falls_through_to_unknown(self) -> None:
        result = classify([CODE_RULE], code="SERVER_ERROR", reason=None, description=None)
        assert result.failure_class is FailureClass.UNKNOWN

    def test_code_none_does_not_match_a_code_rule(self) -> None:
        result = classify([CODE_RULE], code=None, reason=None, description=None)
        assert result.failure_class is FailureClass.UNKNOWN


class TestDescriptionMatching:
    def test_substring_hit(self) -> None:
        result = classify(
            [DESCRIPTION_RULE],
            code=None,
            reason=None,
            description="Mandate debit not as per frequency",
        )
        assert result.failure_class is FailureClass.UNKNOWN
        assert result.source == "src:description"

    def test_substring_case_insensitive(self) -> None:
        result = classify(
            [DESCRIPTION_RULE], code=None, reason=None, description="NOT AS PER FREQUENCY, blocked"
        )
        assert result.failure_class is FailureClass.UNKNOWN
        assert result.source == "src:description"

    def test_substring_absent(self) -> None:
        result = classify(
            [DESCRIPTION_RULE], code=None, reason=None, description="totally unrelated"
        )
        assert result.failure_class is FailureClass.UNKNOWN
        assert result.source is None  # fell through to the no-match default, not the rule

    def test_description_none_does_not_match_a_description_rule(self) -> None:
        result = classify([DESCRIPTION_RULE], code=None, reason=None, description=None)
        assert result.failure_class is FailureClass.UNKNOWN
        assert result.source is None  # fell through to the no-match default, not the rule


class TestClassifyControlFlow:
    def test_empty_rule_list_is_unknown(self) -> None:
        result = classify([], code="ANYTHING", reason="anything", description="anything")
        assert result.failure_class is FailureClass.UNKNOWN
        assert result.source is None

    def test_first_non_matching_rule_falls_through_to_second_matching_rule(self) -> None:
        result = classify(
            [REASON_RULE, CODE_RULE],
            code="GATEWAY_ERROR",
            reason="not_insufficient_funds",
            description=None,
        )
        assert result.failure_class is FailureClass.CHANNEL_DOWNTIME

    def test_first_match_wins_over_a_later_matching_rule(self) -> None:
        # Both rules would match; the earlier one in file order must win.
        also_matches_channel_downtime = TaxonomyRule(
            failure_class=FailureClass.CREDENTIAL_DEAD, source="src:decoy", code="GATEWAY_ERROR"
        )
        result = classify(
            [CODE_RULE, also_matches_channel_downtime],
            code="GATEWAY_ERROR",
            reason=None,
            description=None,
        )
        assert result.failure_class is FailureClass.CHANNEL_DOWNTIME

    def test_no_rule_matches_returns_unknown_with_explanatory_note(self) -> None:
        result = classify(
            [REASON_RULE, CODE_RULE], code="SERVER_ERROR", reason="card_expired", description=None
        )
        assert result.failure_class is FailureClass.UNKNOWN
        assert result.note is not None and "never a guess" in result.note
