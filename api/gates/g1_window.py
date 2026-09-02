"""G1_WINDOW — customer contact only 08:00-19:00 IST, every channel including
automated messaging. PRD §9 / SPEC §5.5.

Scope decision (approved plan, this phase): gates customer-CONTACT actions
only (SEND_PRE_DEBIT_NOTICE, CREATE_REAUTH_LINK, CREATE_AFA_LINK, NUDGE) —
the cited circular governs recovery-agent *contact*, not silent auto-debit
timing (a RETRY_NOW/RETRY_SCHEDULED never reaches the customer directly).
SPEC §6.3's illustrative Case View mock shows this gate blocking a plain
retry proposal; that is treated as illustrative flavor, not a literal
contract this deviates from — same treatment Day 3 gave SALVAGE_SPEC.md
§5.4's stale POL-004 priority number.
"""

from __future__ import annotations

import datetime as dt

from api.gates.types import CONTACT_ACTIONS, GateContext, GateVerdict, VerdictKind

CLAUSE_REF = "RBI DOR.ORG.REC.65/21.04.158/2022-23 (12 Aug 2022)"
RULE_TEXT = (
    "Regulated entities and their recovery agents shall not call the borrower "
    "before 8:00 a.m. and after 7:00 p.m. Applies across all contact channels, "
    "including automated messaging."
)

WINDOW_OPEN_HOUR = 8
WINDOW_CLOSE_HOUR = 19


def evaluate(ctx: GateContext) -> GateVerdict:
    if ctx.action not in CONTACT_ACTIONS:
        return GateVerdict(
            gate="G1_WINDOW",
            kind=VerdictKind.ALLOW,
            clause_ref=CLAUSE_REF,
            detail=f"{ctx.action.value} is not a customer-contact action; window does not apply",
            evaluated_at=ctx.now_utc,
        )
    if ctx.now_utc.tzinfo is None:
        return GateVerdict(
            gate="G1_WINDOW",
            kind=VerdictKind.BLOCK,
            clause_ref=CLAUSE_REF,
            detail="malformed: now_utc has no timezone, cannot verify the contact window safely",
            evaluated_at=ctx.now_utc,
        )

    now_ist = ctx.now_ist()
    if WINDOW_OPEN_HOUR <= now_ist.hour < WINDOW_CLOSE_HOUR:
        return GateVerdict(
            gate="G1_WINDOW",
            kind=VerdictKind.ALLOW,
            clause_ref=CLAUSE_REF,
            detail=f"{now_ist.strftime('%H:%M')} IST is within 08:00-19:00",
            evaluated_at=ctx.now_utc,
        )

    replan_at = _next_window_open(now_ist)
    return GateVerdict(
        gate="G1_WINDOW",
        kind=VerdictKind.REPLAN,
        clause_ref=CLAUSE_REF,
        detail=f"{now_ist.strftime('%H:%M')} IST is outside 08:00-19:00",
        evaluated_at=ctx.now_utc,
        replan_at=replan_at.astimezone(dt.UTC),
    )


def _next_window_open(now_ist: dt.datetime) -> dt.datetime:
    """The next 08:00 IST, strictly after `now_ist`. If it's already past
    08:00 today (i.e. we're in the post-19:00 tail), that's tomorrow's 08:00.
    """
    today_open = now_ist.replace(hour=WINDOW_OPEN_HOUR, minute=0, second=0, microsecond=0)
    if now_ist < today_open:
        return today_open
    return today_open + dt.timedelta(days=1)
