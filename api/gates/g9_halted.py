"""G9_HALTED — no auto-charge on a halted subscription. PRD §9 / SPEC §5.5.

Never REPLAN: a halted subscription does not un-halt by waiting — it needs an
explicit customer-initiated re-authentication (NOTES.md day-1 finding:
recovery-from-halted is real but requires a successful charge/re-auth
action, not a passive timer), which is exactly why CREATE_REAUTH_LINK and
CREATE_AFA_LINK are never gated by this rule — they're the way *out* of
halted, not a further silent charge attempt.
"""

from __future__ import annotations

from api.gates.types import (
    CHARGE_ACTIONS,
    KNOWN_SUBSCRIPTION_STATES,
    GateContext,
    GateVerdict,
    VerdictKind,
)

CLAUSE_REF = "Razorpay subscription state machine"
RULE_TEXT = (
    "A subscription in the halted state may not be auto-charged. Recovery "
    "from halted requires a customer-initiated re-authentication, not a "
    "further silent debit attempt."
)


def evaluate(ctx: GateContext) -> GateVerdict:
    if ctx.subscription_state not in KNOWN_SUBSCRIPTION_STATES:
        return GateVerdict(
            gate="G9_HALTED",
            kind=VerdictKind.BLOCK,
            clause_ref=CLAUSE_REF,
            detail=f"malformed: unrecognised subscription_state {ctx.subscription_state!r}",
            evaluated_at=ctx.now_utc,
        )
    if ctx.action not in CHARGE_ACTIONS:
        return GateVerdict(
            gate="G9_HALTED",
            kind=VerdictKind.ALLOW,
            clause_ref=CLAUSE_REF,
            detail=f"{ctx.action.value} is not an auto-charge; halted state does not block it",
            evaluated_at=ctx.now_utc,
        )
    if ctx.subscription_state == "halted":
        return GateVerdict(
            gate="G9_HALTED",
            kind=VerdictKind.BLOCK,
            clause_ref=CLAUSE_REF,
            detail="subscription is halted — no auto-charge permitted",
            evaluated_at=ctx.now_utc,
        )
    return GateVerdict(
        gate="G9_HALTED",
        kind=VerdictKind.ALLOW,
        clause_ref=CLAUSE_REF,
        detail=f"subscription state is {ctx.subscription_state!r}, not halted",
        evaluated_at=ctx.now_utc,
    )
