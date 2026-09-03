/**
 * Splitting a decision's gate verdicts back into remediation hops.
 *
 * `resolve_with_remediation()` (api/gates/registry.py) re-gates every substitute
 * action through the whole eleven-gate chain and accumulates each pass into one
 * flat `gate_results` array. So a decision that was blocked once and remediated
 * once carries twenty-two verdicts, not eleven, and up to forty-four at
 * MAX_REMEDIATION_HOPS = 3.
 *
 * Rendering that array flat — which is what the tape did — puts G0_KILL on the
 * page twice with nothing to say why, and loses the one thing worth seeing: the
 * agent proposed an action, a gate refused it, and it came back with a lawful
 * substitute and submitted that to the same eleven gates.
 */

import type { Decision, GateVerdict } from './api'

/** api/gates/registry.py GATES_IN_ORDER. */
export const GATE_COUNT = 11

/**
 * api/gates/registry.py REMEDIATION — the gates whose BLOCK has a compensating
 * action rather than being terminal. Chain order decides which wins when several
 * block at once, so G2 supplies a notice before G3 is asked for a link.
 *
 * A default only. GET /rulebook serves the live map, and callers should pass its
 * keys so a gate that becomes remediable in the backend becomes remediable here
 * without an edit.
 */
export const DEFAULT_REMEDIABLE_GATES = ['G2_PREDEBIT', 'G3_AFA']

export type HopAggregate = 'ALLOW' | 'BLOCK' | 'REPLAN'

export type Hop = {
  index: number
  /** The action this pass of the chain was judging. */
  action: string
  verdicts: GateVerdict[]
  aggregate: HopAggregate
  blocked: GateVerdict[]
  /**
   * The gate whose BLOCK caused the substitution that follows this hop, and the
   * clause it cited. Null on the final hop, which was not substituted for.
   */
  remediatedBy: { gate: string; clause_ref: string } | null
}

/**
 * Aggregate the way the chain does: any BLOCK wins over any REPLAN, which wins
 * over ALLOW. Deliberately mirrors evaluate_chain() rather than reading
 * `final.verdict`, because `final` describes the decision, not this hop.
 */
function aggregate(verdicts: GateVerdict[]): HopAggregate {
  if (verdicts.some((v) => v.verdict === 'BLOCK')) return 'BLOCK'
  if (verdicts.some((v) => v.verdict === 'REPLAN')) return 'REPLAN'
  return 'ALLOW'
}

/**
 * Returns one Hop per pass of the gate chain, or null when the array is not a
 * whole number of chains — in which case the caller should render flat and say
 * so, rather than chunk a shape we do not understand into a confident-looking lie.
 */
export function splitHops(decision: Decision, remediableGates?: string[]): Hop[] | null {
  const results = decision.gate_results
  if (results.length === 0 || results.length % GATE_COUNT !== 0) return null

  const count = results.length / GATE_COUNT
  const remediable = new Set(remediableGates ?? DEFAULT_REMEDIABLE_GATES)

  // `suppressed` carries one entry per blocking gate, not one per hop — a single
  // refused RETRY_SCHEDULED that tripped both G2 and G3 appears in it twice. So
  // the actions the earlier hops were judging are its DISTINCT actions in order
  // of first appearance; the last hop is the one `final` describes.
  const proposals: string[] = []
  for (const item of decision.suppressed) {
    if (!proposals.includes(item.action)) proposals.push(item.action)
  }
  const aligned = proposals.length === count - 1

  return Array.from({ length: count }, (_, i) => {
    const verdicts = results.slice(i * GATE_COUNT, (i + 1) * GATE_COUNT)
    const last = i === count - 1
    const blocked = verdicts.filter((v) => v.verdict === 'BLOCK')

    let action: string
    if (last) action = decision.final.action
    else if (aligned) action = proposals[i]
    else if (i === 0) action = decision.policy.proposed_action
    else action = 'UNKNOWN'

    // Which gate forced the substitution: the first remediable blocker in chain
    // order, read off this hop's own verdicts. Deriving it from the hop rather
    // than from `suppressed` keeps the ordering rule that picked it visible even
    // when several gates blocked at once.
    const cause = blocked.find((v) => remediable.has(v.gate))

    return {
      index: i,
      action,
      verdicts,
      aggregate: aggregate(verdicts),
      blocked,
      remediatedBy: last || !cause ? null : { gate: cause.gate, clause_ref: cause.clause_ref },
    }
  })
}
