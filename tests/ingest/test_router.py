"""HTTP-level proof that the route is wired correctly.

This is a small, separate FastAPI app mounting only the ingest router — not
`api.main.app` — because `api.main`'s lifespan connects to the real MONGO_URI
at startup, which these tests must not touch. `get_event_repository` is
overridden with a mongomock-backed repository instead.

The bulk of ingest's behaviour is already proven in test_webhook_ingest.py
against the core function directly, with no HTTP layer. This file exists for
exactly one thing that can only be proven at the HTTP layer: that
`request.body()` really is read before anything else in the actual handler,
and that outcomes map to the right status codes over real ASGI request/
response plumbing, not just in the type signatures.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import AsyncIterator

import pytest
from api.config import settings
from api.db.collections import apply_indexes
from api.db.repositories import Repositories
from api.ingest.router import get_event_repository
from api.ingest.router import router as ingest_router
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient
from pymongo.errors import DuplicateKeyError
from scripts.redact_fixtures import FIXTURE_SECRET


def sign(body: bytes) -> str:
    return hmac.new(FIXTURE_SECRET.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    # The router reads the webhook secret from api.config.settings(), the
    # real config loader (day 1's HMAC-parity fix made this the only
    # sanctioned way to read it — see tests/local_only/test_real_signature.py
    # for why a second, hand-rolled path is exactly the bug to avoid). Tests
    # sign fixtures with FIXTURE_SECRET, so the real secret is overridden for
    # the duration of the test via the same env-var + cache-clear pattern
    # used throughout this project, not a parallel config mechanism.
    #
    # settings() is process-wide lru_cache'd: monkeypatch undoes the env var
    # at teardown, but not the already-cached Settings object built while it
    # was patched, so the cache is cleared on both sides of the test to keep
    # this fixture-secret override from leaking into whatever runs next in
    # the same process.
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", FIXTURE_SECRET)
    settings.cache_clear()

    db = AsyncMongoMockClient()["salvage_router_test"]
    await apply_indexes(db)
    repos = Repositories(db)

    app = FastAPI()
    app.include_router(ingest_router)
    app.dependency_overrides[get_event_repository] = lambda: repos.events

    transport = ASGITransport(app=app)
    try:
        yield AsyncClient(transport=transport, base_url="http://test")
    finally:
        settings.cache_clear()


class TestOrderingAtTheHttpLayer:
    async def test_a_body_that_only_parses_after_signature_verification_still_verifies(
        self, client: AsyncClient
    ) -> None:
        # If the handler parsed the body before verifying — the exact bug
        # SPEC §5.1 calls "classic" — a malformed body would raise inside
        # json.loads() before the signature check ever ran, and this request
        # would come back 500 instead of 200. It coming back 200 with
        # signature_valid recorded true is the structural proof of ordering.
        body = b"{not valid json, but validly signed"
        headers = {
            "X-Razorpay-Signature": sign(body),
            "X-Razorpay-Event-Id": "evt_router_ordering",
        }
        async with client as c:
            response = await c.post("/webhooks/razorpay", content=body, headers=headers)
        assert response.status_code == 200
        assert response.json()["status"] == "received"


class TestStatusCodes:
    async def test_valid_signature_returns_200_received(self, client: AsyncClient) -> None:
        body = json.dumps({"event": "payment.failed"}).encode()
        headers = {
            "X-Razorpay-Signature": sign(body),
            "X-Razorpay-Event-Id": "evt_router_valid",
        }
        async with client as c:
            response = await c.post("/webhooks/razorpay", content=body, headers=headers)
        assert response.status_code == 200
        assert response.json() == {"status": "received"}

    async def test_replayed_event_id_returns_200_duplicate(self, client: AsyncClient) -> None:
        body = json.dumps({"event": "payment.failed"}).encode()
        headers = {
            "X-Razorpay-Signature": sign(body),
            "X-Razorpay-Event-Id": "evt_router_dup",
        }
        async with client as c:
            first = await c.post("/webhooks/razorpay", content=body, headers=headers)
            second = await c.post("/webhooks/razorpay", content=body, headers=headers)
        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json() == {"status": "duplicate"}

    async def test_invalid_signature_returns_401(self, client: AsyncClient) -> None:
        body = json.dumps({"event": "payment.failed"}).encode()
        headers = {
            "X-Razorpay-Signature": "0" * 64,
            "X-Razorpay-Event-Id": "evt_router_bad_sig",
        }
        async with client as c:
            response = await c.post("/webhooks/razorpay", content=body, headers=headers)
        assert response.status_code == 401
        assert response.json() == {"status": "invalid_signature"}

    async def test_missing_signature_header_returns_401(self, client: AsyncClient) -> None:
        body = json.dumps({"event": "payment.failed"}).encode()
        async with client as c:
            response = await c.post(
                "/webhooks/razorpay",
                content=body,
                headers={"X-Razorpay-Event-Id": "evt_router_no_sig"},
            )
        assert response.status_code == 401


class TestDependencyWiring:
    async def test_get_event_repository_returns_a_repository_wired_with_exceptions(self) -> None:
        # Every HTTP-level test above overrides this dependency entirely, so
        # its real body is otherwise never exercised. The exceptions repo
        # being wired (not None) is what makes storage-exhaustion filing
        # work at all in production — see api/db/repositories.py's
        # _file_exhaustion.
        db = AsyncMongoMockClient()["salvage_router_wiring_test"]
        repo = get_event_repository(db)
        assert repo._exceptions is not None


class TestStorageFailureAtTheHttpLayer:
    async def test_an_exhausted_append_returns_500_with_detail(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", FIXTURE_SECRET)
        settings.cache_clear()

        db = AsyncMongoMockClient()["salvage_router_storage_failure_test"]
        await apply_indexes(db)
        repos = Repositories(db)

        async def always_contended(_: dict[str, object]) -> None:
            # keyPattern, not a string match — see test_repositories.py's
            # matching comment and api/db/repositories.py's _is_chain_race.
            raise DuplicateKeyError(
                "E11000 duplicate key error ... index: uq_events_run_prev_hash",
                details={"keyPattern": {"run_id": 1, "prev_hash": 1}},
            )

        repos.events._c.insert_one = always_contended  # type: ignore[method-assign]

        app = FastAPI()
        app.include_router(ingest_router)
        app.dependency_overrides[get_event_repository] = lambda: repos.events

        body = json.dumps({"event": "payment.failed"}).encode()
        headers = {
            "X-Razorpay-Signature": sign(body),
            "X-Razorpay-Event-Id": "evt_router_storage_failed",
        }
        transport = ASGITransport(app=app)
        try:
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                response = await c.post("/webhooks/razorpay", content=body, headers=headers)
        finally:
            settings.cache_clear()

        assert response.status_code == 500
        payload = response.json()
        assert payload["status"] == "storage_failed"
        assert "detail" in payload
