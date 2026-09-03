import { useEffect, useState } from 'react'
import { api, type CaseSummary, type ExceptionRow } from '../lib/api'
import { istStamp, rupees } from '../lib/format'
import { ArrowLeft } from './icons'

/**
 * S5 — the Exception Desk. What the agent would not decide, and why.
 *
 * Two different things land here and they are not the same kind of event, so
 * they are not in the same table. A case the agent declined to act on is the
 * system working: an UNKNOWN failure class escalates to a human rather than
 * guessing at a money action. A row in `exceptions` is the system failing: the
 * pipeline raised, or the hash chain could not be appended.
 *
 * FR-18 says this is reachable in one click from the deck and not hidden behind
 * a tab. A system that only shows its successes is not showing its work.
 */

function why(item: CaseSummary): string {
  if (item.failure_class === 'UNKNOWN') {
    return 'No taxonomy rule matched the decline. Classified UNKNOWN rather than guessed at, so no money action was proposed.'
  }
  return 'Policy routed this to a human. The failure class is known, but no automated play applies to it.'
}

export function ExceptionDesk({
  onBack,
  onOpenCase,
}: {
  onBack: () => void
  onOpenCase: (id: string) => void
}) {
  const [cases, setCases] = useState<CaseSummary[] | null>(null)
  const [rows, setRows] = useState<ExceptionRow[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    Promise.all([api.cases(), api.exceptions().catch(() => ({ exceptions: [], count: 0 }))])
      .then(([deck, ex]) => {
        if (!alive) return
        setCases(
          deck.cases.filter(
            (c) => c.failure_class === 'UNKNOWN' || c.final_action === 'ESCALATE_HUMAN',
          ),
        )
        setRows(ex.exceptions)
      })
      .catch((e: Error) => alive && setError(e.message))
    return () => {
      alive = false
    }
  }, [])

  return (
    <div>
      <div className="border-b border-rule px-4 py-3">
        <button
          type="button"
          onClick={onBack}
          className="eyebrow inline-flex items-center gap-1.5 hover:text-ink"
        >
          <ArrowLeft size="sm" />
          Command Deck
        </button>
        <h1 className="font-display mt-1.5 text-figure font-semibold tracking-tight">
          Exception desk
        </h1>
        <p className="mt-2 max-w-[68ch] text-body text-dim">
          Every case the agent refused to decide, and every time the pipeline itself failed.
          Knowing where its own judgement stops is part of what the agent is for.
        </p>
      </div>

      {error ? (
        <p className="px-4 py-6 text-data text-block">
          The API is not answering. Start it with <span className="data">make dev</span>, then
          reload.
        </p>
      ) : null}

      <section className="px-4 py-6">
        <p className="eyebrow">Escalated to a human</p>
        {cases === null ? (
          <p className="mt-3 text-data text-dim">Reading the deck…</p>
        ) : cases.length === 0 ? (
          <p className="mt-3 max-w-[68ch] text-data text-dim">
            Nothing escalated. Every decline so far matched a taxonomy rule and a policy rule.
            Post a webhook carrying an unrecognised error code and it will appear here.
          </p>
        ) : (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[42rem] border-collapse">
              <thead>
                <tr className="border-y border-rule text-left">
                  <th className="eyebrow py-2 pr-4 font-normal">Case</th>
                  <th className="eyebrow py-2 pr-4 text-right font-normal">Amount</th>
                  <th className="eyebrow py-2 pr-4 font-normal">Class</th>
                  <th className="eyebrow py-2 pr-4 font-normal">Why it stopped</th>
                  <th className="eyebrow py-2 font-normal">Opened</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-rule">
                {cases.map((item) => (
                  <tr
                    key={item.id}
                    onClick={() => onOpenCase(item.id)}
                    className="cursor-pointer align-top hover:bg-white/60"
                  >
                    <td className="data py-2.5 pr-4 text-data">{item.customer_ref}</td>
                    {/* Amounts align down the column. This is a ledger. */}
                    <td className="data py-2.5 pr-4 text-right text-data">
                      {rupees(item.amount_paise)}
                    </td>
                    <td className="data py-2.5 pr-4 text-data text-replan">
                      {item.failure_class ?? '—'}
                    </td>
                    <td className="max-w-[34ch] py-2.5 pr-4 text-data-sm text-dim">{why(item)}</td>
                    <td className="data py-2.5 text-data-sm text-dim">
                      {istStamp(item.opened_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="border-t border-rule px-4 py-6">
        <p className="eyebrow">Pipeline failures</p>
        <p className="mt-2 max-w-[68ch] text-data-sm text-dim">
          Not a judgement call — the decision cycle raised, or the audit chain could not be
          appended. These are defects, and an empty table here is the good outcome.
        </p>
        {rows.length === 0 ? (
          <p className="mt-3 text-data text-allow">None recorded.</p>
        ) : (
          <div className="mt-3 divide-y divide-rule border-y border-rule">
            {rows.map((row, i) => (
              <div key={row._id ?? i} className="flex flex-wrap gap-x-4 gap-y-1 py-2.5">
                <span className="data text-data text-block">{row.reason_code}</span>
                <span className="data text-data-sm text-dim">{istStamp(row.ts)}</span>
                {row.case_id ? (
                  <button
                    type="button"
                    onClick={() => onOpenCase(row.case_id as string)}
                    className="data text-data-sm underline hover:text-ink"
                  >
                    {row.case_id}
                  </button>
                ) : null}
                {row.detail ? (
                  <span className="w-full text-data-sm [overflow-wrap:anywhere] text-dim">
                    {row.detail}
                  </span>
                ) : null}
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
