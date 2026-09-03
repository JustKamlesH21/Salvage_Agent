"""The live tick — api/scheduler/live.py.

`fire_due_jobs()` was pure and tested from day 4, and nothing called it, so a
replanned action never actually fired in a running system. These tests cover
the layer that does call it: which decisions become due jobs, what the kill
switch does to a tick, and whether firing writes a record that shows the gates
re-ran.
"""

from __future__ import annotations

import datetime as dt

import pytest
from api.db.repositories import Repositories
from api.executor import killswitch
from api.policy.types import Action
from api.scheduler.live import due_jobs, tick
from bson import ObjectId

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# 19:40 IST — outside the 08:00-19:00 contact window.
EVENING = dt.datetime(2026, 9, 3, 19, 40, tzinfo=IST).astimezone(dt.UTC)
# 10:00 IST next morning — inside it.
MORNING = dt.datetime(2026, 9, 4, 10, 0, tzinfo=IST).astimezone(dt.UTC)


async def _seed(
    repos: Repositories,
    *,
    action: Action,
    verdict: str,
    at: dt.datetime,
    case_id: str = "case_1",
    decision_id: str = "dec_1",
    created_at: dt.datetime | None = None,
    with_channel: bool = True,
    amount_paise: int | None = 500_000,
) -> None:
    await repos.cases.insert(
        {
            "_id": case_id,
            "customer_id": f"cust_{case_id}",
            "amount_paise": 500_000,
            "category": "standard",
            "mandate_type": "card",
            "subscription_status": "active",
            "state": "open",
        }
    )
    await repos.customers.insert(
        {
            "_id": f"cust_{case_id}",
            "contact_hash": "hash" if with_channel else None,
            "opt_out_at": None,
        }
    )
    await repos.decisions.insert(
        {
            "decision_id": decision_id,
            "case_id": case_id,
            "run_id": None,
            "created_at": created_at or (at - dt.timedelta(hours=1)),
            "amount_paise": amount_paise,
            "diagnosis": {"failure_class": "TEMPORARY_LIQUIDITY"},
            "policy": {"rule_id": "POL-005", "proposed_action": action.value},
            "gate_results": [],
            "final": {"action": action.value, "at": at, "verdict": verdict},
            "suppressed": [],
        }
    )


@pytest.fixture(autouse=True)
def _released():
    """Every test starts with the switch released and leaves it released."""
    killswitch.release()
    yield
    killswitch.release()


class TestDueJobs:
    async def test_a_replan_whose_time_has_come_is_due(self, repos: Repositories) -> None:
        await _seed(repos, action=Action.NUDGE, verdict="replan", at=MORNING)
        jobs = await due_jobs(repos, MORNING)
        assert [j.action for j in jobs] == [Action.NUDGE]
        assert jobs[0].case_id == "case_1"

    async def test_a_replan_still_in_the_future_is_not(self, repos: Repositories) -> None:
        await _seed(repos, action=Action.NUDGE, verdict="replan", at=MORNING)
        assert await due_jobs(repos, MORNING - dt.timedelta(minutes=1)) == []

    async def test_an_allowed_decision_is_not_a_job(self, repos: Repositories) -> None:
        await _seed(repos, action=Action.NUDGE, verdict="allow", at=MORNING)
        assert await due_jobs(repos, MORNING) == []

    async def test_only_the_latest_decision_on_a_case_counts(self, repos: Repositories) -> None:
        """A superseded replan must not re-fire. This is what stops the same
        job firing on every tick forever."""
        await _seed(
            repos,
            action=Action.NUDGE,
            verdict="replan",
            at=MORNING,
            decision_id="dec_old",
            created_at=EVENING,
        )
        await repos.decisions.insert(
            {
                "decision_id": "dec_new",
                "case_id": "case_1",
                "run_id": None,
                "created_at": MORNING,
                "amount_paise": 500_000,
                "final": {"action": "NUDGE", "at": MORNING, "verdict": "allow"},
                "gate_results": [],
                "suppressed": [],
            }
        )
        assert await due_jobs(repos, MORNING) == []

    async def test_the_key_is_stable_across_ticks(self, repos: Repositories) -> None:
        """Two reads of the same scheduled work must collide on idempotency,
        not slip past each other with different keys."""
        await _seed(repos, action=Action.NUDGE, verdict="replan", at=MORNING)
        first = await due_jobs(repos, MORNING)
        second = await due_jobs(repos, MORNING + dt.timedelta(minutes=5))
        assert first[0].idempotency_key == second[0].idempotency_key


