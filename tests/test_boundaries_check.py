"""The boundary checker has to actually catch things.

A build gate nobody has seen fail is not a gate. Each test feeds the checker a
violation it is supposed to find, and one clean file it must leave alone.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from scripts import check_boundaries as cb


def parse(tmp_path: Path, name: str, source: str) -> tuple[Path, ast.AST, str]:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path, ast.parse(source), source


class TestSdkBoundary:
    @pytest.mark.parametrize(
        "source",
        [
            "import razorpay",
            "import razorpay.client",
            "from razorpay import Client",
            "from razorpay.errors import BadRequestError",
            "import importlib\nx = importlib.import_module('razorpay')",
            "x = __import__('razorpay')",
        ],
    )
    def test_sdk_import_outside_the_executor_is_caught(self, tmp_path: Path, source: str) -> None:
        path, tree, _ = parse(tmp_path, "rogue.py", source)
        assert cb.check_sdk_imports(path, tree), f"missed: {source!r}"

    def test_the_sanctioned_module_is_allowed(self, tmp_path: Path) -> None:
        path, tree, _ = parse(tmp_path, "client.py", "import razorpay")
        cb.SDK_ALLOWED.add(path)
        try:
            assert cb.check_sdk_imports(path, tree) == []
        finally:
            cb.SDK_ALLOWED.discard(path)

    def test_unrelated_imports_pass(self, tmp_path: Path) -> None:
        path, tree, _ = parse(tmp_path, "fine.py", "import httpx\nfrom pymongo import ASCENDING")
        assert cb.check_sdk_imports(path, tree) == []

    def test_the_real_codebase_has_exactly_one_door(self) -> None:
        offenders = []
        for path in cb.python_files(cb.SDK_SCAN_ROOTS):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            offenders += cb.check_sdk_imports(path, tree)
        assert offenders == [], "\n".join(str(o) for o in offenders)


class TestAppendOnly:
    @pytest.mark.parametrize(
        "source",
        [
            "db.events.update_one({}, {})",
            "db.audit.delete_one({})",
            'db["events"].replace_one({}, {})',
            'db["audit"].find_one_and_update({}, {})',
            "self._events_collection.delete_many({})",
            "audit_collection.bulk_write([])",
            "db.events.drop()",
        ],
    )
    def test_mutation_of_an_append_only_collection_is_caught(
        self, tmp_path: Path, source: str
    ) -> None:
        path, tree, text = parse(tmp_path, "rogue.py", source)
        assert cb.check_append_only(path, tree, text), f"missed: {source!r}"

    @pytest.mark.parametrize(
        "source",
        [
            "db.cases.update_one({}, {})",
            "db.customers.find_one_and_update({}, {})",
            "db.events.insert_one({})",
            "db.audit.find_one({})",
        ],
    )
    def test_legitimate_calls_pass(self, tmp_path: Path, source: str) -> None:
        path, tree, text = parse(tmp_path, "fine.py", source)
        assert cb.check_append_only(path, tree, text) == []

    def test_a_mutating_method_added_to_an_append_only_repo_is_caught(self, tmp_path: Path) -> None:
        source = "class AuditRepository:\n    async def update(self, x):\n        return x\n"
        path, tree, text = parse(tmp_path, "repositories.py", source)
        problems = cb.check_append_only(path, tree, text)
        assert problems and "append-only repository" in str(problems[0])

    def test_a_private_mutating_method_is_also_caught(self, tmp_path: Path) -> None:
        source = "class EventRepository:\n    def _delete_bad_row(self):\n        pass\n"
        path, tree, text = parse(tmp_path, "repositories.py", source)
        assert cb.check_append_only(path, tree, text)

    def test_reads_on_an_append_only_repo_are_fine(self, tmp_path: Path) -> None:
        source = "class EventRepository:\n    async def by_rz_event_id(self, x):\n        pass\n"
        path, tree, text = parse(tmp_path, "repositories.py", source)
        assert cb.check_append_only(path, tree, text) == []


class TestScanCoverage:
    """The scan must not have blind spots.

    On day 0 SDK_SCAN_ROOTS was `[api/]` with scripts/ deliberately carved out.
    `make verify` stayed green the whole time, because the exemption lived
    inside the checker: the harness could not catch it, being the thing at
    fault. These tests are the structural fix — a directory that appears later
    cannot quietly fall outside the scan.
    """

    def test_every_top_level_python_directory_is_scanned(self) -> None:
        uncovered = [p for p in cb.top_level_python_dirs() if p not in cb.SDK_SCAN_ROOTS]
        assert uncovered == [], (
            f"{[p.name for p in uncovered]} contain Python but are outside SDK_SCAN_ROOTS, "
            f"so nothing stops them importing the Razorpay SDK. Add them to the list in "
            f"scripts/check_boundaries.py — CLAUDE.md invariant 1 has no exceptions."
        )

    def test_the_scan_actually_reaches_the_scripts_directory(self) -> None:
        # The specific hole that existed on day 0, pinned by name.
        scanned = cb.python_files(cb.SDK_SCAN_ROOTS)
        assert any(p.name == "seed_razorpay.py" for p in scanned)

    def test_only_the_executor_client_is_allowlisted(self) -> None:
        assert {cb.ROOT / "api" / "executor" / "client.py"} == cb.SDK_ALLOWED

    def test_discovery_ignores_artefact_directories(self) -> None:
        names = {p.name for p in cb.top_level_python_dirs()}
        assert not (names & cb.NOT_SOURCE)


class TestWholeRepo:
    def test_check_passes_on_the_current_tree(self) -> None:
        assert cb.main() == 0
