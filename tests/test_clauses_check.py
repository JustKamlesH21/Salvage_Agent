"""The clause checker must fail loudly while the gates are unwritten.

Today it reports eleven missing gates and an empty rulebook. That is the
correct answer on day 0 and it must stay the correct answer — a checker that
passes vacuously would let day 5 ship a gate with no clause reference.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts import check_clauses as cc


class TestGateConstants:
    def test_module_constants_are_read_without_importing(self, tmp_path: Path) -> None:
        path = tmp_path / "g1_window.py"
        path.write_text(
            "import a_module_that_does_not_exist\n"
            'CLAUSE_REF = "RBI FPC"\n'
            'RULE_TEXT: str = "08:00-19:00 IST"\n',
            encoding="utf-8",
        )
        assert {"CLAUSE_REF", "RULE_TEXT"} <= cc.module_constants(path)

    def test_a_gate_missing_its_clause_ref_is_visible(self, tmp_path: Path) -> None:
        path = tmp_path / "g0_kill.py"
        path.write_text('RULE_TEXT = "kill switch"\n', encoding="utf-8")
        assert "CLAUSE_REF" not in cc.module_constants(path)

    def test_all_eleven_gates_are_expected(self) -> None:
        names = [name for name, _ in cc.EXPECTED_GATES]
        assert len(names) == 11
        assert names[0] == "G0_KILL", "G0_KILL is evaluated first, always (invariant 4)"


class TestRuleFields:
    def test_every_required_rule_field_is_checked(self) -> None:
        assert set(cc.REQUIRED_RULE_FIELDS) == {"id", "because", "cites"}


class TestCurrentState:
    @pytest.mark.xfail(
        reason="Gates G0-G10 land on day 5 and policy rules on day 4 (PRD §11). "
        "This turns green then, and is the signal that they did.",
        strict=True,
    )
    def test_clause_check_passes(self) -> None:
        assert cc.main() == 0
