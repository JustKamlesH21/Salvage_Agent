/**
 * Short display forms for the stamps.
 *
 * The real CLAUSE_REF is the source of truth and is what the drawer shows. It
 * is also up to 68 characters — G2, G3, G6 and G8 all cite the same
 * "RBI Digital Payments E-mandate Framework, 2026 (RBI/DPSS/2026-27/396,
 * 21 Apr 2026)" — and a rubber stamp that has to hold that at 375px is not a
 * rubber stamp any more. So the chit gets an abbreviation and the drawer gets
 * the citation.
 *
 * Presentation only. Nothing decides anything from these, and a gate missing
 * from the map falls back to its own CLAUSE_REF rather than to blank.
 */

const SHORT: Record<string, string> = {
  G0_KILL: 'OPS · KILL SWITCH',
  G1_WINDOW: 'RBI FPC · 08:00–19:00',
  G2_PREDEBIT: 'E-MANDATE · PRE-DEBIT',
  G3_AFA: 'E-MANDATE · AFA',
  G4_BUDGET: 'NETWORK · RETRY CAP',
  G5_COOLDOWN: 'CONTACT · COOLDOWN',
  G6_OPTOUT: 'E-MANDATE · OPT-OUT',
  G7_THIRDPARTY: 'RBI FPC · THIRD PARTY',
  G8_POSTDEBIT: 'E-MANDATE · POST-DEBIT',
  G9_HALTED: 'RAZORPAY · HALTED',
  G10_IDEMPOTENT: 'IDEMPOTENCY',
}

export function shortClause(gate: string, clauseRef: string): string {
  return SHORT[gate] ?? clauseRef
}

/** `G3_AFA` → `G3` / `AFA`, so the chain reads as a numbered rail. */
export function splitGateName(gate: string): { number: string; name: string } {
  const at = gate.indexOf('_')
  if (at === -1) return { number: '', name: gate }
  return { number: gate.slice(0, at), name: gate.slice(at + 1) }
}
