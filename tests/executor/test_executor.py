"""execute_action(): dry-run, kill switch, insert-first idempotency
(DuplicateKeyError -> prior result / in-flight), boundary check.
"""

from __future__ import annotations

import datetime as dt

import pytest
from api.config import settings
from api.db.repositories import Repositories
from api.executor import arm, disarm, is_armed
from api.executor.executor import execute_action
from api.policy.types import Action


class TestDryRun:
    async def test_dry_run_never_calls_the_razorpay_client(self, repos: Repositories) -> None:
        result = await execute_action(
            case_id="case1",
            decision_id="dec1",
            action=Action.RETRY_NOW,
            payload={"foo": "bar"},
            idempotency_key="case1:dec1:RETRY_NOW",
            dry_run=True,
            audit=repos.audit,
            kill_switch_engaged=False,
        )
        assert result.outcome == "executed"
        assert result.dry_run is True
        assert result.response is not None
        assert result.response["simulated"] is True

    async def test_pre_and_post_audit_rows_are_both_written(self, repos: Repositories) -> None:
        await execute_action(
            case_id="case1",
            decision_id="dec1",
            action=Action.RETRY_NOW,
            payload={},
            idempotency_key="k1",
            dry_run=True,
            audit=repos.audit,
            kill_switch_engaged=False,
        )
        rows = await repos.audit.by_case("case1")
        phases = {r["phase"] for r in rows}
        assert phases == {"pre", "post"}


class TestKillSwitch:
    async def test_kill_switch_blocks_before_any_audit_write(self, repos: Repositories) -> None:
        result = await execute_action(
            case_id="case1",
            decision_id="dec1",
            action=Action.RETRY_NOW,
            payload={},
            idempotency_key="k-blocked",
            dry_run=True,
            audit=repos.audit,
            kill_switch_engaged=True,
        )
        assert result.outcome == "blocked_kill_switch"
        rows = await repos.audit.by_case("case1")
        assert rows == []


class TestIdempotency:
    async def test_duplicate_key_after_full_dispatch_returns_prior_result_never_redispatches(
        self, repos: Repositories
    ) -> None:
        first = await execute_action(
            case_id="case1",
            decision_id="dec1",
            action=Action.RETRY_NOW,
            payload={"n": 1},
            idempotency_key="k-dup",
            dry_run=True,
            audit=repos.audit,
            kill_switch_engaged=False,
        )
        second = await execute_action(
            case_id="case1",
            decision_id="dec1",
            action=Action.RETRY_NOW,
            payload={"n": 2},  # different payload — must NOT be re-dispatched
            idempotency_key="k-dup",
            dry_run=True,
            audit=repos.audit,
            kill_switch_engaged=False,
        )
        assert first.outcome == "executed"
        assert second.outcome == "already_executed"
        assert second.response == first.response
        rows = await repos.audit.by_case("case1")
        assert (
            len([r for r in rows if r["idempotency_key"] == "k-dup"]) == 2
        )  # one pre, one post — not four

    async def test_pre_row_with_no_post_row_reports_in_flight_not_never_attempted(
        self, repos: Repositories
    ) -> None:
        # Simulate a dispatch that died between the pre-audit insert and the
        # post-audit insert — insert the pre-row directly, bypassing the
        # executor, then call execute_action with the same key.
        await repos.audit.insert(
            {
                "case_id": "case1",
                "decision_id": "dec1",
                "ts": dt.datetime.now(dt.UTC),
                "actor": "system",
                "phase": "pre",
                "action": Action.RETRY_NOW.value,
                "idempotency_key": "k-inflight",
                "request": {},
                "response": None,
                "dry_run": True,
                "clause_ref": None,
                "note": None,
            }
        )
        result = await execute_action(
            case_id="case1",
            decision_id="dec1",
            action=Action.RETRY_NOW,
            payload={},
            idempotency_key="k-inflight",
            dry_run=True,
            audit=repos.audit,
            kill_switch_engaged=False,
        )
        assert result.outcome == "in_flight"

    async def test_different_actions_on_the_same_case_get_independent_keys(
        self, repos: Repositories
    ) -> None:
        a = await execute_action(
            case_id="case1",
            decision_id="dec1",
            action=Action.RETRY_NOW,
            payload={},
            idempotency_key="case1:dec1:RETRY_NOW",
            dry_run=True,
            audit=repos.audit,
            kill_switch_engaged=False,
        )
        b = await execute_action(
            case_id="case1",
            decision_id="dec1",
            action=Action.SEND_PRE_DEBIT_NOTICE,
            payload={},
            idempotency_key="case1:dec1:SEND_PRE_DEBIT_NOTICE",
            dry_run=True,
            audit=repos.audit,
            kill_switch_engaged=False,
        )
        assert a.outcome == "executed"
        assert b.outcome == "executed"


class TestBoundary:
    async def test_action_not_in_the_bounded_catalog_is_rejected(self, repos: Repositories) -> None:
        # ValueError, not a bare assert (compliance-auditor F9): `assert` is
        # stripped under `python -O`, which would silently remove the only
        # check standing between arbitrary input and the action catalog.
        with pytest.raises(ValueError, match="bounded action catalog"):
            await execute_action(
                case_id="case1",
                decision_id="dec1",
                action="NOT_A_REAL_ACTION",  # type: ignore[arg-type]
                payload={},
                idempotency_key="k-bad",
                dry_run=True,
                audit=repos.audit,
                kill_switch_engaged=False,
            )


