"""The chain runner: ordering, every-gate-always-runs, aggregate computation,
and the remediation map's conversion of a specific BLOCK into a compensating
action.
"""

from __future__ import annotations

import datetime as dt

import pytest
from api.gates.registry import (
    GATES_IN_ORDER,
    MAX_REMEDIATION_HOPS,
    REMEDIATION,
    ChainResult,
    evaluate_chain,
    resolve_final_action,
    resolve_with_remediation,
)
from api.gates.types import GateVerdict, VerdictKind
from api.policy.types import Action

from tests.gates.conftest import COMPLIANT_NOW_UTC, base_context


class TestResolveFinalActionMalformedChain:
    def test_replan_aggregate_with_no_replan_at_raises_rather_than_silently_allowing(self) -> None:
        # evaluate_chain() never produces this shape — a malformed ChainResult
        # constructed directly, proving the explicit check (compliance-auditor
        # F9's fix, recurring here per spec-guardian) actually fires instead
        # of being dead code.
        malformed = ChainResult(verdicts=(), aggregate=VerdictKind.REPLAN, replan_at=None)
        with pytest.raises(ValueError, match="replan_at is None"):
            resolve_final_action(malformed, Action.NUDGE, COMPLIANT_NOW_UTC)


class TestOrdering:
    def test_g0_kill_is_evaluated_first(self) -> None:
        assert GATES_IN_ORDER[0].__module__ == "api.gates.g0_kill"

    def test_all_eleven_gates_present_exactly_once(self) -> None:
        modules = [fn.__module__ for fn in GATES_IN_ORDER]
        expected = [
            f"api.gates.g{n}_{slug}"
            for n, slug in enumerate(
                [
                    "kill",
                    "window",
                    "predebit",
                    "afa",
                    "budget",
                    "cooldown",
                    "optout",
                    "thirdparty",
                    "postdebit",
                    "halted",
                    "idempotent",
                ]
            )
        ]
        assert modules == expected


class TestEveryGateAlwaysRuns:
    def test_all_eleven_verdicts_present_even_after_an_early_block(self) -> None:
        # G0_KILL blocks first; every other gate must still run so the
        # Decision Tape can show every verdict, not just the first blocker.
        ctx = base_context(kill_switch_engaged=True, opted_out=True, action=Action.RETRY_NOW)
        result = evaluate_chain(ctx)
        assert len(result.verdicts) == 11
        assert {v.gate for v in result.verdicts} == {
            "G0_KILL",
            "G1_WINDOW",
            "G2_PREDEBIT",
            "G3_AFA",
            "G4_BUDGET",
            "G5_COOLDOWN",
            "G6_OPTOUT",
            "G7_THIRDPARTY",
            "G8_POSTDEBIT",
            "G9_HALTED",
            "G10_IDEMPOTENT",
        }


class TestAggregate:
    def test_all_allow_aggregates_to_allow(self) -> None:
        ctx = base_context(action=Action.CLOSE_NO_ACTION)
        result = evaluate_chain(ctx)
        assert result.aggregate is VerdictKind.ALLOW

    def test_any_block_aggregates_to_block_even_with_replans_present(self) -> None:
        ctx = base_context(
            action=Action.RETRY_NOW,
            kill_switch_engaged=True,  # blocks
            amount_paise=20_000 * 100,  # also blocks (G3)
        )
        result = evaluate_chain(ctx)
        assert result.aggregate is VerdictKind.BLOCK
        assert len(result.blocking) >= 2

    def test_replan_with_no_block_aggregates_to_replan_latest_timestamp(self) -> None:
        # G1_WINDOW replans (outside hours, contact action) and nothing blocks.
        outside_hours = dt.datetime(2026, 9, 3, 14, 0, tzinfo=dt.UTC)  # 19:30 IST
        ctx = base_context(
            action=Action.NUDGE, now_utc=outside_hours, has_confirmation_channel=True
        )
        result = evaluate_chain(ctx)
        assert result.aggregate is VerdictKind.REPLAN
        assert result.replan_at is not None


