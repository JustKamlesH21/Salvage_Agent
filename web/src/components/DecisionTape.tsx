import { useState } from 'react'
import type { Decision, Executed, GateVerdict } from '../lib/api'
import { splitHops } from '../lib/hops'
import { istTime, rupees } from '../lib/format'
import { GateChain } from './GateChain'
import { RemediationTrail } from './RemediationTrail'

/**
 * The Decision Tape — SPEC §6.2's signature element.
 *
 * One decision cycle, rendered as a timecoded strip down a ruled spine. Each
 * step is a chit. Gates that passed are quiet; gates that blocked are struck
 * through with the clause overprinted at an angle, like a rubber stamp. That
 * contrast is the whole product in one image: the system considered an action,
 * and something written down stopped it.
 *
 * The gates step delegates to GateChain, which splits the verdicts back into the
 * passes that produced them. Before that split existed this row rendered all
 * twenty-two verdicts of a remediated decision as one flat list, with G0_KILL
 * appearing twice and nothing on the page to say why.
 */

function Row({
  time,
  label,
  children,
  delay,
}: {
  time?: string
  label: string
  children: React.ReactNode
  delay: number
}) {
  return (
    <div className="chit-enter relative flex gap-3 sm:gap-4" style={{ animationDelay: `${delay}ms` }}>
      <div className="data w-gutter shrink-0 pt-[2px] text-right text-data-sm text-dim sm:w-gutter-lg">
        {time ?? ''}
      </div>
      <div className="relative flex shrink-0 flex-col items-center">
        <span className="mt-[7px] size-[7px] shrink-0 rotate-45 border border-ink bg-paper" />
      </div>
      <div className="min-w-0 flex-1 pb-5">
        <div className="eyebrow mb-1">{label}</div>
        {children}
      </div>
    </div>
  )
}

function ExecutedRow({ row }: { row: Executed }) {
  const tone = row.phase === 'block' ? 'text-block' : row.phase === 'post' ? 'text-allow' : 'text-dim'
  return (
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 py-[3px]">
      <span className={`data text-data ${tone}`}>{row.action}</span>
      <span className="data text-data-sm text-dim">{row.phase}</span>
      {row.dry_run ? <span className="data text-data-sm text-replan">dry run</span> : null}
      <span className="data text-data-sm text-dim">{istTime(row.at)}</span>
      {row.note ? (
        <span className="data-wrap text-data-sm text-dim">{row.note}</span>
      ) : null}
    </div>
  )
}

/** Fallback for a gate_results array that is not a whole number of chains. We do
 *  not know its shape, so we render it plainly and say so rather than chunking it
 *  into something that would look authoritative and be wrong. */
function UnsplitGates({ gates }: { gates: GateVerdict[] }) {
  return (
    <div>
      <p className="mb-2 text-data-sm text-block">
        {gates.length} verdicts do not divide into whole passes of the chain. Showing them
        unsplit — read this as a defect in the decision record, not as the chain's order.
      </p>
      {gates.map((verdict, i) => (
        <p key={`${verdict.gate}-${i}`} className="data text-data-sm">
          <span className={verdict.verdict === 'BLOCK' ? 'struck' : ''}>{verdict.gate}</span>
          <span className="mx-2 text-dim">{verdict.verdict}</span>
          <span className="text-dim">{verdict.clause_ref}</span>
        </p>
      ))}
    </div>
  )
}

