"""100% branch coverage of an empty package is not a passing grade.

CLAUDE.md requires 100% branch coverage on api/gates/ and api/normalise/,
because those two modules are where correctness is the product. Today both are
empty packages, so coverage reports 100% of zero statements — a vacuous pass.

This test makes that vacuity a visible failure instead of a green tick. It goes
away on its own: the normaliser lands on day 3 and the gates on day 4 (PRD §11,
compressed replan — was day 5 in the original 10-day plan). Do not delete it to
get a green run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
COVERED_PACKAGES = ("api/gates", "api/normalise")


def statement_bearing_modules(package: Path) -> list[Path]:
    """Modules with something to measure — a docstring-only file has no statements."""
    modules = []
    for path in sorted(package.glob("*.py")):
        body = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        if len(body) > 1:
            modules.append(path)
    return modules


@pytest.mark.parametrize("package", COVERED_PACKAGES)
def test_covered_package_has_code_to_cover(package: str) -> None:
    modules = statement_bearing_modules(ROOT / package)
    assert modules, (
        f"{package}/ contains no measurable code, so its 100% branch-coverage "
        f"requirement passes over nothing. This is expected on day 0 and is the "
        f"scaffold telling the truth: the normaliser lands on day 3 and the gates "
        f"on day 4 (PRD §11, compressed replan). This test turns green when they do."
    )