class TestResolveFinalAction:
    def test_allow_dispatches_the_proposed_action_now(self) -> None:
        ctx = base_context(action=Action.CLOSE_NO_ACTION)
        chain = evaluate_chain(ctx)
        final = resolve_final_action(chain, Action.CLOSE_NO_ACTION, COMPLIANT_NOW_UTC)
        assert final.action is Action.CLOSE_NO_ACTION
        assert final.verdict is VerdictKind.ALLOW
        assert final.suppressed == ()

    def test_replan_keeps_the_proposed_action_reschedules_it(self) -> None:
        outside_hours = dt.datetime(2026, 9, 3, 14, 0, tzinfo=dt.UTC)
        ctx = base_context(action=Action.NUDGE, now_utc=outside_hours)
        chain = evaluate_chain(ctx)
        final = resolve_final_action(chain, Action.NUDGE, outside_hours)
        assert final.action is Action.NUDGE
        assert final.verdict is VerdictKind.REPLAN
        assert final.at == chain.replan_at
        assert final.suppressed == ()

    def test_g3_afa_block_remediates_to_create_afa_link(self) -> None:
        ctx = base_context(
            action=Action.RETRY_NOW,
            amount_paise=20_000 * 100,
            category="standard",
            pre_debit_notice_sent_at=COMPLIANT_NOW_UTC - dt.timedelta(hours=25),  # G2 clear
            has_confirmation_channel=True,  # G8 clear
        )
        chain = evaluate_chain(ctx)
        final = resolve_final_action(chain, Action.RETRY_NOW, COMPLIANT_NOW_UTC)
        assert final.action is REMEDIATION["G3_AFA"]
        assert final.action is Action.CREATE_AFA_LINK
        assert final.verdict is VerdictKind.BLOCK
        assert any(s["blocked_by"] == "G3_AFA" for s in final.suppressed)
        assert any(s["action"] == "RETRY_NOW" for s in final.suppressed)

    def test_g2_predebit_block_remediates_to_send_pre_debit_notice(self) -> None:
        ctx = base_context(
            action=Action.RETRY_NOW,
            amount_paise=5_000 * 100,  # under AFA threshold — G3 clear
            pre_debit_notice_sent_at=None,  # G2 blocks
            has_confirmation_channel=True,
        )
        chain = evaluate_chain(ctx)
        final = resolve_final_action(chain, Action.RETRY_NOW, COMPLIANT_NOW_UTC)
        assert final.action is Action.SEND_PRE_DEBIT_NOTICE

    def test_both_g2_and_g3_block_picks_g2_first_gate_order_wins(self) -> None:
        # SPEC §6.3's mock shows both a notice AND an AFA link dispatched at
        # once; this build dispatches one remedy per cycle (PRD's
        # decisions.final is singular, not a list — see api/gates/registry.py's
        # module docstring). G2 precedes G3 in gate order, so the notice goes
        # out first; a later cycle would then hit G3 once G2 clears.
        ctx = base_context(
            action=Action.RETRY_NOW,
            amount_paise=20_000 * 100,  # G3 blocks
            pre_debit_notice_sent_at=None,  # G2 also blocks
            has_confirmation_channel=True,
        )
        chain = evaluate_chain(ctx)
        final = resolve_final_action(chain, Action.RETRY_NOW, COMPLIANT_NOW_UTC)
        assert final.action is Action.SEND_PRE_DEBIT_NOTICE
        assert (
            len(final.suppressed) >= 2
        )  # both blocks recorded, even though only one remedy chosen

    def test_optout_block_with_no_remedy_falls_back_to_suppress(self) -> None:
        ctx = base_context(
            action=Action.RETRY_NOW,
            opted_out=True,
            # Isolate G6_OPTOUT as the only blocker — clear G2/G3/G8 so this
            # test proves the no-remedy fallback, not G2's remediation.
            pre_debit_notice_sent_at=COMPLIANT_NOW_UTC - dt.timedelta(hours=25),
            amount_paise=5_000 * 100,
            has_confirmation_channel=True,
        )
        chain = evaluate_chain(ctx)
        final = resolve_final_action(chain, Action.RETRY_NOW, COMPLIANT_NOW_UTC)
        assert final.action is Action.SUPPRESS

    def test_block_with_no_remedy_and_no_optout_falls_back_to_close_no_action(self) -> None:
        ctx = base_context(
            action=Action.RETRY_NOW,
            subscription_state="halted",
            pre_debit_notice_sent_at=COMPLIANT_NOW_UTC - dt.timedelta(hours=25),
            amount_paise=5_000 * 100,
            has_confirmation_channel=True,
        )
        chain = evaluate_chain(ctx)
        final = resolve_final_action(chain, Action.RETRY_NOW, COMPLIANT_NOW_UTC)
        assert final.action is Action.CLOSE_NO_ACTION
        assert any(s["blocked_by"] == "G9_HALTED" for s in final.suppressed)