export function DecisionTape({
  decision,
  amountPaise,
  mandateType,
  index,
  ruleText,
  remediableGates,
}: {
  decision: Decision
  amountPaise: number
  mandateType: string | null
  index: number
  /** gate → RULE_TEXT from GET /rulebook, for the per-gate drawer. */
  ruleText?: Record<string, string>
  /** Keys of GET /rulebook's remediation map, so the substitution arrow follows
   *  the backend rather than a constant copied into the frontend. */
  remediableGates?: string[]
}) {
  const [showDocument, setShowDocument] = useState(false)

  const at = istTime(decision.created_at)
  const { diagnosis, policy, final, gate_results: gates, suppressed, executed } = decision
  const hops = splitHops(decision, remediableGates)

  // Only the rows that record a real dispatch. The pre/post pair for one action
  // is collapsed to its post row where both exist — the tape shows what
  // happened, and the pre row's job is idempotency, not narration.
  const dispatched = executed.filter((row) => row.phase !== 'pre')

  const verdictTone =
    final.verdict === 'allow' ? 'text-allow' : final.verdict === 'replan' ? 'text-replan' : 'text-block'

  // The count is printed by whatever renders below, once.
  const gatesLabel = hops === null ? `Gates · ${gates.length} verdicts` : 'Gates'

  let step = 0
  const nextDelay = () => step++ * 70

  return (
    <section className="border border-rule bg-paper">
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-rule px-4 py-2.5">
        <div className="flex items-baseline gap-3">
          <span className="eyebrow">Decision Tape</span>
          <span className="data text-data-sm text-dim">#{index + 1}</span>
        </div>
        <div className="data flex flex-wrap items-baseline gap-x-3 text-data-sm text-dim">
          <span>{rupees(amountPaise)}</span>
          <span>{mandateType ?? 'card'} mandate</span>
          <span>{at}</span>
        </div>
      </header>

      <div className="tape-paper relative px-4 py-5">
        {/* The spine the chits hang from. Drawn once, on open. */}
        <div
          className="spine-draw absolute top-5 bottom-5 left-[5.4rem] w-px bg-rule sm:left-[7.15rem]"
          aria-hidden
        />

        <Row time={at} label="Event" delay={nextDelay()}>
          <p className="data text-data">
            payment.failed · <span className="text-block">{diagnosis.failure_class}</span>
          </p>
        </Row>

        <Row time={at} label="Normalise" delay={nextDelay()}>
          <p className="data text-data">
            → {diagnosis.failure_class}
            <span className="ml-2 text-dim">[{diagnosis.source === 'llm' ? 'llm' : 'det'}]</span>
          </p>
        </Row>

        <Row time={at} label="Diagnose" delay={nextDelay()}>
          <p className="data text-data">
            {diagnosis.is_recoverable ? 'recoverable' : 'not recoverable'} · conf{' '}
            {diagnosis.confidence.toFixed(2)}
            {diagnosis.timing_hint ? ` · hint ${diagnosis.timing_hint}` : ''}
          </p>
          {diagnosis.evidence.length > 0 ? (
            <p className="mt-1 text-data-sm text-dim">{diagnosis.evidence.join(' · ')}</p>
          ) : null}
        </Row>

        <Row time={at} label="Policy" delay={nextDelay()}>
          <p className="data text-data">
            {policy.rule_id} → {policy.proposed_action}
          </p>
        </Row>

        <Row time={at} label={gatesLabel} delay={nextDelay()}>
          <div className="mt-1">
            {hops === null ? (
              <UnsplitGates gates={gates} />
            ) : hops.length > 1 ? (
              <RemediationTrail hops={hops} ruleText={ruleText} ruleId={policy.rule_id} />
            ) : (
              <GateChain hop={hops[0]} ruleText={ruleText} ruleId={policy.rule_id} />
            )}
          </div>
        </Row>

        <Row time={istTime(final.at)} label="Verdict" delay={nextDelay()}>
          <p className="data text-data">
            <span className={verdictTone}>{final.verdict.toUpperCase()}</span>
            <span className="mx-2 text-rule">·</span>
            {final.action}
          </p>
          {final.verdict === 'replan' ? (
            <p className="mt-1 text-data-sm text-replan">
              Rescheduled for {istTime(final.at)}. Gates are re-evaluated when it fires, not now.
            </p>
          ) : null}
        </Row>

        {dispatched.length > 0 ? (
          <Row label="Executed" delay={nextDelay()}>
            <div className="mt-0.5">
              {dispatched.map((row, i) => (
                <ExecutedRow key={`${row.action}-${row.phase}-${i}`} row={row} />
              ))}
            </div>
          </Row>
        ) : null}

        {suppressed.length > 0 ? (
          <Row label="Suppressed" delay={nextDelay()}>
            <div className="mt-0.5 space-y-1">
              {suppressed.map((item, i) => (
                // A clause ref is one 68-character token — RBI/DPSS/2026-27/396
                // has nothing to break on — so it needs to be allowed to reflow
                // anywhere, or it pushes the whole page sideways at 375px.
                <p key={i} className="data data-wrap text-data-sm text-dim">
                  <span className="struck">{item.action}</span>
                  <span className="mx-2">·</span>
                  {item.blocked_by}
                  <span className="mx-2 text-rule">·</span>
                  {item.clause_ref}
                </p>
              ))}
            </div>
          </Row>
        ) : null}
      </div>

      <footer className="border-t border-rule px-4 py-2">
        <button
          type="button"
          onClick={() => setShowDocument((open) => !open)}
          className="eyebrow hover:text-ink"
        >
          {showDocument ? 'Hide decision document' : 'Read the decision document'}
        </button>
        {showDocument ? (
          <pre className="data mt-3 max-h-96 overflow-auto border border-rule bg-white/50 p-3 text-data-sm leading-relaxed whitespace-pre-wrap text-dim">
            {JSON.stringify(decision, null, 2)}
          </pre>
        ) : null}
      </footer>
    </section>
  )
}
