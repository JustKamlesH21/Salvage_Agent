/**
 * The API surface the screens read. Types mirror what api/routes/ returns —
 * in particular a decision is PRD §8.2's document, unflattened, because the
 * Decision Tape renders that shape directly rather than a projection invented
 * for the frontend.
 */

const BASE = import.meta.env.VITE_API_BASE_URL ?? '/api'

export type GateVerdict = {
  gate: string
  verdict: 'ALLOW' | 'BLOCK' | 'REPLAN'
  clause_ref: string
  detail: string | null
  evaluated_at: string | null
}

export type Executed = {
  action: string
  phase: 'pre' | 'post' | 'block'
  at: string | null
  dry_run: boolean | null
  response: Record<string, unknown> | null
  clause_ref: string | null
  note: string | null
}

export type Decision = {
  decision_id: string
  case_id: string
  created_at: string
  diagnosis: {
    failure_class: string
    confidence: number
    is_recoverable: boolean
    evidence: string[]
    timing_hint: string | null
    source: 'llm' | 'deterministic_override'
  }
  policy: { rule_id: string; proposed_action: string; proposed_at: string }
  gate_results: GateVerdict[]
  final: { action: string; at: string; verdict: 'allow' | 'block' | 'replan' }
  suppressed: { action: string; blocked_by: string; clause_ref: string }[]
  executed: Executed[]
}

export type CaseSummary = {
  id: string
  subscription_id: string | null
  customer_ref: string
  amount_paise: number
  mandate_type: string | null
  category: string | null
  state: string | null
  opened_at: string | null
  failure_class: string | null
  final_action: string | null
  final_verdict: string | null
  decision_id: string | null
}

export type Counters = {
  at_risk_paise: number
  recovered_paise: number
  recovery_rate: number
  in_flight_cases: number
  blocked_by_compliance: number
  total_cases: number
}

export type CaseDetail = {
  case: Record<string, unknown> & { customer_ref: string; amount_paise: number }
  decisions: Decision[]
}

export type Posture = {
  dry_run: boolean
  armed: boolean
  kill_switch: boolean
  live_dispatch_available: boolean
  live_dispatch_note: string
  compliance_tz: string
}

/** GET /rulebook — the gate chain and the policy rules, read off the live
 *  modules by introspection, so this cannot drift from the code it describes. */
export type Rulebook = {
  gates: { gate: string; module: string; clause_ref: string; rule_text: string }[]
  gate_order_note: string
  remediation: Record<string, string>
  max_remediation_hops: number
  policy_rules: {
    id: string
    priority: number
    when: Record<string, unknown>
    action: string
    because: string
    cites: string[]
    timing: string | null
    stop_all_retries: boolean
    max_delay_minutes: number | null
  }[]
}

/** A min-max range across seeds. Never a confidence interval — N=3 does not
 *  support one, and the protocol block says so in its own words. */
export type SeedRange = { min: number; max: number; runs: number }

/** GET /metrics/batch — eval/results.json, served verbatim. The `finding`
 *  strings are written by the harness; the UI renders them rather than
 *  restating them, so no screen can claim more than the run supports. */
export type BatchMetrics = {
  generated_at: string
  headline: {
    recovery_rate: {
      finding: string
      baseline_net: SeedRange
      salvage_net: SeedRange
      sensitivity_ranking_holds: boolean
    }
    attempt_efficiency: {
      finding: string
      baseline_attempts_per_recovery: SeedRange
      salvage_attempts_per_recovery: SeedRange
      baseline_wasted_on_hard_declines: SeedRange
      salvage_wasted_on_hard_declines: SeedRange
    }
    compliance: {
      finding: string
      baseline: { contacts_outside_0800_1900_ist: number }[]
      salvage: { contacts_outside_0800_1900_ist: number }[]
    }
    llm_contribution: {
      finding: string
      llm_calls: number
      llm_driven_decisions: number
      latency_cost_note: string
    }
  }
  protocol: { cohort_size: number; seeds: number[]; arms: string[]; reporting: string }
}

/** GET /exceptions — infrastructure failures, not judgement calls. The pipeline
 *  crashed or the hash chain could not be appended. */
export type ExceptionRow = {
  _id?: string
  case_id: string | null
  reason_code: string
  detail: string | null
  ts: string | null
  resolved_by: string | null
}

export type FeedItem = {
  decision_id: string
  case_id: string
  at: string
  failure_class: string | null
  rule_id: string | null
  action: string | null
  verdict: string | null
  blocked_by: { gate: string; clause_ref: string }[]
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${BASE}${path}`)
  if (!response.ok) {
    throw new Error(`${path} responded ${response.status}`)
  }
  return (await response.json()) as T
}

export const api = {
  cases: () => get<{ cases: CaseSummary[]; counters: Counters }>('/cases'),
  case: (id: string) => get<CaseDetail>(`/cases/${id}`),
  posture: () => get<Posture>('/control/status'),
  rulebook: () => get<Rulebook>('/rulebook'),
  batchMetrics: () => get<BatchMetrics>('/metrics/batch'),
  exceptions: () => get<{ exceptions: ExceptionRow[]; count: number }>('/exceptions'),

  async kill(engaged: boolean): Promise<Posture> {
    await fetch(`${BASE}/control/kill`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ engaged }),
    })
    return api.posture()
  },

  /** SSE, not websockets — less to go wrong on demo day (SPEC §6.4). */
  subscribe(onItem: (item: FeedItem) => void): () => void {
    const source = new EventSource(`${BASE}/stream/decisions`)
    source.addEventListener('decision', (event) => {
      try {
        onItem(JSON.parse((event as MessageEvent<string>).data) as FeedItem)
      } catch {
        // A malformed frame drops that frame; it does not take the feed down.
      }
    })
    return () => source.close()
  },
}
