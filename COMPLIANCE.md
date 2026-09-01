# Compliance

Gate ↔ clause mapping. Every gate module exports `CLAUSE_REF` and `RULE_TEXT`,
and `scripts/check_clauses.py` fails the build if either is missing.

> **Day 0 stub.** No gate is implemented. The table below is the specification
> from `PRD.md` §9; the `RULE_TEXT` column is filled with the *verbatim* clause
> as each gate is written on day 5. A citation that does not match what the code
> enforces is worse than a missing one — it is a false statement inside an audit
> trail — so nothing is filled in here until the clause has been read.

| Gate | Rule | Authority | Status |
|---|---|---|---|
| `G0_KILL` | Kill switch engaged → block everything. Evaluated first, always. | Ops | not built |
| `G1_WINDOW` | Customer contact only 08:00–19:00 IST, every channel including automated messaging | RBI Fair Practices Code, recovery agent conduct | not built |
| `G2_PREDEBIT` | No auto-debit without a pre-transaction notice ≥24h prior carrying merchant name, amount, debit date/time, mandate reference and opt-out | RBI E-mandate Framework 2026 (notified 21 Apr 2026) | not built |
| `G3_AFA` | No-AFA auto-debit ≤ ₹15,000/txn; ≤ ₹1,00,000 for insurance premiums, mutual fund subscriptions and credit-card bills. Above → AFA link, never a silent retry | same framework | not built |
| `G4_BUDGET` | Max 4 Salvage attempts per case per 14 days, on top of Razorpay's own T+1/T+2/T+3 | Card network retry limits; soft→hard decline conversion risk | not built |
| `G5_COOLDOWN` | ≥48h between contacts, ≤3 contacts per 7 days | Anti-harassment | not built |
| `G6_OPTOUT` | Opt-out permanent and immediate | E-mandate Framework 2026 | not built |
| `G7_THIRDPARTY` | Never contact employer, relatives, neighbours or any non-account contact | RBI FPC | not built |
| `G8_POSTDEBIT` | Post-transaction confirmation with grievance-redressal details after every successful collection | E-mandate Framework 2026 | not built |
| `G9_HALTED` | No auto-charge on a halted subscription | Razorpay subscription state machine | not built |
| `G10_IDEMPOTENT` | Refuse any action whose idempotency key already exists in `audit` | Correctness | not built |

## Standing caveat

These gates encode our reading of the E-mandate Framework 2026 and the Fair
Practices Code. They are engineering controls, not legal advice, and a
production deployment needs counsel review. Full source URLs go in the README
on day 10.
