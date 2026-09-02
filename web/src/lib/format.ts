/**
 * Money and time, formatted the way this product has to format them.
 *
 * Amounts are Indian-grouped (₹9,17,650 — not ₹917,650) because that is what
 * the audience reads. Timestamps are rendered in IST with the zone shown,
 * always: half the compliance logic in this system is about clocks, and a
 * timestamp that does not say which one it is in is a bug waiting to be
 * believed (SPEC §6.5).
 */

const RUPEES = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 })

/** Paise → `₹9,17,650`. Rounded to whole rupees; this is a ledger, not an invoice. */
export function rupees(paise: number | null | undefined): string {
  if (paise === null || paise === undefined) return '—'
  return `₹${RUPEES.format(Math.round(paise / 100))}`
}

/** Paise → `9,17,650`, for places where the ₹ sits in a column header instead. */
export function rupeesBare(paise: number | null | undefined): string {
  if (paise === null || paise === undefined) return '—'
  return RUPEES.format(Math.round(paise / 100))
}

export function percent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return '—'
  return `${(value * 100).toFixed(digits)}%`
}

const IST_DATE = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  day: '2-digit',
  month: 'short',
})

const IST_TIME = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

/** `23:14 IST` — the timecode down the left of the tape. */
export function istTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return '—'
  return `${IST_TIME.format(at)} IST`
}

/** `01 Sep · 23:14 IST` — where the day matters as well as the hour. */
export function istStamp(iso: string | null | undefined): string {
  if (!iso) return '—'
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return '—'
  return `${IST_DATE.format(at)} · ${IST_TIME.format(at)} IST`
}

/** `4m ago`, for the live feed only, where recency is the point. */
export function ago(iso: string | null | undefined): string {
  if (!iso) return ''
  const at = new Date(iso).getTime()
  if (Number.isNaN(at)) return ''
  const seconds = Math.max(0, Math.round((Date.now() - at) / 1000))
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`
  return `${Math.round(seconds / 86400)}d ago`
}

/** `TEMPORARY_LIQUIDITY` → `Temporary liquidity`, for prose contexts only.
 *  Class names stay verbatim in data contexts — they are identifiers there. */
export function humanClass(value: string | null | undefined): string {
  if (!value) return 'Unclassified'
  const lower = value.toLowerCase().replace(/_/g, ' ')
  return lower.charAt(0).toUpperCase() + lower.slice(1)
}
