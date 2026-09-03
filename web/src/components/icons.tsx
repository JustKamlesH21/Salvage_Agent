/**
 * The icon set. Hand-drawn rather than pulled from a library, for the same
 * reason there is no component library: the visual identity is the point, and
 * Lucide's rounded 24px grid belongs to a different, softer product than a
 * mandate ledger does.
 *
 * House rules, so the set stays a set: a 24x24 grid, 1.5px stroke, square caps
 * and mitred joins, no curves where a corner will do. Three sizes only.
 *
 * Semantics are chosen by use, not by glyph. An icon sitting beside its own
 * label is decorative and is hidden from the accessibility tree; an icon
 * carrying meaning on its own takes a `title` and is announced.
 */

type IconSize = 'sm' | 'md' | 'lg'

const PX: Record<IconSize, number> = { sm: 16, md: 20, lg: 24 }

export type IconProps = {
  size?: IconSize
  className?: string
  /** Supply only when the icon carries meaning no adjacent text already carries. */
  title?: string
}

function Svg({
  size = 'md',
  className,
  title,
  children,
}: IconProps & { children: React.ReactNode }) {
  const px = PX[size]
  return (
    <svg
      width={px}
      height={px}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="square"
      strokeLinejoin="miter"
      className={className}
      role={title ? 'img' : undefined}
      aria-hidden={title ? undefined : true}
    >
      {title ? <title>{title}</title> : null}
      {children}
    </svg>
  )
}

/** ALLOW. */
export function Check(props: IconProps) {
  return (
    <Svg {...props}>
      <polyline points="4,12.5 9.5,18 20,6.5" />
    </Svg>
  )
}

/** BLOCK. The prohibition sign is worth the one curve in the set — nothing else
 *  reads as "refused" this fast, and legibility beats stylistic purity here. */
export function Blocked(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="12" cy="12" r="8.5" />
      <line x1="5.9" y1="5.9" x2="18.1" y2="18.1" />
    </Svg>
  )
}

/** REPLAN — a deferral, not a refusal. A clock, because the gate is saying
 *  "later", and the tape used to render this the same red as a block. */
export function Replan(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="12" cy="12" r="8.5" />
      <polyline points="12,6.5 12,12 16,14.5" />
    </Svg>
  )
}

export function ArrowLeft(props: IconProps) {
  return (
    <Svg {...props}>
      <line x1="20" y1="12" x2="5" y2="12" />
      <polyline points="10.5,6.5 5,12 10.5,17.5" />
    </Svg>
  )
}

export function ArrowRight(props: IconProps) {
  return (
    <Svg {...props}>
      <line x1="4" y1="12" x2="19" y2="12" />
      <polyline points="13.5,6.5 19,12 13.5,17.5" />
    </Svg>
  )
}

export function ArrowDown(props: IconProps) {
  return (
    <Svg {...props}>
      <line x1="12" y1="4" x2="12" y2="19" />
      <polyline points="6.5,13.5 12,19 17.5,13.5" />
    </Svg>
  )
}

export function ChevronDown(props: IconProps) {
  return (
    <Svg {...props}>
      <polyline points="5.5,9 12,15.5 18.5,9" />
    </Svg>
  )
}

/** The remediation fork: one proposal in, a substitute out. */
export function Split(props: IconProps) {
  return (
    <Svg {...props}>
      <line x1="3" y1="12" x2="9" y2="12" />
      <polyline points="9,12 15,6 21,6" />
      <polyline points="9,12 15,18 21,18" />
    </Svg>
  )
}

export function Shield(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M12 3.5 L20 6.5 V12 C20 16.5 16.6 19 12 20.5 C7.4 19 4 16.5 4 12 V6.5 Z" />
    </Svg>
  )
}

export function Stamp(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="3.5" y="16" width="17" height="4.5" />
      <path d="M8.5 16 V9 h7 v7" />
    </Svg>
  )
}

export function KillSwitch(props: IconProps) {
  return (
    <Svg {...props}>
      <line x1="12" y1="3.5" x2="12" y2="11" />
      <path d="M7.6 6.9 A6.6 6.6 0 1 0 16.4 6.9" />
    </Svg>
  )
}