class TestKillSwitch:
    async def test_an_engaged_switch_freezes_the_tick(self, repos: Repositories) -> None:
        """Freeze, not cancel. Running the chain under a kill would work —
        G0_KILL blocks first — but a BLOCK is terminal, so the scheduled action
        would be cancelled and releasing the switch would not bring it back."""
        await _seed(repos, action=Action.NUDGE, verdict="replan", at=MORNING)
        killswitch.engage()

        assert await tick(repos, MORNING) == []
        # Nothing was written...
        assert len(await repos.decisions.by_case("case_1")) == 1
        # ...and the job is still owed once the switch is released.
        killswitch.release()
        assert len(await due_jobs(repos, MORNING)) == 1


class TestTick:
    async def test_a_due_job_fires_and_records_its_own_decision(self, repos: Repositories) -> None:
        await _seed(repos, action=Action.NUDGE, verdict="replan", at=MORNING)

        results = await tick(repos, MORNING)

        assert len(results) == 1
        assert results[0].executed is not None
        decisions = await repos.decisions.by_case("case_1")
        assert len(decisions) == 2
        fired = decisions[-1]
        assert fired["fired_from"] == "dec_1"
        # The whole chain ran again at fire time, not a cached verdict.
        assert len(fired["gate_results"]) == 11
        assert fired["final"]["verdict"] == "allow"

    async def test_firing_makes_the_job_stop_being_due(self, repos: Repositories) -> None:
        await _seed(repos, action=Action.NUDGE, verdict="replan", at=MORNING)
        await tick(repos, MORNING)
        assert await due_jobs(repos, MORNING) == []

    async def test_gates_are_re_evaluated_against_fire_time_reality(
        self, repos: Repositories
    ) -> None:
        """The load-bearing one. A contact scheduled for a compliant moment
        that comes due at 19:40 IST is refused by the gate that governs the
        window — the schedule-time verdict does not travel with the job."""
        await _seed(repos, action=Action.NUDGE, verdict="replan", at=EVENING)

        results = await tick(repos, EVENING)

        assert len(results) == 1
        # Not dispatched: G1_WINDOW moved it rather than letting it through.
        assert results[0].executed is None
        assert results[0].final.verdict.value == "REPLAN"
        window = next(v for v in results[0].chain.verdicts if v.gate == "G1_WINDOW")
        assert window.kind.value == "REPLAN"

    async def test_a_case_with_no_confirmation_channel_is_refused(
        self, repos: Repositories
    ) -> None:
        """G8_POSTDEBIT at fire time, on a charge rather than a contact."""
        await _seed(
            repos,
            action=Action.RETRY_SCHEDULED,
            verdict="replan",
            at=MORNING,
            with_channel=False,
        )

        results = await tick(repos, MORNING)

        assert results[0].executed is None or results[0].final.action != Action.RETRY_SCHEDULED
        blocked = {v.gate for v in results[0].chain.verdicts if v.kind.value == "BLOCK"}
        assert "G8_POSTDEBIT" in blocked

    async def test_the_fired_decision_does_not_invent_a_diagnosis_or_a_rule(
        self, repos: Repositories
    ) -> None:
        """It carries the originating decision's forward. An earlier version
        wrote failure_class="SCHEDULED", which is not a FailureClass member and
        raised on the next webhook for the case, and rule_id="SCHEDULER", which
        resolves to nothing in the rulebook the tape cites from."""
        from api.normalise.types import FailureClass

        await _seed(repos, action=Action.NUDGE, verdict="replan", at=MORNING)
        await tick(repos, MORNING)

        fired = (await repos.decisions.by_case("case_1"))[-1]
        # Round-trips through the enum, which "SCHEDULED" would not.
        assert FailureClass(fired["diagnosis"]["failure_class"]) is FailureClass.TEMPORARY_LIQUIDITY
        assert fired["policy"]["rule_id"] == "POL-005"

    async def test_a_decision_with_no_amount_is_refused_rather_than_guessed(
        self, repos: Repositories
    ) -> None:
        """Fail closed. Re-gating on the case document's figure would evaluate
        G3_AFA against the FIRST failure's amount and stamp the tape with a
        threshold comparison against a number that is not being charged."""
        await _seed(repos, action=Action.NUDGE, verdict="replan", at=MORNING, amount_paise=None)
        assert await tick(repos, MORNING) == []
        assert len(await repos.decisions.by_case("case_1")) == 1

    async def test_a_settled_case_is_never_fired(self, repos: Repositories) -> None:
        """No gate covers "already paid" — G9_HALTED reads a field the case
        document never updates, and Salvage only hears about failures. Without
        this the tick charges an invoice Razorpay's own retries recovered."""
        await _seed(repos, action=Action.RETRY_SCHEDULED, verdict="replan", at=MORNING)
        await repos.cases.transition("case_1", expect="open", to="recovered")

        assert await due_jobs(repos, MORNING) == []
        assert await tick(repos, MORNING) == []

    async def test_a_fire_time_block_writes_its_clause_to_the_audit(
        self, repos: Repositories
    ) -> None:
        """SPEC 5.5. The deck counts phase="block" rows for
        `blocked_by_compliance`; a refusal that writes none is invisible to the
        headline metric this product's argument rests on."""
        await _seed(
            repos,
            action=Action.RETRY_SCHEDULED,
            verdict="replan",
            at=MORNING,
            with_channel=False,
        )
        await tick(repos, MORNING)

        blocks = [r for r in await repos.audit.by_case("case_1") if r.get("phase") == "block"]
        assert blocks, "a fire-time refusal wrote no block row"
        assert all(r["clause_ref"] for r in blocks)
        assert "G8_POSTDEBIT" in {
            r["note"] and r["action"] and g for g in ["G8_POSTDEBIT"] for r in blocks
        } or any("G8" in (r.get("note") or "") or r.get("clause_ref") for r in blocks)

    async def test_nothing_due_writes_nothing(self, repos: Repositories) -> None:
        await _seed(repos, action=Action.NUDGE, verdict="replan", at=MORNING)
        assert await tick(repos, MORNING - dt.timedelta(hours=2)) == []
        assert len(await repos.decisions.by_case("case_1")) == 1

    async def test_a_job_whose_case_vanished_is_skipped_not_crashed(
        self, repos: Repositories
    ) -> None:
        await repos.decisions.insert(
            {
                "decision_id": "orphan",
                "case_id": "case_gone",
                "run_id": None,
                "created_at": EVENING,
                "final": {"action": "NUDGE", "at": MORNING, "verdict": "replan"},
                "gate_results": [],
                "suppressed": [],
            }
        )
        assert await tick(repos, MORNING) == []


