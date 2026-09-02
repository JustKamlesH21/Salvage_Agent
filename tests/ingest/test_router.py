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
from api.ingest.router import get_event_repository, get_repositories
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
    # The route now runs the decision pipeline for payment.failed (day 5), and
    # a decision cycle calls the LLM unless this is set. No test in this file
    # is about the LLM, and none of them may make a network call to find that
    # out — the deterministic path is the one under test here.
    monkeypatch.setenv("DISABLE_LLM", "true")
    settings.cache_clear()

    db = AsyncMongoMockClient()["salvage_router_test"]
    await apply_indexes(db)
    repos = Repositories(db)

    app = FastAPI()
    app.include_router(ingest_router)
    app.dependency_overrides[get_event_repository] = lambda: repos.events
    app.dependency_overrides[get_repositories] = lambda: repos

    transport = ASGITransport(app=app)
    try:
        client = AsyncClient(transport=transport, base_url="http://test")
        client.repos = repos  # type: ignore[attr-defined]  # tests assert on what the pipeline wrote
        yield client
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
        app.dependency_overrides[get_repositories] = lambda: repos

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


def _payment_failed_body(payment_id: str, *, reason: str = "insufficient_funds") -> bytes:
    """The shape day 1's real captures carry (NOTES.md's fixture log), trimmed
    to the fields the pipeline actually reads.
    """
    return json.dumps(
        {
            "event": "payment.failed",
            "payload": {
                "payment": {
                    "entity": {
                        "id": payment_id,
                        "order_id": f"order_{payment_id}",
                        "invoice_id": None,
                        "amount": 120000,
                        "currency": "INR",
                        "method": "card",
                        "error_code": "BAD_REQUEST_ERROR",
                        "error_reason": reason,
                        "error_description": "insufficient balance",
                    }
                }
            },
        }
    ).encode()


class TestPipelineIsActuallyWired:
    """Ingest (day 2) and the decision pipeline (day 4) were built in separate
    phases and never connected: a real webhook produced a hash-chained event
    row and nothing else — no case, no decision, no audit. Found on day 5 when
    the frontend needed something real to render. These tests are what keep
    them connected.
    """

    async def test_a_payment_failed_webhook_opens_a_case_and_records_a_decision(
        self, client: AsyncClient
    ) -> None:
        body = _payment_failed_body("pay_wired_1")
        headers = {
            "X-Razorpay-Signature": sign(body),
            "X-Razorpay-Event-Id": "evt_wired_1",
        }
        repos = client.repos  # type: ignore[attr-defined]
        async with client as c:
            response = await c.post("/webhooks/razorpay", content=body, headers=headers)

        assert response.status_code == 200
        cases = await repos.cases.find({})
        assert len(cases) == 1
        assert cases[0]["payment_id"] == "pay_wired_1"
        # Live partition, not an eval run's.
        assert cases[0]["run_id"] is None

        decisions = await repos.decisions.find({})
        assert len(decisions) == 1
        assert decisions[0]["case_id"] == cases[0]["_id"]
        assert decisions[0]["run_id"] is None
        # The full chain ran, not just the first blocker (11 gates minimum).
        assert len(decisions[0]["gate_results"]) >= 11

    async def test_a_replayed_event_does_not_produce_a_second_decision(
        self, client: AsyncClient
    ) -> None:
        # FR-02's acceptance, at the HTTP layer and through the pipeline:
        # "same rz_event_id replayed produces one case and one set of actions".
        body = _payment_failed_body("pay_wired_2")
        headers = {
            "X-Razorpay-Signature": sign(body),
            "X-Razorpay-Event-Id": "evt_wired_2",
        }
        repos = client.repos  # type: ignore[attr-defined]
        async with client as c:
            first = await c.post("/webhooks/razorpay", content=body, headers=headers)
            second = await c.post("/webhooks/razorpay", content=body, headers=headers)
            third = await c.post("/webhooks/razorpay", content=body, headers=headers)

        assert first.json()["status"] == "received"
        assert second.json()["status"] == "duplicate"
        assert third.json()["status"] == "duplicate"
        assert len(await repos.cases.find({})) == 1
        assert len(await repos.decisions.find({})) == 1

    async def test_an_event_type_the_pipeline_does_not_handle_is_stored_but_not_decided(
        self, client: AsyncClient
    ) -> None:
        # Only payment.failed is in scope (PRD §2). The other five event types
        # are still captured and hash-chained — the audit trail does not have
        # holes just because nothing acts on a row yet.
        body = json.dumps({"event": "subscription.halted", "payload": {}}).encode()
        headers = {
            "X-Razorpay-Signature": sign(body),
            "X-Razorpay-Event-Id": "evt_wired_halted",
        }
        repos = client.repos  # type: ignore[attr-defined]
        async with client as c:
            response = await c.post("/webhooks/razorpay", content=body, headers=headers)

        assert response.status_code == 200
        assert await repos.events.count() == 1
        assert await repos.cases.find({}) == []
        assert await repos.decisions.find({}) == []

    async def test_an_unparseable_body_is_stored_and_decided_on_by_nothing(
        self, client: AsyncClient
    ) -> None:
        # ingest stores a best-effort text form when the body will not parse;
        # `payload` is then a str, not a dict. The pipeline must not try to
        # read `.get("payload")` off it.
        body = b"{not valid json, but validly signed"
        headers = {
            "X-Razorpay-Signature": sign(body),
            "X-Razorpay-Event-Id": "evt_wired_unparseable",
        }
        repos = client.repos  # type: ignore[attr-defined]
        async with client as c:
            response = await c.post("/webhooks/razorpay", content=body, headers=headers)

        assert response.status_code == 200
        assert await repos.cases.find({}) == []
        assert await repos.exceptions.find({}) == []  # not an error — nothing to decide

    async def test_a_pipeline_failure_lands_on_the_exception_desk_not_in_silence(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The ack has already been sent by the time the pipeline runs, so a
        # raise here can reach no caller. It must not evaporate.
        async def exploding_pipeline(*_args: object, **_kwargs: object) -> dict[str, object]:
            raise RuntimeError("groq is down")

        monkeypatch.setattr("api.ingest.router.process_payment_failed", exploding_pipeline)

        body = _payment_failed_body("pay_wired_boom")
        headers = {
            "X-Razorpay-Signature": sign(body),
            "X-Razorpay-Event-Id": "evt_wired_boom",
        }
        repos = client.repos  # type: ignore[attr-defined]
        async with client as c:
            response = await c.post("/webhooks/razorpay", content=body, headers=headers)

        # The event was stored, so the ack is still a 200 — the status code
        # speaks for storage, not for what the decision layer made of it.
        assert response.status_code == 200
        filed = await repos.exceptions.find({})
        assert len(filed) == 1
        assert filed[0]["reason_code"] == "PIPELINE_FAILED"
        assert "groq is down" in filed[0]["detail"]["error"]
        assert filed[0]["detail"]["rz_event_id"] == "evt_wired_boom"
