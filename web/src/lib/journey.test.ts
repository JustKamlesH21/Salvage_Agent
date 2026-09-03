import { describe, expect, it } from 'vitest'
import type { CaseDetail, Decision, Executed } from './api'
import { journeyFor, untilLabel } from './journey'

function decision(over: Partial<Decision> = {}): Decision {
  return {
    decision_id: 'd1',
    case_id: 'c1',
    created_at: '2026-09-03T14:10:00Z',
    diagnosis: {
      failure_class: 'TEMPORARY_LIQUIDITY',
      confidence: 1,
      is_recoverable: true,
      evidence: [],
      timing_hint: null,
      source: 'deterministic_override',
    },
    policy: {
      rule_id: 'POL-005',
      proposed_action: 'RETRY_SCHEDULED',
      proposed_at: '2026-09-03T14:10:00Z',
    },
    gate_results: [],
    final: { action: 'RETRY_SCHEDULED', at: '2026-09-03T14:10:00Z', verdict: 'allow' },
    suppressed: [],
    executed: [],
    ...over,
  }
}

function detail(decisions: Decision[], caseFields: Record<string, unknown> = {}): CaseDetail {
  return {
    case: {
      customer_ref: 'cust_x',
      amount_paise: 1_800_000,
      state: 'open',
      opened_at: '2026-09-03T14:09:00Z',
      ...caseFields,
    },
    decisions,
  }
}

const post: Executed = {
  action: 'SEND_PRE_DEBIT_NOTICE',
  phase: 'post',
  at: '2026-09-04T02:30:00Z',
  dry_run: true,
  response: null,
  clause_ref: null,
  note: null,
}

describe('journeyFor', () => {
  it('a case with no decisions has only failed', () => {
    const j = journeyFor(detail([]))
    expect(j.current).toBe('failed')
    expect(j.waiting).toBeNull()
    expect(j.settled).toBe(false)
  })

  it('a replanned case is waiting, and names the gate that moved it', () => {
    const j = journeyFor(
      detail([
        decision({
          final: { action: 'NUDGE', at: '2026-09-04T02:30:00Z', verdict: 'replan' },
          gate_results: [
            { gate: 'G0_KILL', verdict: 'ALLOW', clause_ref: 'x', detail: null, evaluated_at: null },
            { gate: 'G1_WINDOW', verdict: 'REPLAN', clause_ref: 'RBI FPC', detail: null, evaluated_at: null },
          ],
        }),
      ]),
    )
    expect(j.current).toBe('waiting')
    // The point of the whole component: the ETA has a clause behind it.
    expect(j.waiting).toEqual({
      action: 'NUDGE',
      dueAt: '2026-09-04T02:30:00Z',
      movedBy: 'G1_WINDOW',
      movedFrom: '2026-09-03T14:10:00Z',
    })
  })

  it('a dispatched case has moved past waiting', () => {
    const j = journeyFor(detail([decision({ executed: [post] })]))
    expect(j.current).toBe('dispatched')
    // It never waited, so the rail marks that stage skipped rather than done.
    expect(j.stages.find((s) => s.name === 'waiting')?.state).toBe('skipped')
  })

  it('a case that dispatched something and is now waiting shows both', () => {
    // The real shape of the demo case: a pre-debit notice went out, and the
    // retry it unblocks is waiting on the 24h lead time. Marking Dispatched
    // "pending" because it sits right of the cursor would contradict the
    // timestamp printed under it.
    const j = journeyFor(
      detail([
        decision({ executed: [post] }),
        decision({
          decision_id: 'later',
          created_at: '2026-09-03T18:00:00Z',
          final: { action: 'RETRY_SCHEDULED', at: '2026-09-04T10:37:00Z', verdict: 'replan' },
          gate_results: [
            {
              gate: 'G2_PREDEBIT',
              verdict: 'REPLAN',
              clause_ref: 'x',
              detail: null,
              evaluated_at: null,
            },
          ],
        }),
      ]),
    )
    expect(j.current).toBe('waiting')
    expect(j.stages.find((s) => s.name === 'dispatched')?.state).toBe('done')
    expect(j.waiting?.movedBy).toBe('G2_PREDEBIT')
  })

  it('a block row is not a dispatch', () => {
    const j = journeyFor(detail([decision({ executed: [{ ...post, phase: 'block' }] })]))
    expect(j.current).toBe('gated')
  })

  it('a terminal action settles the case', () => {
    for (const action of ['CLOSE_NO_ACTION', 'SUPPRESS', 'ESCALATE_HUMAN']) {
      const j = journeyFor(
        detail([decision({ final: { action, at: '2026-09-03T14:10:00Z', verdict: 'allow' } })]),
      )
      expect(j.current, action).toBe('settled')
      expect(j.settled, action).toBe(true)
    }
  })

  it('a recovered case settles even mid-flight', () => {
    const j = journeyFor(detail([decision()], { state: 'recovered' }))
    expect(j.settled).toBe(true)
  })

  it('reads the latest decision, not the first', () => {
    const j = journeyFor(
      detail([
        decision({ decision_id: 'old', created_at: '2026-09-03T14:10:00Z' }),
        decision({
          decision_id: 'new',
          created_at: '2026-09-03T18:00:00Z',
          final: { action: 'NUDGE', at: '2026-09-04T02:30:00Z', verdict: 'replan' },
        }),
      ]),
    )
    expect(j.current).toBe('waiting')
    expect(j.waiting?.action).toBe('NUDGE')
  })

  it('counts verdicts across every decision on the case', () => {
    const g = { gate: 'G0_KILL', verdict: 'ALLOW' as const, clause_ref: 'x', detail: null, evaluated_at: null }
    const j = journeyFor(
      detail([decision({ gate_results: [g, g] }), decision({ decision_id: 'd2', gate_results: [g] })]),
    )
    expect(j.stages.find((s) => s.name === 'gated')?.note).toBe('3 verdicts')
  })
})

describe('untilLabel', () => {
  const now = Date.parse('2026-09-03T14:10:00Z')
  it('counts down in the units that matter', () => {
    expect(untilLabel('2026-09-03T14:14:00Z', now)).toBe('in 4m')
    expect(untilLabel('2026-09-04T02:30:00Z', now)).toBe('in 12h 20m')
    expect(untilLabel('2026-09-06T20:10:00Z', now)).toBe('in 3d 6h')
  })
  it('says due now once the moment has passed', () => {
    expect(untilLabel('2026-09-03T14:09:00Z', now)).toBe('due now')
  })
})
