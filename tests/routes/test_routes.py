"""The read routes the frontend renders, and the two controls.

The one that matters most is `TestKillSwitchActuallyStops`: a kill switch that
changes what the status bar says without changing what the system does would
be worse than not having one, because it would be believed.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import AsyncIterator

import pytest
from api.config import settings
from api.db.collections import apply_indexes
from api.db.repositories import Repositories
from api.executor import killswitch
from api.main import app
from api.pipeline import process_payment_failed
from api.routes.cases import get_repositories
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("DISABLE_LLM", "true")
    monkeypatch.setenv("ARM_CONFIRM_TOKEN", "test-token")
    settings.cache_clear()
    killswitch.release()

    db = AsyncMongoMockClient()["salvage_routes_test"]
    await apply_indexes(db)
    repos = Repositories(db)

    # api.main's lifespan connects to the real MONGO_URI, so the transport is
    # built without it — the dependency override is what supplies storage.
    app.dependency_overrides[get_repositories] = lambda: repos
    transport = ASGITransport(app=app)
    try:
        c = AsyncClient(transport=transport, base_url="http://test")
        c.repos = repos  # type: ignore[attr-defined]
        yield c
    finally:
        app.dependency_overrides.clear()
        killswitch.release()
        settings.cache_clear()


def payload(payment_id: str, *, amount: int = 120000) -> dict[str, object]:
    return {
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "subscription_id": f"sub_{payment_id}",
                    "customer_id": f"cust_{payment_id}",
                    "amount": amount,
                    "currency": "INR",
                    "method": "card",
                    "error_code": "BAD_REQUEST_ERROR",
                    "error_reason": "insufficient_funds",
                    "error_description": "insufficient balance",
                }
            }
        },
    }


async def seed_case(repos: Repositories, payment_id: str, **kwargs: object) -> dict[str, object]:
    return await process_payment_failed(
        payload(payment_id, **kwargs),  # type: ignore[arg-type]
        repos,
        now_utc=dt.datetime(2026, 9, 10, 6, 0, tzinfo=dt.UTC),
        dry_run=True,
        kill_switch_engaged=False,
        disable_llm=True,
    )


class TestListCases:
    async def test_lists_live_cases_with_counters(self, client: AsyncClient) -> None:
        repos = client.repos  # type: ignore[attr-defined]
        await seed_case(repos, "pay_list_1")
        await seed_case(repos, "pay_list_2", amount=2_000_000)

        async with client as c:
            response = await c.get("/cases")

        assert response.status_code == 200
        body = response.json()
        assert len(body["cases"]) == 2
        assert body["counters"]["total_cases"] == 2
        assert body["counters"]["at_risk_paise"] == 120000 + 2_000_000
        # Restraint is a first-class counter, not a footnote (SPEC §6.3).
        assert "blocked_by_compliance" in body["counters"]

    async def test_eval_run_cases_do_not_appear_on_the_live_deck(self, client: AsyncClient) -> None:
        repos = client.repos  # type: ignore[attr-defined]
        await seed_case(repos, "pay_live")
        await process_payment_failed(
            payload("pay_batch"),
            repos,
            now_utc=dt.datetime(2026, 9, 10, 6, 0, tzinfo=dt.UTC),
            dry_run=True,
            kill_switch_engaged=False,
            disable_llm=True,
            run_id="eval_99",
        )

        async with client as c:
            live = (await c.get("/cases")).json()
            batch = (await c.get("/cases", params={"run_id": "eval_99"})).json()

        assert [c["subscription_id"] for c in live["cases"]] == ["sub_pay_live"]
        assert [c["subscription_id"] for c in batch["cases"]] == ["sub_pay_batch"]

    async def test_no_customer_identity_is_returned(self, client: AsyncClient) -> None:
        repos = client.repos  # type: ignore[attr-defined]
        await seed_case(repos, "pay_pii")

        async with client as c:
            body = (await c.get("/cases")).json()

        rendered = json.dumps(body)
        assert "cust_" in body["cases"][0]["customer_ref"]
        # The raw customer id is a Razorpay identifier, not a name — but the
        # UI has no use for it either, and the reference is what it shows.
        assert body["cases"][0].get("contact") is None
        assert "email" not in rendered


class TestCaseDetail:
    async def test_returns_the_decision_tape_joined_to_audit(self, client: AsyncClient) -> None:
        repos = client.repos  # type: ignore[attr-defined]
        decision = await seed_case(repos, "pay_detail")
        case_id = str(decision["case_id"])

        async with client as c:
            response = await c.get(f"/cases/{case_id}")

        assert response.status_code == 200
        body = response.json()
        assert body["case"]["customer_ref"].startswith("cust_")
        assert len(body["decisions"]) == 1

        tape = body["decisions"][0]
        assert tape["decision_id"] == decision["decision_id"]
        assert len(tape["gate_results"]) >= 11
        assert {"gate", "verdict", "clause_ref", "detail", "evaluated_at"} <= set(
            tape["gate_results"][0]
        )
        # The join that day 4's review found structurally impossible.
        assert isinstance(tape["executed"], list)

    async def test_unknown_case_says_what_to_do(self, client: AsyncClient) -> None:
        async with client as c:
            response = await c.get("/cases/nope")
        assert response.status_code == 404
        assert "GET /cases" in response.json()["detail"]

    async def test_audit_rows_are_returned_unprojected(self, client: AsyncClient) -> None:
        repos = client.repos  # type: ignore[attr-defined]
        decision = await seed_case(repos, "pay_audit")

        async with client as c:
            body = (await c.get(f"/cases/{decision['case_id']}/audit")).json()

        assert body["audit"], "a decision that dispatched must leave audit rows"
        # Chain fields included: this is the screen a sceptic reads.
        assert "hash" in body["audit"][0]
        assert "prev_hash" in body["audit"][0]


class TestControl:
    async def test_status_reports_the_posture_and_that_live_dispatch_is_unavailable(
        self, client: AsyncClient
    ) -> None:
        async with client as c:
            body = (await c.get("/control/status")).json()
        assert body["dry_run"] is True
        assert body["armed"] is False
        assert body["live_dispatch_available"] is False
        assert "stub" in body["live_dispatch_note"]

    async def test_arming_requires_the_confirm_token(self, client: AsyncClient) -> None:
        async with client as c:
            wrong = (await c.post("/control/arm", json={"confirm_token": "guess"})).json()
            assert wrong["armed"] is False
            assert "ARM_CONFIRM_TOKEN" in wrong["error"]

            right = (await c.post("/control/arm", json={"confirm_token": "test-token"})).json()
            assert right["armed"] is True

            await c.post("/control/disarm")
            assert (await c.get("/control/status")).json()["armed"] is False


class TestKillSwitchActuallyStops:
    """FR-10: engaging it freezes pending actions. A switch that only changes
    what the status bar says would be worse than none, because it would be
    believed.
    """

    async def test_engaging_the_switch_stops_dispatch_not_just_the_display(
        self, client: AsyncClient
    ) -> None:
        repos = client.repos  # type: ignore[attr-defined]

        async with client as c:
            await c.post("/control/kill", json={"engaged": True})
            assert (await c.get("/control/status")).json()["kill_switch"] is True
            assert (await c.get("/health")).json()["kill_switch"] is True

            # The pipeline resolves the switch itself when the caller passes
            # nothing — the live webhook route's exact call shape.
            decision = await process_payment_failed(
                payload("pay_killed"),
                repos,
                now_utc=dt.datetime(2026, 9, 10, 6, 0, tzinfo=dt.UTC),
                dry_run=True,
                disable_llm=True,
            )

        gates = {g["gate"]: g["verdict"] for g in decision["gate_results"]}
        assert gates["G0_KILL"] == "BLOCK"
        executed = decision["execute_result"]
        assert executed is None or executed.outcome != "executed"

    async def test_releasing_the_switch_lets_work_through_again(self, client: AsyncClient) -> None:
        repos = client.repos  # type: ignore[attr-defined]
        async with client as c:
            await c.post("/control/kill", json={"engaged": True})
            await c.post("/control/kill", json={"engaged": False})
            assert (await c.get("/control/status")).json()["kill_switch"] is False

            decision = await process_payment_failed(
                payload("pay_released"),
                repos,
                now_utc=dt.datetime(2026, 9, 10, 6, 0, tzinfo=dt.UTC),
                dry_run=True,
                disable_llm=True,
            )

        gates = {g["gate"]: g["verdict"] for g in decision["gate_results"]}
        assert gates["G0_KILL"] == "ALLOW"


class TestRulebook:
    async def test_renders_every_gate_and_every_rule_with_its_citation(
        self, client: AsyncClient
    ) -> None:
        async with client as c:
            body = (await c.get("/rulebook")).json()

        assert len(body["gates"]) == 11
        for gate in body["gates"]:
            assert gate["clause_ref"]
            assert gate["rule_text"]
        for rule in body["policy_rules"]:
            assert rule["because"]
            assert rule["cites"]
        assert body["remediation"]
        assert body["max_remediation_hops"] == 3


class TestExceptions:
    async def test_returns_the_desk(self, client: AsyncClient) -> None:
        repos = client.repos  # type: ignore[attr-defined]
        await repos.exceptions.insert(
            {
                "case_id": None,
                "reason_code": "PIPELINE_FAILED",
                "detail": {"error": "boom"},
                "ts": dt.datetime.now(dt.UTC),
                "resolved_by": None,
            }
        )
        async with client as c:
            body = (await c.get("/exceptions")).json()
        assert body["count"] == 1
        assert body["exceptions"][0]["reason_code"] == "PIPELINE_FAILED"
