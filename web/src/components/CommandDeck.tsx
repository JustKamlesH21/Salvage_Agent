import { useEffect, useMemo, useState } from 'react'
import { api, type CaseSummary, type Counters, type FeedItem } from '../lib/api'
import { ago, istStamp, percent, rupees, rupeesBare } from '../lib/format'
import {
  STAGE_LABEL,
  stageForSummary,
  untilLabel,
  type StageName,
} from '../lib/journey'

/**
 * S1 — the Command Deck.
 *
 * Four counters, recovery by failure class, and the live decision feed.
 * `Blocked by compliance` is given exactly the same weight as `Recovered`,
 * which is deliberate and is the whole argument of the product: restraint is
 * an outcome, not an absence of one (SPEC §6.3).
 */

function Counter({
  label,
  value,
  sub,
  tone = 'text-ink',
}: {
  label: string
  value: string
  sub?: string
  tone?: string
}) {
  return (
    <div className="border-rule px-4 py-4 not-last:border-b sm:not-last:border-r sm:not-last:border-b-0">
      <div className="eyebrow">{label}</div>
      <div className={`font-display mt-1.5 text-figure leading-none font-semibold tabular-nums ${tone}`}>
        {value}
      </div>
      {sub ? <div className="data mt-1.5 text-data-sm text-dim">{sub}</div> : null}
    </div>
  )
}

function ClassBars({ cases }: { cases: CaseSummary[] }) {
  const rows = useMemo(() => {
    const byClass = new Map<string, { total: number; recovered: number; value: number }>()
    for (const item of cases) {
      const key = item.failure_class ?? 'UNCLASSIFIED'
      const row = byClass.get(key) ?? { total: 0, recovered: 0, value: 0 }
      row.total += 1
      row.value += item.amount_paise
      if (item.state === 'recovered') row.recovered += 1
      byClass.set(key, row)
    }
    return [...byClass.entries()]
      .map(([name, row]) => ({ name, ...row, rate: row.total ? row.recovered / row.total : 0 }))
      .sort((a, b) => b.total - a.total)
  }, [cases])

  if (rows.length === 0) {
    return (
      <p className="px-4 py-6 text-data text-dim">
        No classified cases yet. Send a <span className="data">payment.failed</span> webhook
        and the classes will appear here as cases open.
      </p>
    )
  }

  return (
    <div className="divide-y divide-rule">
      {rows.map((row) => (
        <div key={row.name} className="flex items-center gap-3 px-4 py-2">
          {/* Ten cells, filled to the recovery rate — a ledger tally, not a
              gradient progress bar. */}
          <span className="flex shrink-0 gap-[2px]" aria-hidden>
            {Array.from({ length: 10 }, (_, i) => (
              <span
                key={i}
                className={`h-3 w-[7px] border ${
                  i < Math.round(row.rate * 10)
                    ? 'border-ledger bg-ledger'
                    : 'border-rule bg-transparent'
                }`}
              />
            ))}
          </span>
          <span className="data min-w-0 flex-1 truncate text-data">{row.name}</span>
          <span className="data shrink-0 text-data text-dim">{row.total} cases</span>
          <span className="data w-12 shrink-0 text-right text-data">
            {percent(row.rate, 0)}
          </span>
        </div>
      ))}
    </div>
  )
}