class TestArming:
    """Invariant 8: "Going live requires an explicit arm call with a confirm
    token." `DRY_RUN=false` alone must never be enough (compliance-auditor F4).
    """

    def setup_method(self) -> None:
        disarm()

    def teardown_method(self) -> None:
        disarm()

    async def test_dry_run_false_without_arming_is_blocked_not_armed(
        self, repos: Repositories
    ) -> None:
        result = await execute_action(
            case_id="case1",
            decision_id="dec1",
            action=Action.RETRY_NOW,
            payload={},
            idempotency_key="k-unarmed",
            dry_run=False,
            audit=repos.audit,
            kill_switch_engaged=False,
        )
        assert result.outcome == "blocked_not_armed"
        rows = await repos.audit.by_case("case1")
        assert rows == []  # refused before any audit write, same as the kill switch

    async def test_dry_run_true_never_needs_arming(self, repos: Repositories) -> None:
        result = await execute_action(
            case_id="case1",
            decision_id="dec1",
            action=Action.RETRY_NOW,
            payload={},
            idempotency_key="k-dryrun-unarmed",
            dry_run=True,
            audit=repos.audit,
            kill_switch_engaged=False,
        )
        assert result.outcome == "executed"

    def test_arm_rejects_the_wrong_token(self) -> None:
        settings.cache_clear()
        try:
            assert arm("definitely-not-the-configured-token") is False
            assert is_armed() is False
        finally:
            settings.cache_clear()

    def test_arm_accepts_the_configured_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings.cache_clear()
        monkeypatch.setenv("ARM_CONFIRM_TOKEN", "test-token-123")
        try:
            assert arm("test-token-123") is True
            assert is_armed() is True
        finally:
            settings.cache_clear()

    def test_empty_configured_token_can_never_be_armed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings.cache_clear()
        monkeypatch.setenv("ARM_CONFIRM_TOKEN", "")
        try:
            assert arm("") is False  # an unset token must never be satisfied by an empty argument
            assert is_armed() is False
        finally:
            settings.cache_clear()

    def test_disarm_reverts_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings.cache_clear()
        monkeypatch.setenv("ARM_CONFIRM_TOKEN", "test-token-123")
        try:
            arm("test-token-123")
            assert is_armed() is True
            disarm()
            assert is_armed() is False
        finally:
            settings.cache_clear()


class TestAuditRowsCarryTheDecisionsClock:
    """The gate chain re-derives its whole context — G4_BUDGET's 14-day
    window, G5_COOLDOWN's 48 hours, G2_PREDEBIT's 24-hour notice lead — from
    the `ts` on these rows. If the executor stamps them with the wall clock
    while the gates reasoned about some other instant, the case history the
    next decision cycle reads is one that never happened.

    Found on day 5 while building the batch harness, which drives cases on a
    simulated clock: wall-clock stamps made every prior attempt look ancient,
    so budgets and cooldowns never fired and arm B was being credited with
    attempts compliance would have refused.
    """

    async def test_both_rows_are_stamped_with_the_supplied_clock(self, repos) -> None:  # type: ignore[no-untyped-def]
        decided_at = dt.datetime(2026, 9, 15, 4, 30, tzinfo=dt.UTC)

        await execute_action(
            case_id="case1",
            decision_id="dec1",
            action=Action.NUDGE,
            payload={},
            idempotency_key="case1:pay1:NUDGE",
            dry_run=True,
            audit=repos.audit,
            kill_switch_engaged=False,
            now_utc=decided_at,
        )

        rows = await repos.audit.by_case("case1")
        assert len(rows) == 2
        for row in rows:
            stamped = row["ts"]
            stamped = stamped if stamped.tzinfo else stamped.replace(tzinfo=dt.UTC)
            assert stamped == decided_at

    async def test_omitting_the_clock_still_stamps_wall_time(self, repos) -> None:  # type: ignore[no-untyped-def]
        # Live traffic passes nothing and must keep working exactly as before.
        before = dt.datetime.now(dt.UTC)
        await execute_action(
            case_id="case2",
            decision_id="dec2",
            action=Action.NUDGE,
            payload={},
            idempotency_key="case2:pay2:NUDGE",
            dry_run=True,
            audit=repos.audit,
            kill_switch_engaged=False,
        )
        after = dt.datetime.now(dt.UTC)

        rows = await repos.audit.by_case("case2")
        stamped = rows[0]["ts"]
        stamped = stamped if stamped.tzinfo else stamped.replace(tzinfo=dt.UTC)
        # BSON stores milliseconds, so a stamp taken microseconds after
        # `before` can round-trip to just below it — the same truncation
        # day 2's hash-chain entry in NOTES.md already logged. One
        # millisecond of slack on each end, not a loosened assertion.
        assert before - dt.timedelta(milliseconds=1) <= stamped
        assert stamped <= after + dt.timedelta(milliseconds=1)
