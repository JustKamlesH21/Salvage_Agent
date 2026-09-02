"""G5_COOLDOWN — at least 48h between customer contacts, no more than 3
contacts in a rolling 7-day window. PRD §9 / SPEC §5.5.

Applies to contact actions only (silent auto-debit never counts as a
"contact"). Both sub-rules become lawful purely by waiting, so this always
REPLANs when it fires rather than terminally blocking — the later of the two
computed timestamps, since both must hold at once.
"""

from __future__ import annotations

import datetime as dt

from api.gates.types import CONTACT_ACTIONS, GateContext, GateVerdict, VerdictKind

CLAUSE_REF = "Anti-harassment / marginal-value-of-nth-contact research (SPEC §1, §5.5)"
RULE_TEXT = (
    "At least 48 hours must elapse between customer contacts, and no more "
    "than 3 contacts may be made within any rolling 7-day window."
)

MIN_GAP = dt.timedelta(hours=48)
WINDOW = dt.timedelta(days=7)
MAX_CONTACTS_IN_WINDOW = 3


def evaluate(ctx: GateContext) -> GateVerdict:
    if ctx.action not in CONTACT_ACTIONS:
        return GateVerdict(
            gate="G5_COOLDOWN",
            kind=VerdictKind.ALLOW,
            clause_ref=CLAUSE_REF,
            detail=f"{ctx.action.value} is not a customer contact; cooldown does not apply",
            evaluated_at=ctx.now_utc,
        )
    naive_contacts = any(t.tzinfo is None for t in ctx.contacts_in_7d)
    naive_last = ctx.last_contact_at is not None and ctx.last_contact_at.tzinfo is None
    if ctx.now_utc.tzinfo is None or naive_contacts or naive_last:
        return GateVerdict(
            gate="G5_COOLDOWN",
            kind=VerdictKind.BLOCK,
            clause_ref=CLAUSE_REF,
            detail="malformed: naive timestamp in now_utc, last_contact_at, or contacts_in_7d",
            evaluated_at=ctx.now_utc,
        )

    replan_candidates: list[dt.datetime] = []

    if ctx.last_contact_at is not None:
        gap_ready = ctx.last_contact_at + MIN_GAP
        if ctx.now_utc < gap_ready:
            replan_candidates.append(gap_ready)

    window_start = ctx.now_utc - WINDOW
    in_window = sorted(t for t in ctx.contacts_in_7d if t > window_start)
    if len(in_window) >= MAX_CONTACTS_IN_WINDOW:
        replan_candidates.append(in_window[0] + WINDOW)

    if not replan_candidates:
        return GateVerdict(
            gate="G5_COOLDOWN",
            kind=VerdictKind.ALLOW,
            clause_ref=CLAUSE_REF,
            detail=f"48h gap ok, {len(in_window)}/{MAX_CONTACTS_IN_WINDOW} contacts in 7 days",
            evaluated_at=ctx.now_utc,
        )
    return GateVerdict(
        gate="G5_COOLDOWN",
        kind=VerdictKind.REPLAN,
        clause_ref=CLAUSE_REF,
        detail=f"cooldown active — {len(in_window)}/{MAX_CONTACTS_IN_WINDOW} in 7d, or <48h gap",
        evaluated_at=ctx.now_utc,
        replan_at=max(replan_candidates),
    )
