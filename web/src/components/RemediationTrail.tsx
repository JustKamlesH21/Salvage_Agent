import { Fragment } from 'react'
import { GateChain } from './GateChain'
import { ArrowDown, ArrowRight } from './icons'
import type { Hop } from '../lib/hops'
import { GATE_COUNT } from '../lib/hops'

/**
 * The signature shot: what the agent wanted to do, what stopped it, and what it
 * did instead — with both proposals submitted to the same eleven gates.
 *
 * These are not two decisions. They are one decision whose verdicts arrive as a
 * single flat array of eleven-times-however-many-hops, because
 * resolve_with_remediation() re-gates each substitute from scratch and
 * accumulates as it goes. Laid flat, that array reads as noise. Split back into
 * the passes that produced it, it reads as reasoning.
 *
 * Built for N hops rather than two: MAX_REMEDIATION_HOPS is 3, so a decision can
 * carry up to 44 verdicts, and the demo case's shape is not the only shape.
 */

function Link({ gate }: { gate: string }) {
  return (
    <div className="flex w-full shrink-0 flex-row items-center gap-2 py-2 xl:w-28 xl:flex-col xl:justify-center xl:py-0 xl:text-center">
      <span className="shrink-0 text-dim xl:hidden">
        <ArrowDown size="md" title={`Remediated after ${gate} blocked`} />
      </span>
      <span className="hidden shrink-0 text-dim xl:block">
        <ArrowRight size="md" title={`Remediated after ${gate} blocked`} />
      </span>
      <span className="data text-data-sm text-block">{gate}</span>
      <span className="eyebrow">forced a substitute</span>
    </div>
  )
}

export function RemediationTrail({
  hops,
  ruleText,
  ruleId,
}: {
  hops: Hop[]
  ruleText?: Record<string, string>
  ruleId?: string
}) {
  const total = hops.length * GATE_COUNT

  return (
    <div>
      <p className="eyebrow mb-2">
        {total} gate verdicts on one decision · {hops.length} passes of {GATE_COUNT}
      </p>

      {/* Wide content scrolls in its own container; the page body never does. */}
      <div className="-mx-1 overflow-x-auto px-1 pb-1">
        <div className="flex flex-col xl:flex-row xl:items-stretch xl:gap-2">
          {hops.map((hop, i) => (
            <Fragment key={hop.index}>
              <div className="min-w-0 xl:flex-1">
                <GateChain
                  hop={hop}
                  ruleText={ruleText}
                  ruleId={ruleId}
                  // Each pass draws in its own beat rather than one stagger walking
                  // across all twenty-two rows and landing the last stamp a second late.
                  baseDelay={i * 320}
                />
              </div>
              {hop.remediatedBy ? <Link gate={hop.remediatedBy.gate} /> : null}
            </Fragment>
          ))}
        </div>
      </div>
    </div>
  )
}
