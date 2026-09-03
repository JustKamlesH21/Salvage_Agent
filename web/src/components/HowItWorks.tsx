import { useEffect, useMemo, useState } from 'react'
import {
  api,
  type BatchMetrics,
  type CaseDetail,
  type Decision,
  type Rulebook,
  type SeedRange,
} from '../lib/api'
import { splitGateName } from '../lib/clauses'
import { splitHops, type Hop } from '../lib/hops'
import { percent } from '../lib/format'
import { RemediationTrail } from './RemediationTrail'
import { ArrowLeft, ArrowRight, Replan, Shield, Split, Stamp } from './icons'

/**
 * The explainer. What the agent does, why there are eleven gates, and what the
 * batch actually showed — for someone who has five minutes and has never seen
 * the system before.
 *
 * Everything here is read from the API rather than written into the page. The
 * gates come from GET /rulebook, which introspects the live gate modules; the
 * substitution is a real decision pulled from the live partition; the numbers
 * are the harness's own `finding` strings from GET /metrics/batch. A threshold
 * that changes in api/gates/ changes this page, and no screen can claim more
 * than the run supports because no screen is doing the claiming.
 */

function Section({
  n,
  title,
  lede,
  children,
}: {
  n: string
  title: string
  lede?: string
  children: React.ReactNode
}) {
  return (
    <section className="border-t border-rule px-4 py-8 sm:py-10">
      <p className="eyebrow">{n}</p>
      <h2 className="font-display mt-2 max-w-[28ch] text-lead font-semibold tracking-tight text-balance">
        {title}
      </h2>
      {lede ? <p className="mt-3 max-w-[68ch] text-body text-dim">{lede}</p> : null}
      <div className="mt-6">{children}</div>
    </section>
  )
}

function range(r: SeedRange | undefined, fmt: (n: number) => string): string {
  if (!r) return '—'
  return r.min === r.max ? fmt(r.min) : `${fmt(r.min)}–${fmt(r.max)}`
}

/** Razorpay's fixed ladder against a diagnosed attempt placed between its rungs. */
function RetryTimeline() {
  const rungs = [
    { x: 190, label: 'T+1' },
    { x: 330, label: 'T+2' },
    { x: 470, label: 'T+3' },
  ]
  return (
    <figure>
      <svg viewBox="0 0 720 230" className="w-full" role="img"
        aria-label="Razorpay retries three times on a fixed schedule then halts the subscription. Salvage adds a diagnosed attempt between those rungs without consuming the retry budget.">
        <line x1="40" y1="115" x2="690" y2="115" stroke="var(--color-rule)" strokeWidth="1" />

        {/* The failure, and the end of the line. */}
        <g fontFamily="var(--font-data)" fontSize="11" fill="var(--color-dim)">
          <rect x="42" y="103" width="24" height="24" fill="none" stroke="var(--color-stamp)" strokeWidth="1.5" />
          <text x="42" y="148" fill="var(--color-stamp)">charge fails</text>
          <text x="42" y="163">T = 0</text>

          <rect x="612" y="103" width="24" height="24" fill="var(--color-stamp)" opacity="0.14" stroke="var(--color-stamp)" strokeWidth="1.5" />
          <text x="596" y="148" fill="var(--color-stamp)">halted</text>
        </g>

        {/* Razorpay's ladder: same schedule, same time of day, whatever the cause. */}
        <g fontFamily="var(--font-data)" fontSize="11" fill="var(--color-dim)">
          {rungs.map((r) => (
            <g key={r.label}>
              <line x1={r.x} y1="60" x2={r.x} y2="115" stroke="var(--color-rule)" strokeWidth="1" strokeDasharray="2 3" />
              <rect x={r.x - 12} y="44" width="24" height="24" fill="none" stroke="var(--color-ink)" strokeWidth="1.5" />
              <text x={r.x - 12} y="34">{r.label}</text>
            </g>
          ))}
          <text x="40" y="20" fill="var(--color-ink)">RAZORPAY — three blind retries, fixed schedule</text>
        </g>

        {/* Salvage's insert: placed where the diagnosis says it belongs. */}
        <g fontFamily="var(--font-data)" fontSize="11">
          <line x1="260" y1="115" x2="260" y2="176" stroke="var(--color-ledger)" strokeWidth="1" strokeDasharray="2 3" />
          <rect x="248" y="176" width="24" height="24" fill="var(--color-ledger)" opacity="0.14" stroke="var(--color-ledger)" strokeWidth="1.5" />
          <text x="284" y="186" fill="var(--color-ledger)">SALVAGE — one diagnosed attempt,</text>
          <text x="284" y="200" fill="var(--color-ledger)">placed at the next payday window</text>
          <text x="40" y="222" fill="var(--color-dim)">
            a manual invoice charge does not consume the auto-retry budget
          </text>
        </g>
      </svg>
    </figure>
  )
}

