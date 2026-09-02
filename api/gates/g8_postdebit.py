"""G8_POSTDEBIT — every successful collection must be followed by a
post-transaction confirmation carrying grievance-redressal details.
PRD §9 / SPEC §5.5.

SPEC §4.2's A1-A10 action catalog has no dedicated "send post-debit
confirmation" action, so this gate cannot check a proposed confirmation
action the way the other ten check a proposed retry/contact/link. Interpreted
instead (documented here, not left implicit) as: a new auto-debit must not be
attempted if the customer structurally cannot receive the confirmation RBI
requires afterward — grounded in the real "issuers must send an alert after
debit" requirement. `has_confirmation_channel` reflects whether the customer
record carries a contact_hash or email_hash (PRD §8.2's `customers` shape).
Never REPLAN: a missing channel does not appear by waiting.
"""

from __future__ import annotations

from api.gates.types import CHARGE_ACTIONS, GateContext, GateVerdict, VerdictKind

CLAUSE_REF = "RBI Digital Payments E-mandate Framework, 2026 (RBI/DPSS/2026-27/396, 21 Apr 2026)"
RULE_TEXT = (
    "Every successful auto-debit collection must be followed by a "
    "post-transaction confirmation to the customer, including "
    "grievance-redressal details."
)


def evaluate(ctx: GateContext) -> GateVerdict:
    if ctx.action not in CHARGE_ACTIONS:
        return GateVerdict(
            gate="G8_POSTDEBIT",
            kind=VerdictKind.ALLOW,
            clause_ref=CLAUSE_REF,
            detail=f"{ctx.action.value} is not an auto-debit; no confirmation obligation",
            evaluated_at=ctx.now_utc,
        )
    if ctx.has_confirmation_channel:
        return GateVerdict(
            gate="G8_POSTDEBIT",
            kind=VerdictKind.ALLOW,
            clause_ref=CLAUSE_REF,
            detail="customer has a confirmation channel on record",
            evaluated_at=ctx.now_utc,
        )
    return GateVerdict(
        gate="G8_POSTDEBIT",
        kind=VerdictKind.BLOCK,
        clause_ref=CLAUSE_REF,
        detail="no confirmation channel on record — cannot fulfil the post-debit alert obligation",
        evaluated_at=ctx.now_utc,
    )
