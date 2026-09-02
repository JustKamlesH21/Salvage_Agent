"""G6_OPTOUT — opt-out is permanent and immediate. No further automated
contact, ever. PRD §9 / SPEC §5.5.

Never REPLAN: an opt-out does not expire by waiting. `CLOSE_NO_ACTION` and
`SUPPRESS` stay allowed even while opted out — refusing to close a case or
acknowledge the opt-out itself would be the one way this gate could trap a
case in limbo forever.
"""

from __future__ import annotations

from api.gates.types import OPT_OUT_EXEMPT_ACTIONS, GateContext, GateVerdict, VerdictKind

CLAUSE_REF = "RBI Digital Payments E-mandate Framework, 2026 (RBI/DPSS/2026-27/396, 21 Apr 2026)"
RULE_TEXT = (
    "Where a customer has opted out, no further automated contact or "
    "auto-debit attempt may occur. The opt-out is permanent and takes "
    "immediate effect."
)


def evaluate(ctx: GateContext) -> GateVerdict:
    if not ctx.opted_out:
        return GateVerdict(
            gate="G6_OPTOUT",
            kind=VerdictKind.ALLOW,
            clause_ref=CLAUSE_REF,
            detail="no opt-out on record",
            evaluated_at=ctx.now_utc,
        )
    if ctx.action in OPT_OUT_EXEMPT_ACTIONS:
        return GateVerdict(
            gate="G6_OPTOUT",
            kind=VerdictKind.ALLOW,
            clause_ref=CLAUSE_REF,
            detail=f"opted out, but {ctx.action.value} honours the opt-out, not further contact",
            evaluated_at=ctx.now_utc,
        )
    return GateVerdict(
        gate="G6_OPTOUT",
        kind=VerdictKind.BLOCK,
        clause_ref=CLAUSE_REF,
        detail="customer has opted out — permanent, no further contact or auto-debit",
        evaluated_at=ctx.now_utc,
    )
