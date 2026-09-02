"""G4_BUDGET — max 4 Salvage-initiated attempts per case within a rolling
14-day window, on top of Razorpay's own T+1/T+2/T+3. PRD §9 / SPEC §5.5.

`salvage_attempts_in_window` is Salvage-only by construction: api/pipeline.py's
`build_gate_context()` derives it from `audit` rows whose `action` is
`RETRY_NOW`/`RETRY_SCHEDULED`, never from a case-document field and never
from Razorpay's own auto-retries (which are never written to `audit` at all)
— CLAUDE.md: "Razorpay's own auto-retries... do NOT consume that budget —
this is the product's key lever, do not accidentally design it away."
(An earlier version of this docstring said the count came from
`case.salvage_attempt_count` — spec-guardian D6: that field is seeded 0 and
never incremented; `audit` is, and always was, the real source.) This gate
is a safety net independent of policy's own POL-009, which also stops
proposing a retry past 4 attempts using the same audit-derived count — this
still checks independently, so a bypass of policy alone cannot bypass the
budget.
"""

from __future__ import annotations

import datetime as dt

from api.gates.types import CHARGE_ACTIONS, GateContext, GateVerdict, VerdictKind

CLAUSE_REF = "Card network retry limits; soft-to-hard decline conversion risk (SPEC §1, §13)"
RULE_TEXT = (
    "No more than 4 Salvage-initiated recovery attempts per case within a "
    "rolling 14-day window, independent of and additional to Razorpay's own "
    "T+1/T+2/T+3 auto-retry schedule."
)

MAX_ATTEMPTS = 4
WINDOW = dt.timedelta(days=14)


def evaluate(ctx: GateContext) -> GateVerdict:
    if ctx.action not in CHARGE_ACTIONS:
        return GateVerdict(
            gate="G4_BUDGET",
            kind=VerdictKind.ALLOW,
            clause_ref=CLAUSE_REF,
            detail=f"{ctx.action.value} does not consume the attempt budget",
            evaluated_at=ctx.now_utc,
        )
    if ctx.now_utc.tzinfo is None or any(t.tzinfo is None for t in ctx.salvage_attempts_in_window):
        return GateVerdict(
            gate="G4_BUDGET",
            kind=VerdictKind.BLOCK,
            clause_ref=CLAUSE_REF,
            detail="malformed: naive timestamp in now_utc or salvage_attempts_in_window",
            evaluated_at=ctx.now_utc,
        )

    window_start = ctx.now_utc - WINDOW
    in_window = sorted(t for t in ctx.salvage_attempts_in_window if t > window_start)
    if len(in_window) < MAX_ATTEMPTS:
        return GateVerdict(
            gate="G4_BUDGET",
            kind=VerdictKind.ALLOW,
            clause_ref=CLAUSE_REF,
            detail=f"{len(in_window)}/{MAX_ATTEMPTS} Salvage attempts in the trailing 14 days",
            evaluated_at=ctx.now_utc,
        )

    oldest = in_window[0]
    replan_at = oldest + WINDOW
    return GateVerdict(
        gate="G4_BUDGET",
        kind=VerdictKind.REPLAN,
        clause_ref=CLAUSE_REF,
        detail=f"{len(in_window)}/{MAX_ATTEMPTS} attempts in 14 days — budget exhausted",
        evaluated_at=ctx.now_utc,
        replan_at=replan_at,
    )
