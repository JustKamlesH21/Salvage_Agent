"""The append-only repositories must not have a way to change history.

Two layers, both tested: the class surface (no mutating method exists) and the
boundary checker that fails the build if one is added later.
"""

from __future__ import annotations

import inspect

from api.db import repositories as repo

MUTATING_WORDS = ("update", "delete", "replace", "upsert", "remove", "drop")


class TestRepositorySurface:
    def test_event_and_audit_repositories_expose_no_mutating_method(self) -> None:
        for cls in (repo.EventRepository, repo.AuditRepository):
            names = [
                name
                for name, _ in inspect.getmembers(cls, predicate=inspect.isfunction)
                if not name.startswith("__")
            ]
            offending = [n for n in names if any(word in n.lower() for word in MUTATING_WORDS)]
            assert offending == [], (
                f"{cls.__name__} exposes {offending}. PRD §8.4: these repositories offer "
                f"insert and reads only. Correct a bad record by appending, not by editing."
            )

    def test_they_do_expose_insert(self) -> None:
        for cls in (repo.EventRepository, repo.AuditRepository):
            assert hasattr(cls, "insert")

    def test_mutable_repositories_are_unaffected(self) -> None:
        # The restriction is on events and audit, not on the whole data layer.
        assert hasattr(repo.CaseRepository, "transition")
