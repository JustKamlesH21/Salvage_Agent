"""G3_AFA — no-AFA auto-debit permitted only up to ₹15,000/txn; ₹1,00,000 for
insurance premiums, mutual fund subscriptions and credit-card bill payments.
Above threshold, the action must route to an AFA-authenticated link, never a
silent retry. PRD §9 / SPEC §5.5.

Never REPLAN (gate-authoring skill: "G3_AFA above the threshold does not
replan, because waiting changes nothing"). A BLOCK here is converted by the
remediation map in api/gates/registry.py into dispatching CREATE_AFA_LINK —
this module only owns the threshold check, not the remedy.
"""

from __future__ import annotations

from api.gates.types import CHARGE_ACTIONS, KNOWN_CATEGORIES, GateContext, GateVerdict, VerdictKind

CLAUSE_REF = "RBI Digital Payments E-mandate Framework, 2026 (RBI/DPSS/2026-27/396, 21 Apr 2026)"
RULE_TEXT = (
    "Auto-debit without Additional Factor of Authentication is permitted only "
    "up to ₹15,000 per transaction; up to ₹1,00,000 for insurance premiums, "
    "mutual fund subscriptions and credit-card bill payments. Above the "
    "applicable threshold, the transaction must route through an "
    "AFA-authenticated flow — never a silent auto-debit."
)

STANDARD_THRESHOLD_PAISE = 15_000 * 100
ENHANCED_THRESHOLD_PAISE = 100_000 * 100
ENHANCED_CATEGORIES = frozenset({"insurance", "mf", "cc_bill"})


def _threshold_paise(category: str) -> int:
    return ENHANCED_THRESHOLD_PAISE if category in ENHANCED_CATEGORIES else STANDARD_THRESHOLD_PAISE


def evaluate(ctx: GateContext) -> GateVerdict:
    if ctx.action not in CHARGE_ACTIONS:
        return GateVerdict(
            gate="G3_AFA",
            kind=VerdictKind.ALLOW,
            clause_ref=CLAUSE_REF,
            detail=f"{ctx.action.value} is not an auto-debit; AFA threshold does not apply",
            evaluated_at=ctx.now_utc,
        )
    if ctx.category not in KNOWN_CATEGORIES or ctx.amount_paise < 0:
        return GateVerdict(
            gate="G3_AFA",
            kind=VerdictKind.BLOCK,
            clause_ref=CLAUSE_REF,
            detail=f"malformed: category={ctx.category!r}, amount_paise={ctx.amount_paise!r}",
            evaluated_at=ctx.now_utc,
        )

    threshold = _threshold_paise(ctx.category)
    amount_str = f"₹{ctx.amount_paise / 100:,.2f}"
    threshold_str = f"₹{threshold / 100:,.0f}"
    if ctx.amount_paise <= threshold:
        return GateVerdict(
            gate="G3_AFA",
            kind=VerdictKind.ALLOW,
            clause_ref=CLAUSE_REF,
            detail=f"{amount_str} <= {threshold_str} threshold ({ctx.category})",
            evaluated_at=ctx.now_utc,
        )
    return GateVerdict(
        gate="G3_AFA",
        kind=VerdictKind.BLOCK,
        clause_ref=CLAUSE_REF,
        detail=f"{amount_str} > {threshold_str} threshold ({ctx.category}) — AFA required",
        evaluated_at=ctx.now_utc,
    )
