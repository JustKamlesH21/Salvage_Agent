import { useEffect, useState } from 'react'
import { api, type CaseDetail } from '../lib/api'
import { istStamp, rupees } from '../lib/format'
import { DecisionTape } from './DecisionTape'

/**
 * S2 — the Case View. The header states the case in the terms a compliance
 * reviewer would ask about (amount, mandate, category, attempt budget), and
 * everything below it is Decision Tapes, newest first.
 */
export function CaseView({ caseId, onBack }: { caseId: string; onBack: () => void }) {
  const [detail, setDetail] = useState<CaseDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    api
      .case(caseId)
      .then((data) => alive && setDetail(data))
      .catch((e: Error) => alive && setError(e.message))
    return () => {
      alive = false
    }
  }, [caseId])

  if (error) {
    return (
      <div className="px-4 py-8">
        <p className="text-[13px] text-stamp">{error}</p>
        <button type="button" onClick={onBack} className="eyebrow mt-3 hover:text-ink">
          ← Back to the deck
        </button>
      </div>
    )
  }

  if (!detail) {
    return <p className="px-4 py-8 text-[12px] text-dim">Reading the case…</p>
  }

  const record = detail.case as Record<string, unknown>
  const attempts = detail.decisions.filter((d) =>
    ['RETRY_NOW', 'RETRY_SCHEDULED'].includes(d.final.action),
  ).length

  const facts: [string, string][] = [
    ['Customer', detail.case.customer_ref],
    ['Amount', rupees(detail.case.amount_paise)],
    ['Mandate', String(record.mandate_type ?? 'card')],
    ['Category', String(record.category ?? 'standard')],
    ['Subscription', String(record.subscription_status ?? 'active')],
    ['Attempts', `${attempts} of 4 per 14 days`],
    ['State', String(record.state ?? 'open')],
    ['Opened', istStamp(record.opened_at as string | null)],
  ]

  return (
    <div>
      <div className="border-b border-rule px-4 py-3">
        <button type="button" onClick={onBack} className="eyebrow hover:text-ink">
          ← Command Deck
        </button>
        <h1 className="font-display mt-1.5 text-[19px] font-semibold tracking-tight">
          Case {detail.case.customer_ref}
        </h1>
      </div>

      <dl className="grid grid-cols-2 border-b border-rule sm:grid-cols-4">
        {facts.map(([label, value]) => (
          <div key={label} className="border-rule px-4 py-3 not-last:border-r not-last:border-b">
            <dt className="eyebrow">{label}</dt>
            <dd className="data mt-1 text-[12px]">{value}</dd>
          </div>
        ))}
      </dl>

      <div className="space-y-4 p-4">
        {detail.decisions.length === 0 ? (
          <p className="text-[12px] text-dim">
            This case has no decisions yet. That means a webhook opened it and the pipeline
            has not run — check the exception desk.
          </p>
        ) : (
          [...detail.decisions]
            .reverse()
            .map((decision, i) => (
              <DecisionTape
                key={decision.decision_id}
                decision={decision}
                amountPaise={detail.case.amount_paise}
                mandateType={String(record.mandate_type ?? 'card')}
                index={detail.decisions.length - 1 - i}
              />
            ))
        )}
      </div>
    </div>
  )
}
