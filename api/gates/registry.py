"""The gate chain runner. Not a gate itself — exempt from
scripts/check_clauses.py's CLAUSE_REF/RULE_TEXT requirement (NON_GATE_MODULES).

`evaluate_chain()` runs every gate, in PRD §9's order, always — even after an
earlier one blocks (gate-authoring skill: the Decision Tape shows every
gate's verdict, not just the first blocker). `G0_KILL` first, always
(invariant 4); the order below is not configurable by any argument.

`resolve_final_action()` is where the two open questions Day 3's NOTES.md
left for "Day 4's call" get resolved: the remediation map converts a specific
gate's BLOCK into the compensating action SPEC §6.3's Case View mock shows
(a pre-debit notice, an AFA link) without duplicating the threshold each gate
already owns, and without inventing a richer gate return type than SPEC
§5.5's ALLOW/BLOCK/REPLAN.

**Scope simplification, stated explicitly**: SPEC §6.3's mock shows *two*
remedies dispatched in the same cycle (a notice AND an AFA link) when both
G2_PREDEBIT and G3_AFA block at once. PRD §8.2's `decisions.final` is a
single `{action, at, verdict}`, not a list — this build dispatches one remedy
per decision cycle (the first remediable block in gate order — G2 before G3,
so a notice goes out before an AFA link is even considered), and the case
converges to the same end state across the following cycle(s) instead of
within one. A schema change to make `final` a list is future work, not
silently assumed today.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from api.gates import (
    g0_kill,
    g1_window,
    g2_predebit,
    g3_afa,
    g4_budget,
    g5_cooldown,
    g6_optout,
    g7_thirdparty,
    g8_postdebit,
    g9_halted,
    g10_idempotent,
)
from api.gates.types import GateContext, GateVerdict, VerdictKind
from api.policy.types import Action

GateFn = Callable[[GateContext], GateVerdict]

# PRD §9's order. G0_KILL first, always (invariant 4). Not reorderable by any
# argument, flag or config value.
GATES_IN_ORDER: tuple[GateFn, ...] = (
    g0_kill.evaluate,
    g1_window.evaluate,
    g2_predebit.evaluate,
    g3_afa.evaluate,
    g4_budget.evaluate,
    g5_cooldown.evaluate,
    g6_optout.evaluate,
    g7_thirdparty.evaluate,
    g8_postdebit.evaluate,
    g9_halted.evaluate,
    g10_idempotent.evaluate,
)

# Which BLOCK converts into which compensating action, and nothing else — the
# threshold/window each gate enforces stays owned by that gate alone.
REMEDIATION: dict[str, Action] = {
    "G2_PREDEBIT": Action.SEND_PRE_DEBIT_NOTICE,
    "G3_AFA": Action.CREATE_AFA_LINK,
}


@dataclass(frozen=True)
class ChainResult:
    verdicts: tuple[GateVerdict, ...]  # all eleven, in order, always
    aggregate: VerdictKind
    replan_at: dt.datetime | None  # set only when aggregate is REPLAN

    @property
    def blocking(self) -> tuple[GateVerdict, ...]:
        return tuple(v for v in self.verdicts if v.kind == VerdictKind.BLOCK)


def evaluate_chain(ctx: GateContext) -> ChainResult:
    verdicts = tuple(gate(ctx) for gate in GATES_IN_ORDER)
    blocking = [v for v in verdicts if v.kind == VerdictKind.BLOCK]
    replanning = [v for v in verdicts if v.kind == VerdictKind.REPLAN]

    if blocking:
        return ChainResult(verdicts=verdicts, aggregate=VerdictKind.BLOCK, replan_at=None)
    if replanning:
        latest = max(v.replan_at for v in replanning if v.replan_at is not None)
        return ChainResult(verdicts=verdicts, aggregate=VerdictKind.REPLAN, replan_at=latest)
    return ChainResult(verdicts=verdicts, aggregate=VerdictKind.ALLOW, replan_at=None)


@dataclass(frozen=True)
class FinalAction:
    action: Action
    verdict: VerdictKind  # PRD §8.2 decisions.final.verdict: allow | block | replan
    at: dt.datetime
    suppressed: tuple[dict[str, str], ...]  # [{action, blocked_by, clause_ref}]


def resolve_final_action(chain: ChainResult, proposed: Action, now_utc: dt.datetime) -> FinalAction:
    if chain.aggregate == VerdictKind.ALLOW:
        return FinalAction(action=proposed, verdict=VerdictKind.ALLOW, at=now_utc, suppressed=())

    if chain.aggregate == VerdictKind.REPLAN:
        # `evaluate_chain()` only ever sets `aggregate=REPLAN` alongside a
        # `replan_at` (it's computed from the same replanning verdicts) — an
        # explicit check, not a bare `assert`, since `assert` is stripped
        # under `python -O` (compliance-auditor F9's finding, recurring here
        # per spec-guardian's review).
        if chain.replan_at is None:
            raise ValueError("ChainResult.aggregate is REPLAN but replan_at is None (malformed)")
        return FinalAction(
            action=proposed, verdict=VerdictKind.REPLAN, at=chain.replan_at, suppressed=()
        )

    # BLOCK. Try the first remediable block in gate order; else a terminal fallback.
    suppressed = tuple(
        {"action": proposed.value, "blocked_by": v.gate, "clause_ref": v.clause_ref}
        for v in chain.blocking
    )
    for verdict in chain.blocking:
        remedy = REMEDIATION.get(verdict.gate)
        if remedy is not None:
            return FinalAction(
                action=remedy, verdict=VerdictKind.BLOCK, at=now_utc, suppressed=suppressed
            )

    terminal = (
        Action.SUPPRESS
        if any(v.gate == "G6_OPTOUT" for v in chain.blocking)
        else Action.CLOSE_NO_ACTION
    )
    return FinalAction(
        action=terminal, verdict=VerdictKind.BLOCK, at=now_utc, suppressed=suppressed
    )


MAX_REMEDIATION_HOPS = 3

RemediationGateFn = Callable[[Action], Awaitable[ChainResult]]
OnBlockFn = Callable[[Action, tuple[GateVerdict, ...]], Awaitable[None]]


@dataclass(frozen=True)
class RemediationResult:
    verdicts: tuple[GateVerdict, ...]  # every hop's verdicts, in order
    final: FinalAction
    dispatchable: bool  # False only when the hop bound was hit without settling
    hops: int


async def resolve_with_remediation(
    initial_chain: ChainResult,
    proposed: Action,
    now_utc: dt.datetime,
    gate_fn: RemediationGateFn,
    *,
    on_block: OnBlockFn | None = None,
    max_hops: int = MAX_REMEDIATION_HOPS,
) -> RemediationResult:
    """`resolve_final_action()` may substitute a remediation action (e.g.
    G2_PREDEBIT's BLOCK converting to SEND_PRE_DEBIT_NOTICE) that has never
    itself been gated — compliance-auditor F1: dispatching it on the first
    pass's authority alone is a full-chain bypass, since the remedy is a
    compliance-relevant action (a customer contact, an AFA link) in its own
    right. This re-gates every substitute fresh via `gate_fn`, in a bounded
    loop, before it is ever eligible to dispatch.

    Shared between `api.pipeline.process_payment_failed()` and
    `api.scheduler.runner.fire_due_jobs()` — both had nearly identical copies
    of this loop, and both had the identical residual bug (spec-guardian
    G2/G3): hitting the hop bound without settling must mean "record a
    blocked decision and dispatch nothing", never "dispatch an unverified
    fallback action anyway" — the same bypass shape F1 exists to prevent,
    reintroduced on the exhaustion branch by an earlier version of this fix
    in each caller independently. One shared, one directly-testable
    implementation closes both at once.

    `verdicts` accumulates every hop's gate verdicts, not just the first
    pass's (spec-guardian D2) — a Decision Tape needs to show why the
    originally proposed action was blocked *and* why the action that
    actually dispatched was found compliant.
    """
    final = resolve_final_action(initial_chain, proposed, now_utc)
    accumulated_verdicts = list(initial_chain.verdicts)
    accumulated_suppressed = list(final.suppressed)
    dispatchable = True
    hops = 0

    while final.action != proposed:
        if hops >= max_hops:
            dispatchable = False
            break
        hops += 1
        remedy_chain = await gate_fn(final.action)
        accumulated_verdicts.extend(remedy_chain.verdicts)
        if on_block is not None and remedy_chain.blocking:
            await on_block(final.action, remedy_chain.blocking)
        previous_action = final.action
        final = resolve_final_action(remedy_chain, final.action, now_utc)
        # resolve_final_action() only knows about *this* hop's chain, so its
        # own `suppressed` never includes earlier hops' — accumulate
        # explicitly, or the original blocked-proposal record vanishes the
        # moment a later hop settles to ALLOW.
        accumulated_suppressed.extend(final.suppressed)
        if final.action == previous_action:
            break  # settled naturally: ALLOW, REPLAN, or a terminal BLOCK with no further remedy

    final = FinalAction(
        action=final.action,
        verdict=final.verdict,
        at=final.at,
        suppressed=tuple(accumulated_suppressed),
    )
    if not dispatchable:
        final = FinalAction(
            action=Action.CLOSE_NO_ACTION,
            verdict=VerdictKind.BLOCK,
            at=now_utc,
            suppressed=final.suppressed,
        )

    return RemediationResult(
        verdicts=tuple(accumulated_verdicts), final=final, dispatchable=dispatchable, hops=hops
    )
