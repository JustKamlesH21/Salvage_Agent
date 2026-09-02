"""Shared fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from api.db.collections import apply_indexes
from api.db.repositories import Repositories
from mongomock_motor import AsyncMongoMockClient

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def repo_root() -> Path:
    return ROOT


@pytest.fixture
async def repos() -> Repositories:
    """A fresh in-memory Mongo per test — same pattern as
    tests/test_repositories.py's own `repos` fixture, shared here so
    tests/gates/, tests/executor/, tests/scheduler/ and the pipeline e2e
    tests don't each redefine it.
    """
    db = AsyncMongoMockClient()["salvage_test"]
    await apply_indexes(db)
    return Repositories(db)