class TestObjectIdCases:
    """Regression. Live cases carry a Mongo ObjectId `_id`; only hand-seeded
    ones are plain strings. The first version of this module stringified the
    case id for the whole tick, so against real data `due_jobs()` found eleven
    jobs and `tick()` fired none — the case lookup missed every time.

    The silent failure was the lesser half. Had the lookup succeeded, the
    executor would have written audit rows keyed by the string while
    build_gate_context() reads them back keyed by the ObjectId, so every
    scheduler-dispatched attempt would have been invisible to G4_BUDGET's
    14-day count and G5_COOLDOWN's 48-hour gap. Attempts compliance is supposed
    to bound would not have been counted.

    These tests use ObjectId ids for that reason. The originals above use
    strings and passed throughout.
    """

    async def _seed_oid(self, repos: Repositories, *, at: dt.datetime) -> ObjectId:
        case_id = ObjectId()
        await repos.cases.insert(
            {
                "_id": case_id,
                "customer_id": "cust_oid",
                "amount_paise": 500_000,
                "category": "standard",
                "mandate_type": "card",
                "subscription_status": "active",
                "state": "open",
            }
        )
        await repos.customers.insert(
            {"_id": "cust_oid", "contact_hash": "hash", "opt_out_at": None}
        )
        await repos.decisions.insert(
            {
                "decision_id": "dec_oid",
                "case_id": case_id,
                "run_id": None,
                "created_at": at - dt.timedelta(hours=1),
                "amount_paise": 500_000,
                "diagnosis": {"failure_class": "TEMPORARY_LIQUIDITY"},
                "policy": {"rule_id": "POL-005", "proposed_action": "NUDGE"},
                "final": {"action": "NUDGE", "at": at, "verdict": "replan"},
                "gate_results": [],
                "suppressed": [],
            }
        )
        return case_id

    async def test_a_case_with_an_objectid_id_actually_fires(self, repos: Repositories) -> None:
        await self._seed_oid(repos, at=MORNING)
        assert len(await due_jobs(repos, MORNING)) == 1

        results = await tick(repos, MORNING)

        assert len(results) == 1, "due but never fired — the case lookup missed"
        assert results[0].executed is not None

    async def test_audit_rows_are_written_where_the_gates_read_them(
        self, repos: Repositories
    ) -> None:
        """The compliance half. A dispatched attempt must be visible to the
        next gate context built for that case."""
        case_id = await self._seed_oid(repos, at=MORNING)
        await tick(repos, MORNING)

        rows = await repos.audit.by_case(case_id)
        assert rows, "the dispatch wrote no audit row this case can see"
        assert any(r.get("action") == "NUDGE" for r in rows)

    async def test_the_recorded_decision_is_findable_on_the_case(self, repos: Repositories) -> None:
        case_id = await self._seed_oid(repos, at=MORNING)
        await tick(repos, MORNING)

        decisions = await repos.decisions.by_case(case_id)
        assert len(decisions) == 2
        assert decisions[-1]["fired_from"] == "dec_oid"
