"""G10_IDEMPOTENT — refuse any action whose idempotency key already exists in
`audit`. PRD §9 / SPEC §5.5.

Deliberately redundant with the executor's own insert-first idempotency
guarantee (invariant 9, api/executor/executor.py): this is the gate-level,
Decision-Tape-visible check, informational and advisory; the unique
`(idempotency_key, phase)` index on `audit` is the structural guarantee that
holds even if this gate were somehow bypassed. Two independent layers
enforcing the same property, tested independently.

Never REPLAN: a key that already exists does not stop existing by waiting.
"""

from __future__ import annotations

from api.gates.types import GateContext, GateVerdict, VerdictKind

CLAUSE_REF = "Correctness — executor idempotency (CLAUDE.md invariant 9)"
RULE_TEXT = (
    "An action whose idempotency key already has a corresponding audit "
    "record must never be dispatched again."
)


def evaluate(ctx: GateContext) -> GateVerdict:
    if not ctx.idempotency_key.strip():
        return GateVerdict(
            gate="G10_IDEMPOTENT",
            kind=VerdictKind.BLOCK,
            clause_ref=CLAUSE_REF,
            detail="malformed: empty idempotency_key",
            evaluated_at=ctx.now_utc,
        )
    if ctx.idempotency_key_exists:
        return GateVerdict(
            gate="G10_IDEMPOTENT",
            kind=VerdictKind.BLOCK,
            clause_ref=CLAUSE_REF,
            detail=f"idempotency key {ctx.idempotency_key!r} already has an audit record",
            evaluated_at=ctx.now_utc,
        )
    return GateVerdict(
        gate="G10_IDEMPOTENT",
        kind=VerdictKind.ALLOW,
        clause_ref=CLAUSE_REF,
        detail="idempotency key not previously seen",
        evaluated_at=ctx.now_utc,
    )