const STAGES = [
  { name: 'Event', note: 'payment.failed, HMAC-verified on the raw body' },
  { name: 'Normalise', note: '114 taxonomy rules → one of nine failure classes' },
  { name: 'Diagnose', note: 'the LLM, and only here' },
  { name: 'Policy', note: '11 rules, priority-ordered, first match wins' },
  { name: 'Gates', note: 'all eleven, in order, every time' },
  { name: 'Execute', note: 'idempotency key first, then dispatch' },
]

function Pipeline() {
  return (
    <div>
      <ol className="flex flex-col gap-2 lg:flex-row lg:items-stretch">
        {STAGES.map((stage, i) => (
          <li key={stage.name} className="flex min-w-0 items-center gap-2 lg:flex-1 lg:flex-col lg:items-stretch">
            <div className="min-w-0 flex-1 border border-rule bg-white/50 px-3 py-2.5 lg:flex-none">
              <p className="data text-data text-ink">{stage.name}</p>
              <p className="mt-1 text-data-sm text-dim">{stage.note}</p>
            </div>
            {i < STAGES.length - 1 ? (
              <span className="shrink-0 rotate-90 text-rule lg:hidden">
                <ArrowRight size="sm" />
              </span>
            ) : null}
          </li>
        ))}
      </ol>

      {/* The two lines that make the rest of it safe to believe. */}
      <div className="mt-6 grid gap-3 sm:grid-cols-2">
        <div className="border-l-2 border-ink pl-3">
          <p className="data text-data text-ink">The LLM never chooses a money action.</p>
          <p className="mt-1 text-data-sm text-dim">
            It returns a diagnosis. Where the deterministic normaliser produced anything but
            UNKNOWN, the normaliser wins. Across the whole batch the model drove 6 of 623
            decisions.
          </p>
        </div>
        <div className="border-l-2 border-ink pl-3">
          <p className="data text-data text-ink">One file imports the Razorpay SDK.</p>
          <p className="mt-1 text-data-sm text-dim">
            api/executor/client.py, and the build fails if a second one ever does. The
            prompt context has no field for a name, a contact or an amount, so leaking PII
            into it is a type error rather than an oversight.
          </p>
        </div>
      </div>
    </div>
  )
}

/** The catalogue, from GET /rulebook. No verdict marks: nothing has run here,
 *  and eleven green ticks for gates that never evaluated would be a lie. */
function GateCatalogue({ rulebook }: { rulebook: Rulebook }) {
  const [open, setOpen] = useState<string | null>(null)
  return (
    <div className="border border-rule bg-paper">
      {rulebook.gates.map((gate) => {
        const { number, name } = splitGateName(gate.gate)
        const remedy = rulebook.remediation[gate.gate]
        const isOpen = open === gate.gate
        return (
          <div key={gate.gate} className="border-b border-rule/60 last:border-b-0">
            <button
              type="button"
              onClick={() => setOpen(isOpen ? null : gate.gate)}
              aria-expanded={isOpen}
              className="grid w-full grid-cols-[2rem_minmax(0,1fr)] items-baseline gap-x-3 px-3 py-2.5 text-left hover:bg-white/60 sm:grid-cols-[2rem_7rem_minmax(0,1fr)]"
            >
              <span className="data text-data-sm text-dim">{number}</span>
              <span className="data text-data text-ink">{name}</span>
              <span className="col-span-2 col-start-1 text-data-sm text-dim sm:col-span-1 sm:col-start-3">
                {isOpen ? gate.rule_text : gate.rule_text.slice(0, 96).trimEnd() + '…'}
                {gate.scope_note ? (
                  <span className="ml-2 whitespace-nowrap text-block">narrower in scope</span>
                ) : null}
              </span>
            </button>
            {isOpen ? (
              <div className="border-t border-rule/60 bg-white/50 px-3 py-3">
                <p className="eyebrow">Clause</p>
                <p className="data mt-1 text-data-sm text-ink">{gate.clause_ref}</p>
                {/* Where the gate is narrower than the clause, say so in the same
                    breath. The rule text above is what the regulation requires;
                    this is what Salvage actually checks, and they are not always
                    the same thing. */}
                {gate.scope_note ? (
                  <>
                    <p className="eyebrow mt-3">What this gate actually checks</p>
                    <p className="mt-1 max-w-[68ch] text-data-sm text-block">{gate.scope_note}</p>
                  </>
                ) : null}
                {remedy ? (
                  <p className="mt-3 flex flex-wrap items-center gap-2 text-data-sm text-dim">
                    <span className="text-dim">
                      <Split size="sm" />
                    </span>
                    A block here is not the end of it — Salvage substitutes
                    <span className="data text-ink">{remedy}</span>
                    and re-gates that from scratch.
                  </p>
                ) : null}
              </div>
            ) : null}
          </div>
        )
      })}
    </div>
  )
}