function Feed({ items, onOpen }: { items: FeedItem[]; onOpen: (id: string) => void }) {
  if (items.length === 0) {
    return (
      <p className="px-4 py-6 text-data text-dim">
        Listening. Decisions appear here the moment a webhook arrives — nothing has come
        through yet.
      </p>
    )
  }

  return (
    <div className="divide-y divide-rule">
      {items.map((item) => (
        <button
          key={item.decision_id}
          type="button"
          onClick={() => onOpen(item.case_id)}
          className="block w-full px-4 py-2.5 text-left hover:bg-white/60"
        >
          <div className="flex items-baseline gap-2">
            <span className="data text-data-sm text-dim">{ago(item.at)}</span>
            <span className="data truncate text-data">case {item.case_id.slice(-6)}</span>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
            <span
              className={`data text-data ${
                item.verdict === 'allow'
                  ? 'text-allow'
                  : item.verdict === 'replan'
                    ? 'text-amber'
                    : 'text-block'
              }`}
            >
              {item.action}
            </span>
            {item.blocked_by.slice(0, 2).map((block) => (
              <span key={block.gate} className="stamp stamp-sm">
                {block.gate}
              </span>
            ))}
            {item.blocked_by.length > 2 ? (
              <span className="data text-data-sm text-dim">
                +{item.blocked_by.length - 2}
              </span>
            ) : null}
          </div>
        </button>
      ))}
    </div>
  )
}

const STAGE_TONE: Partial<Record<StageName, string>> = {
  waiting: 'text-replan',
  dispatched: 'text-allow',
  settled: 'text-dim',
}

function StageCell({ summary }: { summary: CaseSummary }) {
  const stage = stageForSummary(summary)
  return <span className={STAGE_TONE[stage] ?? 'text-ink'}>{STAGE_LABEL[stage]}</span>
}

/** What the case is waiting on, and how long. A row that says "in 12h 20m ·
 *  G1_WINDOW" tells you both that nothing is stuck and why it is not moving. */
function NextCell({ summary, now }: { summary: CaseSummary; now: number }) {
  if (summary.final_verdict !== 'replan' || !summary.final_at) {
    return <span className="text-dim">—</span>
  }
  return (
    <span className="flex flex-wrap items-baseline gap-x-2">
      <span className="text-replan">{untilLabel(summary.final_at, now)}</span>
      {summary.replanned_by ? <span className="text-dim">{summary.replanned_by}</span> : null}
    </span>
  )
}

