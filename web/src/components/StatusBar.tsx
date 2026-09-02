import type { Posture } from '../lib/api'

/**
 * The persistent chrome — SPEC §6.2. A judge should never be unsure which
 * mode they are watching, so the bar states the posture in words and changes
 * colour when it changes.
 *
 * The kill switch is a real control and engaging it really does freeze
 * dispatch (G0_KILL is evaluated first in every chain). Arming is a real
 * control too, and it is labelled with what it actually does: this build's
 * live dispatch path is an unimplemented stub, so arming changes the posture
 * and nothing else. Saying so on the bar is better than a button that implies
 * a power the system does not have.
 */
export function StatusBar({
  posture,
  onKill,
  onHome,
}: {
  posture: Posture | null
  onKill: (engaged: boolean) => void
  onHome: () => void
}) {
  const killed = posture?.kill_switch ?? false
  const armed = posture?.armed ?? false
  const dryRun = posture?.dry_run ?? true

  const tone = killed
    ? 'border-stamp bg-stamp text-paper'
    : armed
      ? 'border-amber bg-amber text-paper'
      : 'border-rule bg-paper text-ink'

  const mode = killed ? 'FROZEN' : armed ? 'ARMED' : dryRun ? 'DRY RUN' : 'LIVE'

  return (
    <header
      className={`sticky top-0 z-20 flex flex-wrap items-center gap-x-5 gap-y-2 border-b px-4 py-2.5 ${tone}`}
    >
      <button
        type="button"
        onClick={onHome}
        className="font-display text-[15px] font-semibold tracking-tight"
      >
        SALVAGE
      </button>

      <span className="flex items-center gap-2">
        <span
          className={`size-2 rounded-full ${
            killed || armed ? 'bg-paper' : 'bg-ledger'
          }`}
          aria-hidden
        />
        <span className="data text-[11px] tracking-wider">{mode}</span>
      </span>

      <span
        className={`data hidden text-[10px] sm:inline ${
          killed || armed ? 'text-paper/75' : 'text-dim'
        }`}
      >
        gates evaluate in {posture?.compliance_tz ?? 'Asia/Kolkata'}
      </span>

      <div className="ml-auto flex items-center gap-2">
        {posture && !posture.live_dispatch_available ? (
          <span
            className={`data hidden text-[10px] md:inline ${
              killed || armed ? 'text-paper/75' : 'text-dim'
            }`}
            title={posture.live_dispatch_note}
          >
            live dispatch not built
          </span>
        ) : null}

        <button
          type="button"
          onClick={() => onKill(!killed)}
          className={`data border px-3 py-1 text-[10px] tracking-wider uppercase ${
            killed
              ? 'border-paper text-paper hover:bg-paper hover:text-stamp'
              : 'border-stamp text-stamp hover:bg-stamp hover:text-paper'
          }`}
        >
          {killed ? 'Release kill switch' : 'Engage kill switch'}
        </button>
      </div>
    </header>
  )
}
