# Compliance

Gate ↔ clause mapping. Every gate module exports `CLAUSE_REF` and `RULE_TEXT`,
and `scripts/check_clauses.py` fails the build if either is missing.

> **Day 4: built and cited.** All eleven gates are implemented, tested (100%
> branch coverage), and the `RULE_TEXT` column below is each gate's actual
> constant, not a paraphrase. Sourcing method: fetched live this session from
> secondary legal-analysis sources corroborating the RBI circulars (not the
> raw RBI PDF text itself, which no tool available this session could fetch)
> — flagged per row below where that distinction matters. `Scope` notes where
> a gate's actual code checks less than the full clause it cites; see each
> gate module's own docstring for the full reasoning.

| Gate | `RULE_TEXT` (verbatim from the module) | `CLAUSE_REF` | Scope |
|---|---|---|---|
| `G0_KILL` | "Engaging the kill switch freezes all pending and scheduled actions within one scheduler tick. No action of any kind may execute while it is engaged." | Salvage Ops — Global Kill Switch | Evaluated first, always (invariant 4). Full scope — every action, no exceptions. |
| `G1_WINDOW` | "Regulated entities and their recovery agents shall not call the borrower before 8:00 a.m. and after 7:00 p.m. Applies across all contact channels, including automated messaging." | RBI DOR.ORG.REC.65/21.04.158/2022-23 (12 Aug 2022) | **Gates customer-contact actions only** (notices, links, nudges) — the cited circular governs contact/calling conduct, not silent auto-debit timing. A silent `RETRY_NOW`/`RETRY_SCHEDULED` is exempt. See `api/gates/g1_window.py`'s docstring for why this deviates from SPEC §6.3's illustrative mock. |
| `G2_PREDEBIT` | "No auto-debit may be attempted without a pre-transaction notification sent to the customer at least 24 hours prior. (The framework also requires the notice to carry the merchant name, amount, debit date/time, mandate reference and an opt-out facility — this gate checks lead time only; notice content is not yet verified.)" | RBI Digital Payments E-mandate Framework, 2026 (RBI/DPSS/2026-27/396, 21 Apr 2026) | **Lead-time only.** Checks that a `SEND_PRE_DEBIT_NOTICE` audit row exists ≥24h before the charge; does not verify the notice's content matched the amount/date now being charged (compliance-auditor F7 — narrowed after an earlier version of this text implied full content verification the code doesn't perform). |
| `G3_AFA` | "Auto-debit without Additional Factor of Authentication is permitted only up to ₹15,000 per transaction; up to ₹1,00,000 for insurance premiums, mutual fund subscriptions and credit-card bill payments. Above the applicable threshold, the transaction must route through an AFA-authenticated flow — never a silent auto-debit." | RBI Digital Payments E-mandate Framework, 2026 (RBI/DPSS/2026-27/396, 21 Apr 2026) | Full scope for charge actions. Threshold figures corroborated by multiple independent sources (16 Jun 2022 circular raising ₹5,000→₹15,000; a further categorised ₹1L ceiling for insurance/MF/credit-card-bill). Never REPLANs — waiting doesn't change the amount. |
| `G4_BUDGET` | "No more than 4 Salvage-initiated recovery attempts per case within a rolling 14-day window, independent of and additional to Razorpay's own T+1/T+2/T+3 auto-retry schedule." | Card network retry limits; soft-to-hard decline conversion risk (SPEC §1, §13) | Not an RBI clause — industry/card-network practice, cited as such rather than inventing a regulatory section that doesn't exist. Counts both `pre` and `post` audit rows (compliance-auditor F5) so an in-flight dispatch isn't silently uncounted. |
| `G5_COOLDOWN` | "At least 48 hours must elapse between customer contacts, and no more than 3 contacts may be made within any rolling 7-day window." | Anti-harassment / marginal-value-of-nth-contact research (SPEC §1, §5.5) | Contact actions only. Never BLOCKs — always REPLANs to whichever constraint (48h gap or 7-day cap) clears later. |
| `G6_OPTOUT` | "Where a customer has opted out, no further automated contact or auto-debit attempt may occur. The opt-out is permanent and takes immediate effect." | RBI Digital Payments E-mandate Framework, 2026 (RBI/DPSS/2026-27/396, 21 Apr 2026) | Full scope in principle; in practice fails open when a case has no resolvable `customer_id` (LIMITATIONS.md's "Money path (Day 4)" section — opt-out itself has no trigger wired yet, so this is a documented latent gap, not a live one). `CLOSE_NO_ACTION`/`SUPPRESS` stay exempt so an opted-out case can still be closed. |
| `G7_THIRDPARTY` | "Recovery communication must be directed only to the account holder. Employers, relatives, neighbours, or any other third party must never be contacted regarding the account." | RBI Fair Practices Code — recovery agent conduct | No code path in this build ever sets `contact_target` to anything but the account holder — this gate is a proven-testable safety net (same shape as `G0_KILL`/`G9_HALTED`), not a live filter on real data yet. |
| `G8_POSTDEBIT` | "Every successful auto-debit collection must be followed by a post-transaction confirmation to the customer, including grievance-redressal details." | RBI Digital Payments E-mandate Framework, 2026 (RBI/DPSS/2026-27/396, 21 Apr 2026) | **Reinterpreted, not literally implemented** — SPEC §4.2's action catalog has no "send post-debit confirmation" action to check directly. Modelled instead as: block a *new* charge if the customer has no confirmation channel on record at all. See `api/gates/g8_postdebit.py`'s docstring. |
| `G9_HALTED` | "A subscription in the halted state may not be auto-charged. Recovery from halted requires a customer-initiated re-authentication, not a further silent debit attempt." | Razorpay subscription state machine | Correct as written, but structurally unreachable in production today: this build only processes `payment.failed`, so nothing ever marks a case's subscription `halted` (LIMITATIONS.md). The gate itself is fully tested against synthetic contexts. |
| `G10_IDEMPOTENT` | "An action whose idempotency key already has a corresponding audit record must never be dispatched again." | Correctness — executor idempotency (CLAUDE.md invariant 9) | Deliberately redundant with the executor's own insert-first `DuplicateKeyError` guard — this is the gate-level, Decision-Tape-visible signal; the unique index is the structural guarantee underneath it. |

## Regulatory sourcing, stated honestly

`RULE_TEXT` above is not a transcription of the raw RBI circular PDFs — no
tool available this session could fetch those directly. It is corroborated
by multiple independent secondary legal-analysis sources (law-firm client
alerts, financial press) that agree on the substance and the circular
numbers/dates cited. This is a materially stronger basis than a summary
written from memory, but it is not the same claim as "verbatim from the
primary source," and this file says so rather than implying otherwise.

## Standing caveat

These gates encode our reading of the E-mandate Framework 2026 and the Fair
Practices Code. They are engineering controls, not legal advice, and a
production deployment needs counsel review. Full source URLs go in the README
on the final documentation pass.
