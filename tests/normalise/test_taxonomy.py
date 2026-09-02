"""load_taxonomy()'s own branches, plus a round-trip against the real
committed taxonomy/failure_classes.yaml.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from api.normalise.classifier import TAXONOMY_PATH, classify, load_taxonomy
from api.normalise.types import FailureClass

PUBLISHED_REASONS_PATH = TAXONOMY_PATH.parent / "razorpay_published_reasons.yaml"


def _write(tmp_path: Path, content: object) -> Path:
    path = tmp_path / "taxonomy.yaml"
    path.write_text(yaml.safe_dump(content), encoding="utf-8")
    return path


class TestLoadTaxonomyShapes:
    def test_dict_with_rules_key(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            {
                "rules": [
                    {"reason": "insufficient_funds", "class": "TEMPORARY_LIQUIDITY", "source": "s"}
                ]
            },
        )
        rules = load_taxonomy(path)
        assert len(rules) == 1
        assert rules[0].failure_class is FailureClass.TEMPORARY_LIQUIDITY

    def test_bare_list_without_rules_key(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            [{"reason": "insufficient_funds", "class": "TEMPORARY_LIQUIDITY", "source": "s"}],
        )
        rules = load_taxonomy(path)
        assert len(rules) == 1

    def test_fixture_field_present(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            {
                "rules": [
                    {
                        "reason": "payment_failed",
                        "class": "CHANNEL_DOWNTIME",
                        "source": "s",
                        "fixture": ["029"],
                    }
                ]
            },
        )
        rules = load_taxonomy(path)
        assert rules[0].fixture == ("029",)

    def test_fixture_field_absent_defaults_to_empty_tuple(self, tmp_path: Path) -> None:
        path = _write(tmp_path, {"rules": [{"reason": "x", "class": "UNKNOWN", "source": "s"}]})
        rules = load_taxonomy(path)
        assert rules[0].fixture == ()

    def test_rule_with_no_match_key_raises(self, tmp_path: Path) -> None:
        path = _write(tmp_path, {"rules": [{"class": "UNKNOWN", "source": "s"}]})
        with pytest.raises(ValueError, match="none of reason/code/description_contains"):
            load_taxonomy(path)


class TestRealTaxonomyFile:
    def test_every_entry_has_a_source(self) -> None:
        for rule in load_taxonomy(TAXONOMY_PATH):
            assert rule.source, "every taxonomy entry must cite where its classification came from"

    def test_every_published_reason_has_a_rule(self) -> None:
        # FR-04's acceptance criterion, made checkable rather than asserted:
        # "every reason in Razorpay's published list maps to a FailureClass
        # or explicitly to UNKNOWN." Compares against an independently
        # committed fixture of the published list (spec-guardian G3), not
        # against this file's own reason set, so a future taxonomy edit that
        # silently drops a reason fails this test rather than passing
        # trivially.
        published = set(
            yaml.safe_load(PUBLISHED_REASONS_PATH.read_text(encoding="utf-8"))["reasons"]
        )
        covered = {r.reason for r in load_taxonomy(TAXONOMY_PATH) if r.reason is not None}
        missing = published - covered
        assert not missing, f"published reasons with no taxonomy rule at all: {sorted(missing)}"

    def test_fallback_rules_are_ordered_after_every_reason_keyed_rule(self) -> None:
        # classify() is first-match-wins over file order (spec-guardian G6):
        # the code+description fallback and code-only fallback rules are
        # correct only because they sit after every exact-reason rule. This
        # pins that as a checked property, not a convention a future edit
        # could silently break.
        rules = load_taxonomy(TAXONOMY_PATH)
        reason_indices = [i for i, r in enumerate(rules) if r.reason is not None]
        fallback_indices = [i for i, r in enumerate(rules) if r.reason is None]
        assert reason_indices, "expected at least one reason-keyed rule"
        assert fallback_indices, "expected at least one fallback (description/code-only) rule"
        assert max(reason_indices) < min(fallback_indices), (
            "a code+description_contains or code-only fallback rule sits before a "
            "reason-keyed rule — this can silently reclassify anything that also has "
            "a populated error_reason"
        )

    def test_at_least_one_rule_per_non_unknown_class(self) -> None:
        rules = load_taxonomy(TAXONOMY_PATH)
        covered = {rule.failure_class for rule in rules}
        for failure_class in FailureClass:
            assert failure_class in covered, f"{failure_class} has no taxonomy rule at all"

    def test_insufficient_funds_classifies_temporary_liquidity(self) -> None:
        rules = load_taxonomy(TAXONOMY_PATH)
        result = classify(rules, code=None, reason="insufficient_funds", description=None)
        assert result.failure_class is FailureClass.TEMPORARY_LIQUIDITY

    def test_real_fixture_029_reason_classifies_channel_downtime(self) -> None:
        # fixture 029: error_reason=payment_failed, error_source=gateway,
        # error_step=payment_authorization — our one real decline capture.
        rules = load_taxonomy(TAXONOMY_PATH)
        result = classify(
            rules, code="BAD_REQUEST_ERROR", reason="payment_failed", description="Payment failed"
        )
        assert result.failure_class is FailureClass.CHANNEL_DOWNTIME

    def test_mandate_frequency_artifact_classifies_unknown_via_description(self) -> None:
        # fixtures 020/028: error_reason/source/step all null, only
        # error_code + error_description populated (NOTES.md day-1 finding 1).
        rules = load_taxonomy(TAXONOMY_PATH)
        result = classify(
            rules,
            code="BAD_REQUEST_ERROR",
            reason=None,
            description="Mandate debit not as per frequency",
        )
        assert result.failure_class is FailureClass.UNKNOWN
        assert result.source == "fixture:020,028"

    def test_bare_bad_request_error_with_no_reason_or_known_description_is_unknown(self) -> None:
        # BAD_REQUEST_ERROR alone carries no signal (NOTES.md: seen on both a
        # real decline and an unrelated test-tool artifact) — must not match
        # a code-only rule the way GATEWAY_ERROR/SERVER_ERROR do.
        rules = load_taxonomy(TAXONOMY_PATH)
        result = classify(
            rules, code="BAD_REQUEST_ERROR", reason=None, description="some other message"
        )
        assert result.failure_class is FailureClass.UNKNOWN
        assert result.source is None
