"""G2_PREDEBIT — no auto-debit without a pre-transaction notice sent at least
24 hours prior. PRD §9 / SPEC §5.5.

No notice sent at all -> BLOCK (waiting alone never fixes this; the
remediation map in api/gates/registry.py converts this specific BLOCK into
dispatching SEND_PRE_DEBIT_NOTICE). Notice sent but under 24h old -> REPLAN
to the moment it turns 24h, since that becomes lawful purely by waiting.

**What this gate actually checks, stated precisely (compliance-auditor F7):**
only that *some* `SEND_PRE_DEBIT_NOTICE` audit row exists for this case more
than 24h ago — the lead-time requirement. It does not verify the notice's
*content* (merchant name, amount, debit date/time, mandate reference,
opt-out facility) matches the charge now being attempted; today's
`SEND_PRE_DEBIT_NOTICE` dispatch doesn't record amount/date in a form this
gate reads back. `RULE_TEXT` below is narrowed to the lead-time clause this
code enforces, not the full notice-content clause the framework also
requires — citing the fuller text here would be a citation the code doesn't
back, which is worse than a narrower one that's accurate.
"""

from __future__ import annotations

import datetime as dt

from api.gates.types import CHARGE_ACTIONS, GateContext, GateVerdict, VerdictKind

CLAUSE_REF = "RBI Digital Payments E-mandate Framework, 2026 (RBI/DPSS/2026-27/396, 21 Apr 2026)"
RULE_TEXT = (
    "No auto-debit may be attempted without a pre-transaction notification sent "
    "to the customer at least 24 hours prior. (The framework also requires the "
    "notice to carry the merchant name, amount, debit date/time, mandate "
    "reference and an opt-out facility — this gate checks lead time only; "
    "notice content is not yet verified. See this module's docstring.)"
)

NOTICE_LEAD_TIME = dt.timedelta(hours=24)


def evaluate(ctx: GateContext) -> GateVerdict:
    if ctx.action not in CHARGE_ACTIONS:
        return GateVerdict(
            gate="G2_PREDEBIT",
            kind=VerdictKind.ALLOW,
            clause_ref=CLAUSE_REF,
            detail=f"{ctx.action.value} is not an auto-debit; pre-debit notice does not apply",
            evaluated_at=ctx.now_utc,
        )
    if ctx.now_utc.tzinfo is None:
        return GateVerdict(
            gate="G2_PREDEBIT",
            kind=VerdictKind.BLOCK,
            clause_ref=CLAUSE_REF,
            detail="malformed: now_utc has no timezone, cannot verify notice lead time safely",
            evaluated_at=ctx.now_utc,
        )

    notice_at = ctx.pre_debit_notice_sent_at
    if notice_at is None:
        return GateVerdict(
            gate="G2_PREDEBIT",
            kind=VerdictKind.BLOCK,
            clause_ref=CLAUSE_REF,
            detail="no pre-debit notice has been sent for this charge",
            evaluated_at=ctx.now_utc,
        )
    if notice_at.tzinfo is None or notice_at > ctx.now_utc:
        return GateVerdict(
            gate="G2_PREDEBIT",
            kind=VerdictKind.BLOCK,
            clause_ref=CLAUSE_REF,
            detail="malformed: pre_debit_notice_sent_at is naive or in the future",
            evaluated_at=ctx.now_utc,
        )

    ready_at = notice_at + NOTICE_LEAD_TIME
    if ctx.now_utc >= ready_at:
        return GateVerdict(
            gate="G2_PREDEBIT",
            kind=VerdictKind.ALLOW,
            clause_ref=CLAUSE_REF,
            detail=f"notice sent {notice_at.isoformat()}, now past the 24h lead time",
            evaluated_at=ctx.now_utc,
        )
    return GateVerdict(
        gate="G2_PREDEBIT",
        kind=VerdictKind.REPLAN,
        clause_ref=CLAUSE_REF,
        detail=f"notice sent {notice_at.isoformat()}, 24h lead time not yet elapsed",
        evaluated_at=ctx.now_utc,
        replan_at=ready_at,
    )