def _allow_chain() -> ChainResult:
    verdict = GateVerdict(
        gate="G0_KILL",
        kind=VerdictKind.ALLOW,
        clause_ref="c",
        detail="d",
        evaluated_at=COMPLIANT_NOW_UTC,
    )
    return ChainResult(verdicts=(verdict,), aggregate=VerdictKind.ALLOW, replan_at=None)


def _block_chain(gate: str) -> ChainResult:
    verdict = GateVerdict(
        gate=gate,
        kind=VerdictKind.BLOCK,
        clause_ref="c",
        detail="d",
        evaluated_at=COMPLIANT_NOW_UTC,
    )
    return ChainResult(verdicts=(verdict,), aggregate=VerdictKind.BLOCK, replan_at=None)


class TestResolveWithRemediation:
    """Direct, synthetic tests of the shared remediation loop
    (compliance-auditor F1, spec-guardian D2/G1/G2/G3) — a fake `gate_fn`
    exercises paths real gate combinations can't realistically construct
    (an oscillating or never-settling remedy), without needing real gates,
    policy, or database plumbing. Shared by api.pipeline and
    api.scheduler.runner, so one test suite covers both callers.
    """

    async def test_no_remediation_needed_settles_immediately(self) -> None:
        chain = _allow_chain()

        async def gate_fn(action: Action) -> ChainResult:
            raise AssertionError("gate_fn must not be called when the first pass already settles")

        result = await resolve_with_remediation(
            chain, Action.CLOSE_NO_ACTION, COMPLIANT_NOW_UTC, gate_fn
        )
        assert result.hops == 0
        assert result.dispatchable is True
        assert result.final.action is Action.CLOSE_NO_ACTION
        assert result.final.verdict is VerdictKind.ALLOW
        assert result.verdicts == chain.verdicts

    async def test_one_hop_remedy_that_passes_its_own_check_settles_allow(self) -> None:
        first = _block_chain("G2_PREDEBIT")  # remedy: SEND_PRE_DEBIT_NOTICE

        async def gate_fn(action: Action) -> ChainResult:
            assert action is Action.SEND_PRE_DEBIT_NOTICE
            return _allow_chain()

        result = await resolve_with_remediation(first, Action.RETRY_NOW, COMPLIANT_NOW_UTC, gate_fn)
        assert result.hops == 1
        assert result.dispatchable is True
        assert result.final.action is Action.SEND_PRE_DEBIT_NOTICE
        assert result.final.verdict is VerdictKind.ALLOW
        assert len(result.verdicts) == 2  # first pass + the one re-gate hop

    async def test_remedy_that_fails_its_own_check_falls_to_terminal_and_settles(self) -> None:
        # Hop 1: the remedy (SEND_PRE_DEBIT_NOTICE) is blocked by a gate with
        # no REMEDIATION entry (G7_THIRDPARTY) -> falls to the terminal
        # CLOSE_NO_ACTION. Hop 2: CLOSE_NO_ACTION is re-gated too (correct —
        # even the terminal fallback is verified, not assumed safe) and
        # settles once it stops changing.
        first = _block_chain("G2_PREDEBIT")

        async def gate_fn(action: Action) -> ChainResult:
            return _block_chain("G7_THIRDPARTY")  # no remedy entry -> terminal, every hop

        result = await resolve_with_remediation(first, Action.RETRY_NOW, COMPLIANT_NOW_UTC, gate_fn)
        assert result.hops == 2
        assert result.dispatchable is True
        assert result.final.action is Action.CLOSE_NO_ACTION
        assert result.final.verdict is VerdictKind.BLOCK

    async def test_never_settling_remedy_exhausts_the_hop_bound_and_dispatches_nothing(
        self,
    ) -> None:
        # A pathological gate_fn that always blocks with a DIFFERENT remedy
        # each time, oscillating forever — this is exactly what
        # compliance-auditor's report proves cannot happen with the real
        # gates (G2/G3 only gate charge actions; their remedies are contact
        # actions neither can re-fire on), but the loop's own safety bound
        # must hold even if that invariant were ever violated by a future
        # REMEDIATION entry.
        first = _block_chain("G2_PREDEBIT")
        calls = {"n": 0}

        async def gate_fn(action: Action) -> ChainResult:
            calls["n"] += 1
            # Alternate between the two remediable gates forever.
            return _block_chain("G3_AFA" if calls["n"] % 2 else "G2_PREDEBIT")

        blocked_actions: list[Action] = []

        async def on_block(action: Action, blocking: tuple[GateVerdict, ...]) -> None:
            blocked_actions.append(action)

        result = await resolve_with_remediation(
            first, Action.RETRY_NOW, COMPLIANT_NOW_UTC, gate_fn, on_block=on_block
        )
        assert result.hops == MAX_REMEDIATION_HOPS
        assert result.dispatchable is False  # the critical assertion: nothing may dispatch
        assert result.final.action is Action.CLOSE_NO_ACTION
        assert result.final.verdict is VerdictKind.BLOCK
        # every hop's block was still recorded via on_block, even though
        # nothing was ever allowed to execute
        assert len(blocked_actions) == MAX_REMEDIATION_HOPS
        # first pass + one entry per hop
        assert len(result.verdicts) == 1 + MAX_REMEDIATION_HOPS

    async def test_replan_from_a_remedy_hop_is_returned_not_looped(self) -> None:
        first = _block_chain("G2_PREDEBIT")

        async def gate_fn(action: Action) -> ChainResult:
            verdict = GateVerdict(
                gate="G1_WINDOW",
                kind=VerdictKind.REPLAN,
                clause_ref="c",
                detail="d",
                evaluated_at=COMPLIANT_NOW_UTC,
                replan_at=COMPLIANT_NOW_UTC + dt.timedelta(hours=1),
            )
            return ChainResult(
                verdicts=(verdict,), aggregate=VerdictKind.REPLAN, replan_at=verdict.replan_at
            )

        result = await resolve_with_remediation(first, Action.RETRY_NOW, COMPLIANT_NOW_UTC, gate_fn)
        assert result.hops == 1
        assert result.dispatchable is True
        assert result.final.verdict is VerdictKind.REPLAN
        assert result.final.action is Action.SEND_PRE_DEBIT_NOTICE

    async def test_on_block_is_called_for_every_hop_that_blocks(self) -> None:
        first = _block_chain("G2_PREDEBIT")
        seen: list[str] = []

        async def gate_fn(action: Action) -> ChainResult:
            return _allow_chain()

        async def on_block(action: Action, blocking: tuple[GateVerdict, ...]) -> None:
            seen.append(action.value)

        await resolve_with_remediation(
            first, Action.RETRY_NOW, COMPLIANT_NOW_UTC, gate_fn, on_block=on_block
        )
        # The remedy's own chain (from gate_fn) was ALLOW, so on_block is
        # never called for it — only the caller's pre-loop first-pass block
        # (handled outside this function) would have used it.
        assert seen == []

    async def test_suppressed_accumulates_across_hops_not_overwritten(self) -> None:
        first = _block_chain("G2_PREDEBIT")
        # Hop 1: gate SEND_PRE_DEBIT_NOTICE -> blocked by G3_AFA (remedy:
        # CREATE_AFA_LINK). Hop 2: gate CREATE_AFA_LINK -> ALLOW. Two hops,
        # two distinct suppressed-by records, must both survive.
        hop = {"n": 0}

        async def gate_fn_two_hops(action: Action) -> ChainResult:
            hop["n"] += 1
            return _block_chain("G3_AFA") if hop["n"] == 1 else _allow_chain()

        result = await resolve_with_remediation(
            first, Action.RETRY_NOW, COMPLIANT_NOW_UTC, gate_fn_two_hops
        )
        assert result.hops == 2
        assert result.dispatchable is True
        assert result.final.verdict is VerdictKind.ALLOW
        # Both the original RETRY_NOW-blocked-by-G2 and the intermediate
        # SEND_PRE_DEBIT_NOTICE-blocked-by-G3 records must survive.
        blocked_by = {s["blocked_by"] for s in result.final.suppressed}
        assert blocked_by == {"G2_PREDEBIT", "G3_AFA"}
