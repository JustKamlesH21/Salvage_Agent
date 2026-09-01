---
name: policy-rule-authoring
description: The YAML shape for a policy rule per SALVAGE_SPEC section 5.4 - priority bands, how to source a cites entry, and the rule that because explains the payments reasoning rather than restating the condition. Use when adding or changing anything in api/policy/rules/, or when a failure class needs a different default play.
---

# Authoring a policy rule

The policy engine is 100% deterministic. It is what decides the action; the LLM
only diagnoses. Rules are evaluated in priority order and **first match wins**,
so a rule's priority is part of its meaning.

## Shape

```yaml
- id: POL-004
  priority: 40
  when:
    failure_class: TEMPORARY_LIQUIDITY
    attempt_count: {lte: 3}
  then:
    action: RETRY_SCHEDULED
    timing: NEXT_PAYDAY_WINDOW
    channel_note: "manual invoice charge — does not consume subscription retry budget"
  because: "Insufficient-funds declines clear on salary credit, not on a fixed
            day-3 timer. Nighttime and pre-payday attempts underperform."
  cites: ["razorpay/docs/payments/subscriptions/payment-retries",
          "industry:payday-aligned-retry"]
```

- `id` — `POL-NNN`, allocated in order, never reused. It appears in
  `decisions.policy.rule_id` and on screen, so a recycled id makes an old
  decision unreadable.
- `when` — matched against the case and the diagnosis. Conditions are
  conjunctive. A list means "any of".
- `then.action` — one of A1–A10 from SPEC §4.2 and nothing else. The executor
  validates the id against the enum before dispatch; an action not in the
  catalog cannot execute, so inventing one here produces a runtime failure, not
  a new capability.
- `then.timing` — a *symbolic* window (`NEXT_PAYDAY_WINDOW`, `HOURS_24`,
  `NOW`), never a literal timestamp. Resolution to a real moment happens later
  and is the gates' business, not the rule's.

`scripts/check_clauses.py` fails the build if `id`, `because` or `cites` is
missing or empty.

## Priority bands

Bands, not arbitrary integers. Leave gaps inside a band so a rule can be
inserted without renumbering:

| Band | Kind of rule | Examples |
|---|---|---|
| 1–19 | **Stop.** Hard declines and dead mandates. Retrying burns issuer goodwill. | `CREDENTIAL_DEAD`, `MANDATE_INVALID` |
| 20–39 | **Escalate.** Needs a human or an authenticated customer action. | above AFA threshold, `UNKNOWN` |
| 40–59 | **Re-auth.** Customer-initiated recovery via a link. | `CREDENTIAL_STALE`, `AUTH_ABANDONED` |
| 60–79 | **Retry, scheduled.** Recoverable, but timing is the lever. | `TEMPORARY_LIQUIDITY`, `ISSUER_HOLD` |
| 80–89 | **Retry, now.** Transient, short authorization window. | `CHANNEL_DOWNTIME` |
| 90–98 | **Nudge.** Contact only, no money movement. | dunning after budget exhausted |
| 99 | **No action.** The terminal catch-all. Always exactly one rule. | |

Stop rules outrank everything because the expensive mistake in this domain is
retrying something that can never succeed — it wastes budget *and* risks
converting a soft decline into a hard one.

The band-99 catch-all is not optional. A case that matches no rule must land on
`CLOSE_NO_ACTION` with a reason, never fall through silently.

## `because` — the reasoning, not the condition

This string renders in the UI. A judge clicks a decision and reads it. It has to
answer *why is this the right play in payments*, not *what did the condition
say*.

> **No:** "Because the failure class is TEMPORARY_LIQUIDITY and the attempt
> count is at most 3." — restates `when` in prose. Tells the reader nothing they
> could not see.
>
> **Yes:** "Insufficient-funds declines clear on salary credit, not on a fixed
> day-3 timer. Nighttime and pre-payday attempts underperform." — states the
> mechanism, and is checkable against the cited source.

Test for it: delete the `when` block and read the `because` alone. If it still
teaches you something about payments, it is doing its job.

Write it in the interface's voice — active, specific, no hedging. Two sentences
is usually right. If it needs four, the rule is probably two rules.

## `cites` — where the authority comes from

A list, never empty. Two forms, and the prefix is how the UI groups them:

- `razorpay/docs/<path>` — a specific Razorpay documentation page. Cite the page
  that states the behaviour, not the section index. If you have not opened it in
  this session, open it before citing it.
- `industry:<slug>` — a practice claim from the research in SPEC §1 and §13.
  Every slug used must resolve to a source listed in the README's source table.
  A slug that appears nowhere else is an unsourced assertion wearing a citation's
  clothes.

Regulatory clauses are **not** cited here. Those belong to gates, in
`CLAUSE_REF`. Policy rules decide what is worth doing; gates decide what is
permitted. Keeping the two citation vocabularies separate is what lets the
Decision Tape show "this is the play, and here is what the regulator allowed of
it" as two distinct statements.

## Testing a rule

First-match-wins is the property most likely to break silently as rules
accumulate. For every rule added:

1. A case that matches it and gets its action.
2. A case that matches it *and* a lower-priority rule, asserting this one wins.
3. If it narrows an existing rule, a case that used to match the old rule and
   now matches this one.
4. The catch-all still reachable — a case matching nothing lands on
   `CLOSE_NO_ACTION`.

Then run `compliance-auditor` on the diff: `api/policy/` is on its required list.
