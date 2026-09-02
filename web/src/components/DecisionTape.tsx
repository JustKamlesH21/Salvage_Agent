import { useState } from 'react'
import type { Decision, Executed, GateVerdict } from '../lib/api'
import { istTime, rupees } from '../lib/format'

/**
 * The Decision Tape — SPEC §6.2's signature element.
 *
 * One decision cycle, rendered as a timecoded strip down a ruled spine. Each
 * step is a chit. Gates that passed are quiet; gates that blocked are struck
 * through with the RBI clause overprinted at an angle, like a rubber stamp.
 * That contrast is the whole product in one image: the system considered an
 * action, and something written down stopped it.
 *
 * Every gate is shown, including the ones that passed and the ones evaluated
 * after an earlier gate had already blocked — the chain runs in full, always,
 * and a tape that showed only the first blocker would be describing a
 * different system than the one that ran.
 */

const VERDICT_TONE: Record<GateVerdict['verdict'], string> = {
  ALLOW: 'text-ledger',
  BLOCK: 'text-stamp',
  REPLAN: 'text-amber',
}

const VERDICT_MARK: Record<GateVerdict['verdict'], string> = {
  ALLOW: '✓',
  BLOCK: '⊘',
  REPLAN: '↻',
}

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
      <div className="data w-[4.5rem] shrink-0 pt-[2px] text-right text-[10px] leading-5 text-dim sm:w-24">
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

function GateRow({ verdict, delay }: { verdict: GateVerdict; delay: number }) {
  const blocked = verdict.verdict === 'BLOCK'
  return (
    <div
      className="chit-enter flex flex-wrap items-center gap-x-3 gap-y-1.5 py-[3px]"
      style={{ animationDelay: `${delay}ms` }}
    >
      <span className={`data w-4 text-[11px] ${VERDICT_TONE[verdict.verdict]}`}>
        {VERDICT_MARK[verdict.verdict]}
      </span>
      <span className={`data w-36 text-[11px] ${blocked ? 'struck' : 'text-ink'}`}>
        {verdict.gate}
      </span>

      {blocked ? (
        <span
          className="stamp stamp-sm stamp-enter"
          style={{ animationDelay: `${delay + 180}ms` }}
          title={verdict.detail ?? undefined}
        >
          {verdict.clause_ref}
        </span>
      ) : (
        <span className="data text-[10px] text-dim">{verdict.clause_ref}</span>
      )}

      {blocked && verdict.detail ? (
        <span className="w-full pl-7 text-[11px] leading-snug text-dim sm:w-auto sm:pl-0">
          {verdict.detail}
        </span>
      ) : null}
    </div>
  )
}

function ExecutedRow({ row }: { row: Executed }) {
  const tone =
    row.phase === 'block' ? 'text-stamp' : row.phase === 'post' ? 'text-ledger' : 'text-dim'
  return (
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 py-[3px]">
      <span className={`data text-[11px] ${tone}`}>{row.action}</span>
      <span className="data text-[10px] text-dim">{row.phase}</span>
      {row.dry_run ? <span className="data text-[10px] text-amber">dry run</span> : null}
      <span className="data text-[10px] text-dim">{istTime(row.at)}</span>
      {row.note ? <span className="text-[11px] text-dim">{row.note}</span> : null}
    </div>
  )
}

export function DecisionTape({
  decision,
  amountPaise,
  mandateType,
  index,
}: {
  decision: Decision
  amountPaise: number
  mandateType: string | null
  index: number
}) {
  const [showDocument, setShowDocument] = useState(false)

  const at = istTime(decision.created_at)
  const { diagnosis, policy, final, gate_results: gates, suppressed, executed } = decision

  // Only the rows that record a real dispatch. The pre/post pair for one
  // action is collapsed to its post row where both exist — the tape shows
  // what happened, and the pre row's job is idempotency, not narration.
  const dispatched = executed.filter((row) => row.phase !== 'pre')

  const verdictTone =
    final.verdict === 'allow'
      ? 'text-ledger'
      : final.verdict === 'replan'
        ? 'text-amber'
        : 'text-stamp'

  let step = 0
  const nextDelay = () => (step++ * 70)

  return (
    <section className="border border-rule bg-paper">
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-rule px-4 py-2.5">
        <div className="flex items-baseline gap-3">
          <span className="eyebrow">Decision Tape</span>
          <span className="data text-[11px] text-dim">#{index + 1}</span>
        </div>
        <div className="data flex flex-wrap items-baseline gap-x-3 text-[11px] text-dim">
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
          <p className="data text-[11px]">
            payment.failed · <span className="text-stamp">{diagnosis.failure_class}</span>
          </p>
        </Row>

        <Row time={at} label="Normalise" delay={nextDelay()}>
          <p className="data text-[11px]">
            → {diagnosis.failure_class}
            <span className="ml-2 text-dim">
              [{diagnosis.source === 'llm' ? 'llm' : 'det'}]
            </span>
          </p>
        </Row>

        <Row time={at} label="Diagnose" delay={nextDelay()}>
          <p className="data text-[11px]">
            {diagnosis.is_recoverable ? 'recoverable' : 'not recoverable'} · conf{' '}
            {diagnosis.confidence.toFixed(2)}
            {diagnosis.timing_hint ? ` · hint ${diagnosis.timing_hint}` : ''}
          </p>
          {diagnosis.evidence.length > 0 ? (
            <p className="mt-1 text-[11px] leading-snug text-dim">
              {diagnosis.evidence.join(' · ')}
            </p>
          ) : null}
        </Row>

        <Row time={at} label="Policy" delay={nextDelay()}>
          <p className="data text-[11px]">
            {policy.rule_id} → {policy.proposed_action}
          </p>
        </Row>

        <Row time={at} label={`Gates · ${gates.length} evaluated`} delay={nextDelay()}>
          <div className="mt-0.5">
            {gates.map((verdict, i) => (
              <GateRow
                key={`${verdict.gate}-${i}`}
                verdict={verdict}
                delay={nextDelay()}
              />
            ))}
          </div>
        </Row>

        <Row time={istTime(final.at)} label="Verdict" delay={nextDelay()}>
          <p className="data text-[11px]">
            <span className={verdictTone}>{final.verdict.toUpperCase()}</span>
            <span className="mx-2 text-rule">·</span>
            {final.action}
          </p>
          {final.verdict === 'replan' ? (
            <p className="mt-1 text-[11px] text-amber">
              Rescheduled for {istTime(final.at)}. Gates are re-evaluated when it fires,
              not now.
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
                <p key={i} className="data text-[11px] text-dim">
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
          <pre className="data mt-3 max-h-96 overflow-auto border border-rule bg-white/50 p-3 text-[10px] leading-relaxed whitespace-pre-wrap text-dim">
            {JSON.stringify(decision, null, 2)}
          </pre>
        ) : null}
      </footer>
    </section>
  )
}
