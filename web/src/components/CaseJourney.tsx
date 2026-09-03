import { useEffect, useState } from 'react'
import type { CaseDetail } from '../lib/api'
import { journeyFor, untilLabel, type Stage } from '../lib/journey'
import { istStamp, istTime } from '../lib/format'
import { Check, Replan } from './icons'

/**
 * Where this case is, and what it is waiting for.
 *
 * A parcel tracker answers "where is it and when does it arrive" before it
 * shows you anything else, because that is the question. This is the same
 * question asked of a recovery: what stage is it at, what happens next, when.
 *
 * The difference — and it is the whole reason this belongs in a compliance
 * product — is that the ETA here moves for a stated reason. A parcel is late
 * because of traffic. This is late because the RBI Fair Practices Code says
 * nobody gets contacted after seven, and the gate that says so is named on the
 * card. The wait is not a delay; it is the product working.
 */

const DOT: Record<Stage['state'], string> = {
  done: 'border-ink bg-ink',
  current: 'border-replan bg-replan',
  pending: 'border-rule bg-paper',
  skipped: 'border-rule bg-paper',
}

function StageDot({ stage }: { stage: Stage }) {
  if (stage.state === 'done') {
    return (
      <span className="flex size-5 shrink-0 items-center justify-center border border-ink bg-ink text-paper">
        <Check size="sm" />
      </span>
    )
  }
  if (stage.state === 'current') {
    return (
      <span className="flex size-5 shrink-0 items-center justify-center border border-replan bg-paper text-replan">
        <Replan size="sm" />
      </span>
    )
  }
  return <span className={`mt-0.5 size-4 shrink-0 border ${DOT[stage.state]}`} />
}

export function CaseJourney({ detail }: { detail: CaseDetail }) {
  const journey = journeyFor(detail)
  const [now, setNow] = useState(() => Date.now())

  // The countdown is the one thing on this screen that moves on its own while
  // nothing is happening, which is exactly when someone is looking at it.
  useEffect(() => {
    if (!journey.waiting) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [journey.waiting])

  return (
    <section className="border-b border-rule px-4 py-5">
      <p className="eyebrow">Where this case is</p>

      <ol className="mt-4 flex flex-col gap-y-3 lg:flex-row lg:items-start lg:gap-x-2">
        {journey.stages.map((stage, i) => (
          <li key={stage.name} className="flex min-w-0 gap-3 lg:flex-1 lg:flex-col lg:gap-2">
            <div className="flex shrink-0 flex-col items-center lg:w-full lg:flex-row lg:items-center">
              <StageDot stage={stage} />
              {i < journey.stages.length - 1 ? (
                <span
                  className={`w-px flex-1 lg:h-px lg:w-full ${
                    stage.state === 'done' ? 'bg-ink' : 'bg-rule'
                  }`}
                  aria-hidden
                />
              ) : null}
            </div>
            <div className="min-w-0 pb-1">
              <p
                className={`data text-data ${
                  stage.state === 'pending' || stage.state === 'skipped'
                    ? 'text-dim'
                    : stage.state === 'current'
                      ? 'text-replan'
                      : 'text-ink'
                }`}
              >
                {stage.label}
              </p>
              {stage.state === 'skipped' ? (
                <p className="text-data-sm text-dim">not needed</p>
              ) : stage.at ? (
                <p className="data text-data-sm text-dim">
                  {stage.name === 'waiting' ? istStamp(stage.at) : istTime(stage.at)}
                </p>
              ) : null}
              {stage.note ? <p className="text-data-sm text-dim">{stage.note}</p> : null}
            </div>
          </li>
        ))}
      </ol>

      {journey.waiting ? (
        <div className="mt-5 border border-replan/40 bg-white/60 p-4">
          <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
            <span className="data text-data text-ink">{journey.waiting.action}</span>
            <span className="data text-data text-replan">
              {untilLabel(journey.waiting.dueAt, now)}
            </span>
            <span className="data text-data-sm text-dim">
              due {istStamp(journey.waiting.dueAt)}
            </span>
          </div>

          {journey.waiting.movedBy ? (
            <p className="mt-2 max-w-[68ch] text-data-sm text-dim">
              Proposed for {istTime(journey.waiting.movedFrom)} and moved by{' '}
              <span className="data text-block">{journey.waiting.movedBy}</span>. It was not
              delayed — it was refused at that hour and given the next lawful one.
            </p>
          ) : null}

          <p className="mt-2 text-data-sm text-dim">
            All eleven gates run again when it fires, against the world as it is then. A
            verdict reached now does not travel with the action.
          </p>
        </div>
      ) : journey.settled ? (
        <p className="mt-4 text-data-sm text-dim">
          Nothing further is owed on this case.
        </p>
      ) : null}
    </section>
  )
}