export function HowItWorks({ onBack, onOpenCase }: { onBack: () => void; onOpenCase: (id: string) => void }) {
  const [rulebook, setRulebook] = useState<Rulebook | null>(null)
  const [metrics, setMetrics] = useState<BatchMetrics | null>(null)
  const [example, setExample] = useState<{ id: string; detail: CaseDetail } | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let alive = true
    Promise.all([api.rulebook(), api.batchMetrics().catch(() => null)])
      .then(([book, batch]) => {
        if (!alive) return
        setRulebook(book)
        setMetrics(batch)
        // A real remediated decision rather than a scripted one. The cases whose
        // final action is a remediation target are exactly the substituted ones.
        const targets = new Set(Object.values(book.remediation))
        return api.cases().then(({ cases }) => {
          const hit = cases.find((c) => c.final_action && targets.has(c.final_action))
          if (!hit || !alive) return
          return api.case(hit.id).then((detail) => alive && setExample({ id: hit.id, detail }))
        })
      })
      .catch(() => alive && setFailed(true))
    return () => {
      alive = false
    }
  }, [])

  const ruleText = useMemo(
    () => Object.fromEntries((rulebook?.gates ?? []).map((g) => [g.gate, g.rule_text])),
    [rulebook],
  )
  const remediableGates = useMemo(
    () => (rulebook ? Object.keys(rulebook.remediation) : undefined),
    [rulebook],
  )

  const exampleHops = useMemo(() => {
    const candidates = (example?.detail.decisions ?? []).map((decision) => ({
      decision,
      hops: splitHops(decision, remediableGates),
    }))
    return candidates.find((c): c is { decision: Decision; hops: Hop[] } => (c.hops?.length ?? 0) > 1) ?? null
  }, [example, remediableGates])

  const head = metrics?.headline

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
          How Salvage decides
        </h1>
        <p className="mt-2 max-w-[68ch] text-body text-dim">
          A recurring charge failed. Something has to decide what to do about it, and every
          option is a money action against a real customer under RBI rules. This is what
          happens in between.
        </p>
      </div>

      {failed ? (
        <p className="border-b border-rule px-4 py-3 text-data text-block">
          The API is not answering, so the gates and the numbers below cannot be read from
          it. Start it with <span className="data">make dev</span>, then reload.
        </p>
      ) : null}

      <Section
        n="01"
        title="Razorpay retries three times. It does not ask why."
        lede="When a mandate charge fails, the subscription goes pending and retries on T+1, T+2 and T+3 — same schedule, same time of day, whether the card is dead or the customer is three days from payday. After the third, it halts. Salvage sits in that gap."
      >
        <RetryTimeline />
        <p className="mt-4 max-w-[68ch] text-data-sm text-dim">
          The lever is documented rather than clever: a manual charge on an issued invoice
          does not count against the subscription's remaining retries. So a diagnosed,
          precisely-timed attempt can be added without spending the platform's safety net.
        </p>
      </Section>

      <Section
        n="02"
        title="Diagnose with a model. Decide with code."
        lede="Six stages between the webhook and the money. The model appears in exactly one of them, and it does not get a vote on what happens next."
      >
        <Pipeline />
      </Section>

      <Section
        n="03"
        title="Eleven gates, in order, every time"
        lede={
          rulebook?.gate_order_note ??
          'Every proposed action is submitted to the whole chain before anything is dispatched.'
        }
      >
        {rulebook ? (
          <GateCatalogue rulebook={rulebook} />
        ) : (
          <p className="text-data text-dim">Reading the rulebook…</p>
        )}
        <p className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-data-sm text-dim">
          <span className="inline-flex items-center gap-2">
            <span className="text-dim">
              <Shield size="sm" />
            </span>
            G0_KILL is evaluated first and needs no token
          </span>
          <span className="inline-flex items-center gap-2">
            <span className="text-replan">
              <Replan size="sm" />
            </span>
            only G1, G2, G4 and G5 can defer; the rest allow or refuse
          </span>
          <span className="inline-flex items-center gap-2">
            <span className="text-dim">
              <Stamp size="sm" />
            </span>
            every refusal is stamped with the clause it came from
          </span>
        </p>
      </Section>

      <Section
        n="04"
        title="A refusal is not the end of the decision"
        lede="When a gate blocks something it can compensate for, Salvage substitutes the lawful action and submits that to all eleven gates from scratch. One decision, two full chains — this is a live one, not an illustration."
      >
        {exampleHops ? (
          <>
            <RemediationTrail
              hops={exampleHops.hops}
              ruleText={ruleText}
              ruleId={exampleHops.decision.policy.rule_id}
            />
            <button
              type="button"
              onClick={() => example && onOpenCase(example.id)}
              className="eyebrow mt-4 inline-flex items-center gap-1.5 hover:text-ink"
            >
              Open this case
              <ArrowRight size="sm" />
            </button>
          </>
        ) : (
          <p className="max-w-[68ch] text-data text-dim">
            No remediated decision in the live partition yet — every proposal so far cleared
            or was refused outright. Post a failed-payment webhook and one will appear here.
          </p>
        )}
      </Section>

      <Section
        n="05"
        title="What the batch actually showed"
        lede={
          metrics
            ? `${metrics.protocol.cohort_size} mandates, ${metrics.protocol.seeds.length} seeds, ${metrics.protocol.arms.length} arms. ${metrics.protocol.reporting}`
            : 'Run make batch to produce these numbers.'
        }
      >
        {head ? (
          <div className="grid gap-px border border-rule bg-rule sm:grid-cols-2">
            {/* The uncomfortable one goes first, deliberately. */}
            <div className="bg-paper p-4">
              <p className="eyebrow">Net recovery</p>
              <p className="data mt-2 text-data text-block">Not distinguishable</p>
              <p className="mt-2 max-w-[52ch] text-data-sm text-dim">{head.recovery_rate.finding}</p>
              <dl className="mt-3 space-y-1 text-data-sm">
                <div className="flex justify-between gap-4">
                  <dt className="text-dim">baseline</dt>
                  <dd className="data">{range(head.recovery_rate.baseline_net, (n) => percent(n))}</dd>
                </div>
                <div className="flex justify-between gap-4">
                  <dt className="text-dim">Salvage</dt>
                  <dd className="data">{range(head.recovery_rate.salvage_net, (n) => percent(n))}</dd>
                </div>
              </dl>
            </div>

            <div className="bg-paper p-4">
              <p className="eyebrow">Attempts per recovery</p>
              <p className="data mt-2 text-data text-allow">
                {range(head.attempt_efficiency.salvage_attempts_per_recovery, (n) => n.toFixed(2))} against{' '}
                {range(head.attempt_efficiency.baseline_attempts_per_recovery, (n) => n.toFixed(2))}
              </p>
              <p className="mt-2 max-w-[52ch] text-data-sm text-dim">
                {head.attempt_efficiency.finding}
              </p>
              <dl className="mt-3 space-y-1 text-data-sm">
                <div className="flex justify-between gap-4">
                  <dt className="text-dim">wasted on hard declines, baseline</dt>
                  <dd className="data text-block">
                    {range(head.attempt_efficiency.baseline_wasted_on_hard_declines, String)}
                  </dd>
                </div>
                <div className="flex justify-between gap-4">
                  <dt className="text-dim">wasted on hard declines, Salvage</dt>
                  <dd className="data text-allow">
                    {range(head.attempt_efficiency.salvage_wasted_on_hard_declines, String)}
                  </dd>
                </div>
              </dl>
            </div>

            <div className="bg-paper p-4">
              <p className="eyebrow">Compliance</p>
              <p className="data mt-2 text-data text-allow">
                {head.compliance.salvage.reduce((n, s) => n + s.contacts_outside_0800_1900_ist, 0)}{' '}
                contacts outside the window, against{' '}
                <span className="text-block">
                  {head.compliance.baseline.reduce((n, s) => n + s.contacts_outside_0800_1900_ist, 0)}
                </span>
              </p>
              <p className="mt-1 text-data-sm text-dim">
                totalled across all {head.compliance.salvage.length} runs
              </p>
              <p className="mt-2 max-w-[52ch] text-data-sm text-dim">{head.compliance.finding}</p>
            </div>

            <div className="bg-paper p-4">
              <p className="eyebrow">What the model contributed</p>
              <p className="data mt-2 text-data text-ink">
                {head.llm_contribution.llm_driven_decisions} of {head.llm_contribution.llm_calls}{' '}
                decisions
              </p>
              <p className="mt-2 max-w-[52ch] text-data-sm text-dim">
                {head.llm_contribution.finding}
              </p>
              <p className="mt-2 max-w-[52ch] text-data-sm text-dim">
                {head.llm_contribution.latency_cost_note}
              </p>
            </div>
          </div>
        ) : (
          <p className="max-w-[68ch] text-data text-dim">
            No batch results on disk. Run <span className="data">make batch</span> to produce
            eval/results.json, then reload.
          </p>
        )}
      </Section>
    </div>
  )
}
