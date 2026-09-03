import { useState } from 'react'
import { shortClause, splitGateName } from '../lib/clauses'
import type { GateVerdict } from '../lib/api'
import type { Hop } from '../lib/hops'
import { GATE_COUNT } from '../lib/hops'
import { Blocked, Check, ChevronDown, Replan } from './icons'

/**
 * One pass of the gate chain: the action being judged, and all eleven verdicts
 * against it.
 *
 * All eleven always render, including the ones evaluated after an earlier gate
 * had already blocked. evaluate_chain() has no short-circuit — it runs the whole
 * chain every time — and a component that stopped at the first refusal would be
 * describing a different system than the one that ran.
 *
 * The gate numbers are given their own gutter so the chain reads as a numbered
 * rail. "Eleven gates ran" is a claim the interface should let you check by
 * counting, not one you have to take on trust.
 */

const TONE: Record<GateVerdict['verdict'], string> = {
  ALLOW: 'text-allow',
  BLOCK: 'text-block',
  REPLAN: 'text-replan',
}

const MARK = { ALLOW: Check, BLOCK: Blocked, REPLAN: Replan } as const

function GateRow({
  verdict,
  ruleText,
  delay,
}: {
  verdict: GateVerdict
  ruleText: string | undefined
  delay: number
}) {
  const [open, setOpen] = useState(false)
  const blocked = verdict.verdict === 'BLOCK'
  const { number, name } = splitGateName(verdict.gate)
  const Mark = MARK[verdict.verdict]

  return (
    <div
      className="chit-enter border-b border-rule/60 last:border-b-0"
      style={{ animationDelay: `${delay}ms` }}
    >
      {/* A grid, not a wrapping flex: the number, mark, name and clause have to
          line up down the column for the chain to read as a rail, and a chevron
          that wraps onto its own line breaks the row it belongs to. */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="grid w-full grid-cols-[1.5rem_1rem_minmax(0,1fr)_1rem] items-center gap-x-2 py-2 text-left hover:bg-white/60 sm:grid-cols-[1.5rem_1rem_auto_minmax(0,1fr)_1rem]"
      >
        <span className="data text-data-sm text-dim">{number}</span>

        {/* Never colour alone: the mark, the strike and the hue all carry it. */}
        <span className={TONE[verdict.verdict]}>
          <Mark size="sm" title={verdict.verdict} />
        </span>

        <span className={`data whitespace-nowrap text-data ${blocked ? 'struck' : 'text-ink'}`}>
          {name}
        </span>

        <span className="col-span-3 col-start-2 min-w-0 sm:col-span-1 sm:col-start-4">
          {blocked ? (
            <span
              className="stamp stamp-sm stamp-enter"
              style={{ animationDelay: `${delay + 180}ms` }}
            >
              {shortClause(verdict.gate, verdict.clause_ref)}
            </span>
          ) : (
            <span
              className={`data block truncate text-data-sm ${
                verdict.verdict === 'REPLAN' ? 'text-replan' : 'text-dim'
              }`}
            >
              {shortClause(verdict.gate, verdict.clause_ref)}
            </span>
          )}
        </span>

        <span className="col-start-4 row-start-1 self-center text-dim sm:col-start-5">
          <ChevronDown size="sm" className={open ? 'rotate-180' : undefined} />
        </span>

        {verdict.detail && verdict.verdict !== 'ALLOW' ? (
          <span className="col-span-3 col-start-2 text-data-sm text-dim sm:col-span-2 sm:col-start-3">
            {verdict.detail}
          </span>
        ) : null}
      </button>

      {/* The citation in full, and the rule in the words the gate module carries.
          The stamp above is an abbreviation; this is what it abbreviates. */}
      {open ? (
        <div className="border-t border-rule/60 bg-white/50 px-3 py-3">
          <p className="eyebrow">Clause</p>
          <p className="data mt-1 text-data-sm break-words text-ink">{verdict.clause_ref}</p>
          {ruleText ? (
            <>
              <p className="eyebrow mt-3">Rule</p>
              <p className="mt-1 max-w-[68ch] text-data-sm text-dim">{ruleText}</p>
            </>
          ) : null}
          {verdict.detail ? (
            <>
              <p className="eyebrow mt-3">On this case</p>
              <p className="mt-1 text-data-sm text-dim">{verdict.detail}</p>
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

export function GateChain({
  hop,
  ruleText,
  ruleId,
  baseDelay = 0,
}: {
  hop: Hop
  /** gate → RULE_TEXT, from GET /rulebook. Absent until it loads; the row still opens. */
  ruleText?: Record<string, string>
  ruleId?: string
  baseDelay?: number
}) {
  const refused = hop.verdicts.filter((v) => v.verdict === 'BLOCK').length
  const deferred = hop.verdicts.filter((v) => v.verdict === 'REPLAN').length

  const summary =
    hop.aggregate === 'BLOCK'
      ? `${refused} of ${GATE_COUNT} gates refused`
      : hop.aggregate === 'REPLAN'
        ? `${deferred} of ${GATE_COUNT} gates asked for a later time`
        : `${GATE_COUNT} of ${GATE_COUNT} gates cleared`

  return (
    <section className="min-w-0 border border-rule bg-paper">
      <header className="border-b border-rule px-3 py-2.5">
        <p className="eyebrow">{hop.index === 0 ? 'Proposed' : 'Substituted'}</p>
        <div className="mt-1 flex flex-wrap items-baseline gap-x-3">
          <span className="data text-data text-ink">{hop.action}</span>
          {ruleId && hop.index === 0 ? (
            <span className="data text-data-sm text-dim">{ruleId}</span>
          ) : null}
        </div>
      </header>

      <div className="px-3">
        {hop.verdicts.map((verdict, i) => (
          <GateRow
            key={`${verdict.gate}-${i}`}
            verdict={verdict}
            ruleText={ruleText?.[verdict.gate]}
            delay={baseDelay + i * 45}
          />
        ))}
      </div>

      <footer className="flex flex-wrap items-baseline gap-x-3 border-t border-rule px-3 py-2.5">
        <span className={`data text-data ${TONE[hop.aggregate]}`}>
          {hop.aggregate === 'REPLAN' ? 'DEFERRED' : hop.aggregate === 'BLOCK' ? 'BLOCKED' : 'ALLOWED'}
        </span>
        <span className="text-data-sm text-dim">{summary}</span>
      </footer>
    </section>
  )
}
