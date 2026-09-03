import { describe, expect, it } from 'vitest'
import type { Decision, GateVerdict } from './api'
import { GATE_COUNT, splitHops } from './hops'

const GATES = [
  'G0_KILL', 'G1_WINDOW', 'G2_PREDEBIT', 'G3_AFA', 'G4_BUDGET', 'G5_COOLDOWN',
  'G6_OPTOUT', 'G7_THIRDPARTY', 'G8_POSTDEBIT', 'G9_HALTED', 'G10_IDEMPOTENT',
]

/** One full pass of the chain, with the named gates given a non-ALLOW verdict. */
function chain(overrides: Record<string, GateVerdict['verdict']> = {}): GateVerdict[] {
  return GATES.map((gate) => ({
    gate,
    verdict: overrides[gate] ?? 'ALLOW',
    clause_ref: `CLAUSE ${gate}`,
    detail: null,
    evaluated_at: '2026-09-03T12:00:00Z',
  }))
}

function decision(over: Partial<Decision> = {}): Decision {
  return {
    decision_id: 'd1',
    case_id: 'c1',
    created_at: '2026-09-03T12:00:00Z',
    diagnosis: {
      failure_class: 'TEMPORARY_LIQUIDITY',
      confidence: 0.82,
      is_recoverable: true,
      evidence: [],
      timing_hint: 'PAYDAY',
      source: 'deterministic_override',
    },
    policy: { rule_id: 'POL-005', proposed_action: 'RETRY_SCHEDULED', proposed_at: '2026-09-03T12:00:00Z' },
    gate_results: chain(),
    final: { action: 'RETRY_SCHEDULED', at: '2026-09-03T12:00:00Z', verdict: 'allow' },
    suppressed: [],
    executed: [],
    ...over,
  }
}

describe('splitHops', () => {
  it('returns one hop for an unremediated decision', () => {
    const hops = splitHops(decision())
    expect(hops).toHaveLength(1)
    expect(hops![0].action).toBe('RETRY_SCHEDULED')
    expect(hops![0].verdicts).toHaveLength(GATE_COUNT)
    expect(hops![0].aggregate).toBe('ALLOW')
    expect(hops![0].remediatedBy).toBeNull()
  })

  /**
   * Regression, from a live decision on the ₹18,000 case.
   *
   * `suppressed` carries one entry per blocking gate rather than one per hop —
   * the same refused RETRY_SCHEDULED appears twice, once for G2 and once for G3.
   * An earlier version of splitHops assumed one entry per hop, found 2 where it
   * wanted 1, decided the decision was misaligned and returned remediatedBy:
   * null — which silently dropped the remediation arrow, the one thing the
   * component exists to draw.
   */
  it('splits the 22-verdict demo case and names the gate that forced the substitute', () => {
    const hops = splitHops(
      decision({
        gate_results: [
          ...chain({ G2_PREDEBIT: 'BLOCK', G3_AFA: 'BLOCK' }),
          ...chain({ G1_WINDOW: 'REPLAN' }),
        ],
        suppressed: [
          { action: 'RETRY_SCHEDULED', blocked_by: 'G2_PREDEBIT', clause_ref: 'E-mandate 2026' },
          { action: 'RETRY_SCHEDULED', blocked_by: 'G3_AFA', clause_ref: 'E-mandate 2026' },
        ],
        final: { action: 'SEND_PRE_DEBIT_NOTICE', at: '2026-09-04T02:30:00Z', verdict: 'replan' },
      }),
    )

    expect(hops).toHaveLength(2)
    expect(hops!.flatMap((h) => h.verdicts)).toHaveLength(22)

    expect(hops![0].action).toBe('RETRY_SCHEDULED')
    expect(hops![0].aggregate).toBe('BLOCK')
    expect(hops![0].blocked.map((v) => v.gate)).toEqual(['G2_PREDEBIT', 'G3_AFA'])
    // G2 sorts before G3 in the chain, so the notice precedes the AFA link.
    expect(hops![0].remediatedBy?.gate).toBe('G2_PREDEBIT')

    expect(hops![1].action).toBe('SEND_PRE_DEBIT_NOTICE')
    expect(hops![1].aggregate).toBe('REPLAN')
    expect(hops![1].remediatedBy).toBeNull()
  })

  it('handles the three-hop bound', () => {
    const hops = splitHops(
      decision({
        gate_results: [...chain({ G3_AFA: 'BLOCK' }), ...chain({ G2_PREDEBIT: 'BLOCK' }), ...chain()],
        suppressed: [
          { action: 'RETRY_SCHEDULED', blocked_by: 'G3_AFA', clause_ref: 'a' },
          { action: 'CREATE_AFA_LINK', blocked_by: 'G2_PREDEBIT', clause_ref: 'b' },
        ],
        final: { action: 'SEND_PRE_DEBIT_NOTICE', at: '2026-09-03T12:00:00Z', verdict: 'allow' },
      }),
    )
    expect(hops!.map((h) => h.action)).toEqual([
      'RETRY_SCHEDULED', 'CREATE_AFA_LINK', 'SEND_PRE_DEBIT_NOTICE',
    ])
    expect(hops!.map((h) => h.remediatedBy?.gate ?? null)).toEqual(['G3_AFA', 'G2_PREDEBIT', null])
  })

  it('takes the remediable set from the caller, so /rulebook stays the authority', () => {
    const args = {
      gate_results: [...chain({ G9_HALTED: 'BLOCK' }), ...chain()],
      suppressed: [{ action: 'RETRY_SCHEDULED', blocked_by: 'G9_HALTED', clause_ref: 'x' }],
      final: { action: 'CREATE_REAUTH_LINK', at: '2026-09-03T12:00:00Z', verdict: 'allow' as const },
    }
    // G9 is not remediable by default, so no arrow is claimed...
    expect(splitHops(decision(args))![0].remediatedBy).toBeNull()
    // ...but the live rulebook decides, not this file.
    expect(splitHops(decision(args), ['G9_HALTED'])![0].remediatedBy?.gate).toBe('G9_HALTED')
  })

  it('ranks BLOCK above REPLAN within a hop, as the chain does', () => {
    const hops = splitHops(
      decision({ gate_results: chain({ G1_WINDOW: 'REPLAN', G3_AFA: 'BLOCK' }) }),
    )
    expect(hops![0].aggregate).toBe('BLOCK')
  })

  // A shape we do not understand must not be chunked into a confident-looking lie.
  it('returns null rather than mis-chunking a ragged array', () => {
    expect(splitHops(decision({ gate_results: chain().slice(0, 7) }))).toBeNull()
    expect(splitHops(decision({ gate_results: [] }))).toBeNull()
  })

  it('falls back to what policy proposed when suppressed is empty', () => {
    const hops = splitHops(
      decision({ gate_results: [...chain({ G3_AFA: 'BLOCK' }), ...chain()], suppressed: [] }),
    )
    expect(hops).toHaveLength(2)
    expect(hops![0].action).toBe('RETRY_SCHEDULED')
    // The arrow still draws: it is read off the hop's own verdicts, not off
    // `suppressed`, so a thin decision record does not cost the substitution.
    expect(hops![0].remediatedBy?.gate).toBe('G3_AFA')
  })
})