export function CommandDeck({ onOpenCase }: { onOpenCase: (id: string) => void }) {
  const [cases, setCases] = useState<CaseSummary[]>([])
  const [counters, setCounters] = useState<Counters | null>(null)
  const [feed, setFeed] = useState<FeedItem[]>([])
  const [error, setError] = useState<string | null>(null)
  const [now, setNow] = useState(() => Date.now())

  // The deck polls every 4s; the countdowns move every second in between, so
  // the board reads as live rather than as a table that occasionally redraws.
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])

  useEffect(() => {
    let alive = true
    const load = () =>
      api
        .cases()
        .then((data) => {
          if (!alive) return
          setCases(data.cases)
          setCounters(data.counters)
          setError(null)
        })
        .catch((e: Error) => alive && setError(e.message))

    void load()
    const timer = setInterval(() => void load(), 4000)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [])

  useEffect(
    () =>
      api.subscribe((item) =>
        setFeed((current) => [item, ...current.filter((x) => x.decision_id !== item.decision_id)].slice(0, 40)),
      ),
    [],
  )

  return (
    <div>
      <div className="border-b border-rule px-4 py-3">
        <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2">
          <div>
            <h1 className="font-display text-lead font-semibold tracking-tight">Command Deck</h1>
            <p className="mt-0.5 text-data text-dim">
              Every automated money decision, and every one the rules stopped.
            </p>
          </div>
          {/* FR-18: one click from the deck, not behind a tab. */}
          <nav className="flex flex-wrap items-center gap-x-5 gap-y-1">
            <a href="#/exceptions" className="eyebrow hover:text-ink hover:underline">
              Exception desk
            </a>
            <a href="#/how-it-works" className="eyebrow hover:text-ink hover:underline">
              How it works
            </a>
          </nav>
        </div>
      </div>

      {error ? (
        <p className="border-b border-stamp bg-stamp/5 px-4 py-2.5 text-data text-block">
          The API is not answering ({error}). Start it with{' '}
          <span className="data">make dev</span> and this will fill in.
        </p>
      ) : null}

      <section className="grid grid-cols-1 border-b border-rule sm:grid-cols-4">
        <Counter
          label="At risk"
          value={rupees(counters?.at_risk_paise ?? 0)}
          sub={`${counters?.total_cases ?? 0} cases`}
        />
        <Counter
          label="Recovered"
          value={rupees(counters?.recovered_paise ?? 0)}
          sub={percent(counters?.recovery_rate ?? 0)}
          tone="text-allow"
        />
        <Counter
          label="In flight"
          value={String(counters?.in_flight_cases ?? 0)}
          sub="cases being worked"
        />
        <Counter
          label="Blocked by compliance"
          value={String(counters?.blocked_by_compliance ?? 0)}
          sub="actions a clause stopped"
          tone="text-block"
        />
      </section>

      <div className="grid grid-cols-1 lg:grid-cols-2">
        <section className="border-b border-rule lg:border-r lg:border-b-0">
          <h2 className="eyebrow border-b border-rule px-4 py-2">Recovery by failure class</h2>
          <ClassBars cases={cases} />
        </section>

        <section>
          <h2 className="eyebrow border-b border-rule px-4 py-2">Live decision feed</h2>
          <Feed items={feed} onOpen={onOpenCase} />
        </section>
      </div>

      <section className="border-t border-rule">
        <h2 className="eyebrow border-b border-rule px-4 py-2">Cases</h2>
        {cases.length === 0 ? (
          <p className="px-4 py-6 text-data text-dim">
            No cases yet. Post a <span className="data">payment.failed</span> webhook to{' '}
            <span className="data">/webhooks/razorpay</span> and one opens here.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
            <thead>
              <tr className="eyebrow border-b border-rule text-left">
                <th className="px-4 py-2 font-normal">Case</th>
                <th className="px-4 py-2 font-normal">Class</th>
                <th className="hidden px-4 py-2 font-normal sm:table-cell">Action</th>
                <th className="px-4 py-2 font-normal">Stage</th>
                <th className="hidden px-4 py-2 font-normal lg:table-cell">Next</th>
                <th className="px-4 py-2 text-right font-normal">Amount ₹</th>
                <th className="hidden px-4 py-2 font-normal md:table-cell">Opened</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-rule">
              {cases.map((item) => (
                <tr
                  key={item.id}
                  onClick={() => onOpenCase(item.id)}
                  className="cursor-pointer hover:bg-white/60"
                >
                  <td className="data px-4 py-2 whitespace-nowrap text-data">
                    {/* The focusable control. The row's onClick is a convenience
                        for a mouse; this is what a keyboard reaches. */}
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation()
                        onOpenCase(item.id)
                      }}
                      className="hover:underline"
                    >
                      {item.customer_ref}
                    </button>
                  </td>
                  <td className="data px-4 py-2 whitespace-nowrap text-data">{item.failure_class ?? '—'}</td>
                  <td className="data hidden px-4 py-2 whitespace-nowrap text-data sm:table-cell">
                    <span
                      className={
                        item.final_verdict === 'allow'
                          ? 'text-allow'
                          : item.final_verdict === 'replan'
                            ? 'text-amber'
                            : 'text-block'
                      }
                    >
                      {item.final_action ?? '—'}
                    </span>
                  </td>
                  <td className="data px-4 py-2 whitespace-nowrap text-data">
                    <StageCell summary={item} />
                  </td>
                  <td className="data hidden px-4 py-2 whitespace-nowrap text-data-sm lg:table-cell">
                    <NextCell summary={item} now={now} />
                  </td>
                  <td className="data px-4 py-2 text-right text-data">
                    {rupeesBare(item.amount_paise)}
                  </td>
                  <td className="data hidden px-4 py-2 whitespace-nowrap text-data-sm text-dim md:table-cell">
                    {istStamp(item.opened_at)}
                  </td>
                </tr>
              ))}
            </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
