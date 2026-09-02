"""G7_THIRDPARTY — never contact employer, relatives, neighbours, or any
non-account contact. PRD §9 / SPEC §5.5.

No code path in this system currently ever sets `contact_target` to anything
but "account_holder" — Salvage has no employer/relative/neighbour contact
field anywhere in its data model. This gate exists anyway, and is fully
testable (ALLOW/BLOCK), as a safety net for exactly that reason: the same
shape as G0_KILL and G9_HALTED, a check that should never fire in normal
operation but must be provably capable of firing if something upstream ever
tries to route a notification somewhere it shouldn't.
"""

from __future__ import annotations

from api.gates.types import KNOWN_CONTACT_TARGETS, GateContext, GateVerdict, VerdictKind

CLAUSE_REF = "RBI Fair Practices Code — recovery agent conduct"
RULE_TEXT = (
    "Recovery communication must be directed only to the account holder. "
    "Employers, relatives, neighbours, or any other third party must never "
    "be contacted regarding the account."
)


def evaluate(ctx: GateContext) -> GateVerdict:
    if ctx.contact_target not in KNOWN_CONTACT_TARGETS:
        return GateVerdict(
            gate="G7_THIRDPARTY",
            kind=VerdictKind.BLOCK,
            clause_ref=CLAUSE_REF,
            detail=f"malformed: unrecognised contact_target {ctx.contact_target!r}",
            evaluated_at=ctx.now_utc,
        )
    if ctx.contact_target == "account_holder":
        return GateVerdict(
            gate="G7_THIRDPARTY",
            kind=VerdictKind.ALLOW,
            clause_ref=CLAUSE_REF,
            detail="contact target is the account holder",
            evaluated_at=ctx.now_utc,
        )
    return GateVerdict(
        gate="G7_THIRDPARTY",
        kind=VerdictKind.BLOCK,
        clause_ref=CLAUSE_REF,
        detail="contact target is a third party — never permitted",
        evaluated_at=ctx.now_utc,
    )
