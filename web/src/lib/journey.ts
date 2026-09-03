/**
 * Where a case is, and what it is waiting for.
 *
 * The Decision Tape shows one decision's reasoning in full. It does not answer
 * the question anyone actually asks first — *what is happening to this, and
 * when does the next thing happen* — because a decision is a moment and a case
 * is a journey.
 *
 * The stages are derived, not stored. Nothing in the database says "this case
 * is at stage 4"; the stage is a reading of the decisions and the audit rows,
 * which means it cannot disagree with them.
 *
 * The detail worth the whole component: unlike a parcel, this ETA moves for a
 * *reason*, and the reason is a clause. A retry proposed at 19:32 that shows
 * 08:00 tomorrow was not delayed by traffic — G1_WINDOW moved it, because the
 * RBI Fair Practices Code says nobody gets contacted after seven.
 */

import type { CaseDetail, Decision } from './api'

export type StageName = 'failed' | 'diagnosed' | 'gated' | 'waiting' | 'dispatched' | 'settled'

export type StageState = 'done' | 'current' | 'pending' | 'skipped'

export type Stage = {
  name: StageName
  label: string
  state: StageState
  /** ISO timestamp this stage was reached, when it was. */
  at: string | null
  note: string | null
}

export type Waiting = {
  action: string
  /** ISO. When the gates re-run and the action is reconsidered. */
  dueAt: string
  /** The gate that moved it, when one did. */
  movedBy: string | null
  /** ISO. What it was proposed for before the gate moved it. */
  movedFrom: string | null
}

export type Journey = {
  stages: Stage[]
  current: StageName
  waiting: Waiting | null
  /** True once nothing further is owed on this case. */
  settled: boolean
}

const TERMINAL_ACTIONS = new Set(['CLOSE_NO_ACTION', 'SUPPRESS', 'ESCALATE_HUMAN'])
const SETTLED_STATES = new Set(['recovered', 'terminal', 'exception'])

const ORDER: { name: StageName; label: string }[] = [
  { name: 'failed', label: 'Charge failed' },
  { name: 'diagnosed', label: 'Diagnosed' },
  { name: 'gated', label: 'Gated' },
  { name: 'waiting', label: 'Waiting' },
  { name: 'dispatched', label: 'Dispatched' },
  { name: 'settled', label: 'Settled' },
]

function latest(decisions: Decision[]): Decision | null {
  if (decisions.length === 0) return null
  return decisions.reduce((a, b) => (a.created_at >= b.created_at ? a : b))
}

export function journeyFor(detail: CaseDetail): Journey {
  const record = detail.case as Record<string, unknown>
  const decisions = detail.decisions
  const last = latest(decisions)

  const openedAt = (record.opened_at as string | null) ?? null
  const state = String(record.state ?? 'open')

  // A dispatch is an audit row that actually went out — a `block` row is the
  // record of something refused, not something sent.
  const dispatched = decisions
    .flatMap((d) => d.executed)
    .filter((row) => row.phase === 'post')
    .sort((a, b) => (a.at ?? '').localeCompare(b.at ?? ''))

  const isReplan = last?.final.verdict === 'replan'
  const isTerminal =
    (last !== null && TERMINAL_ACTIONS.has(last.final.action)) || SETTLED_STATES.has(state)

  let waiting: Waiting | null = null
  if (isReplan && last) {
    const mover = last.gate_results.find((v) => v.verdict === 'REPLAN')
    waiting = {
      action: last.final.action,
      dueAt: last.final.at,
      movedBy: mover?.gate ?? null,
      movedFrom: last.policy.proposed_at ?? last.created_at,
    }
  }

  let current: StageName
  if (isTerminal) current = 'settled'
  else if (waiting) current = 'waiting'
  else if (dispatched.length > 0) current = 'dispatched'
  else if (last) current = 'gated'
  else current = 'failed'

  const reachedIndex = ORDER.findIndex((s) => s.name === current)

  const stages: Stage[] = ORDER.map((stage, i) => {
    let at: string | null = null
    let note: string | null = null

    switch (stage.name) {
      case 'failed':
        at = openedAt
        break
      case 'diagnosed':
        at = decisions[0]?.created_at ?? null
        note = decisions[0]?.diagnosis.failure_class ?? null
        break
      case 'gated': {
        at = last?.created_at ?? null
        const total = decisions.reduce((n, d) => n + d.gate_results.length, 0)
        note = total > 0 ? `${total} verdicts` : null
        break
      }
      case 'waiting':
        at = waiting?.dueAt ?? null
        note = waiting ? waiting.action : null
        break
      case 'dispatched':
        at = dispatched.at(-1)?.at ?? null
        note = dispatched.at(-1)?.action ?? null
        break
      case 'settled':
        at = (record.closed_at as string | null) ?? null
        note = last && TERMINAL_ACTIONS.has(last.final.action) ? last.final.action : null
        break
    }

    // Evidence decides, not position. A case can have dispatched a pre-debit
    // notice AND be waiting on the retry that notice unblocks — the notice
    // genuinely went out, and a rail that called that stage "pending" because
    // it sits to the right of the cursor would be contradicting the timestamp
    // printed underneath it. A stage before the cursor with nothing to show for
    // it was not skipped over in error; there was simply nothing to do.
    let stageState: StageState
    if (i === reachedIndex) stageState = 'current'
    else if (at !== null) stageState = 'done'
    else if (i < reachedIndex) stageState = 'skipped'
    else stageState = 'pending'

    return { ...stage, state: stageState, at, note }
  })

  return { stages, current, waiting, settled: isTerminal }
}

/** `in 12h 20m`, `in 4m`, `due now`. The countdown to the gates re-running. */
export function untilLabel(iso: string, now: number = Date.now()): string {
  const ms = new Date(iso).getTime() - now
  if (Number.isNaN(ms)) return '—'
  if (ms <= 0) return 'due now'
  const mins = Math.floor(ms / 60000)
  if (mins < 60) return `in ${mins}m`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `in ${hours}h ${mins % 60}m`
  return `in ${Math.floor(hours / 24)}d ${hours % 24}h`
}


/**
 * The same reading, from a case summary rather than a full case.
 *
 * The deck has one row per case and no decision documents, so this sees less
 * than journeyFor() does — it cannot know about dispatches. It deliberately
 * reports `gated` rather than guessing at `dispatched`: a board that overstates
 * where a case has got to is worse than one that understates it.
 */
export function stageForSummary(summary: {
  state: string | null
  final_action: string | null
  final_verdict: string | null
}): StageName {
  if (summary.state && SETTLED_STATES.has(summary.state)) return 'settled'
  if (summary.final_action && TERMINAL_ACTIONS.has(summary.final_action)) return 'settled'
  if (summary.final_verdict === 'replan') return 'waiting'
  if (summary.final_verdict === 'allow') return 'dispatched'
  if (summary.final_verdict) return 'gated'
  return 'failed'
}

export const STAGE_LABEL: Record<StageName, string> = {
  failed: 'Failed',
  diagnosed: 'Diagnosed',
  gated: 'Gated',
  waiting: 'Waiting',
  dispatched: 'Dispatched',
  settled: 'Settled',
}
