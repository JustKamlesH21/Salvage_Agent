"""G0_KILL — the global kill switch. Evaluated first, always (invariant 4;
CLAUDE.md: "No flag, config value, or argument skips the chain. G0_KILL is
evaluated first, always"). api/gates/registry.py enforces the ordering; this
module only enforces its own verdict.
"""

from __future__ import annotations

from api.gates.types import GateContext, GateVerdict, VerdictKind

CLAUSE_REF = "Salvage Ops — Global Kill Switch"
RULE_TEXT = (
    "Engaging the kill switch freezes all pending and scheduled actions within "
    "one scheduler tick. No action of any kind may execute while it is engaged."
)


def evaluate(ctx: GateContext) -> GateVerdict:
    if ctx.kill_switch_engaged:
        return GateVerdict(
            gate="G0_KILL",
            kind=VerdictKind.BLOCK,
            clause_ref=CLAUSE_REF,
            detail="kill switch is engaged — every action is frozen",
            evaluated_at=ctx.now_utc,
        )
    return GateVerdict(
        gate="G0_KILL",
        kind=VerdictKind.ALLOW,
        clause_ref=CLAUSE_REF,
        detail="kill switch not engaged",
        evaluated_at=ctx.now_utc,
    )
