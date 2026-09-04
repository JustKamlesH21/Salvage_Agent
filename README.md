<div align="center">

# Salvage

**A compliance-gated payment recovery agent.**

*Razorpay AI Buildathon 2026 · Track 03 — AI Revenue Recovery*

[![verify](https://img.shields.io/badge/make%20verify-8%2F8%20green-2e7d32?style=flat-square)](#8--verification--the-gate)
[![tests](https://img.shields.io/badge/tests-536-1565c0?style=flat-square)](#8--verification--the-gate)
[![coverage](https://img.shields.io/badge/branch%20coverage-100%25%20on%20gates%20%2B%20normalise-1565c0?style=flat-square)](#8--verification--the-gate)
[![gates](https://img.shields.io/badge/compliance%20gates-11-6a1b9a?style=flat-square)](#5--the-eleven-gates)
[![mode](https://img.shields.io/badge/DRY__RUN-default%20true-c62828?style=flat-square)](#three-barriers-stand-between-this-and-real-money)
[![python](https://img.shields.io/badge/python-3.12-3776ab?style=flat-square)](#10--setup)

---

### The LLM diagnoses. It never decides to move money.

</div>

---

## Contents

| | |
|---|---|
| [1 · The problem](#1--the-problem) | Why a failed recurring charge is not a retry problem |
| [2 · What Salvage is](#2--what-salvage-is) | The agent, its loop, and its bounded authority |
| [3 · Architecture](#3--architecture) | The diagram and the four boundaries that hold it up |
| [4 · How it works](#4--how-it-works-one-decision-end-to-end) | One decision, end to end |
| [5 · The eleven gates](#5--the-eleven-gates) | Gate ↔ RBI clause, and what each one actually checks |
| [6 · The interface](#6--the-interface) | Four screens, and the Decision Tape |
| [7 · Results](#7--results-measured-and-reported-against-ourselves) | The numbers, including the one we did not want |
| [8 · Verification](#8--verification--the-gate) | `make verify`, and why it can't be talked around |
| [9 · Repository map](#9--repository-map) | Where everything lives |
| [10 · Setup](#10--setup) | Prerequisites, install, credentials |
| [11 · Running it](#11--running-it) | Dev, demo, batch, webhooks |
| [12 · Scope and limits](#12--scope-and-limits) | What this does not do |

---

## 1 · The problem

A subscription charge fails. What happens next, on almost every platform, is
the same thing regardless of *why* it failed: retry tomorrow, retry the day
after, retry the day after that, then send a dunning email.

That default is wrong in two directions at once.

**It wastes attempts on failures that cannot recover.** A card reported lost,
a revoked mandate, a closed account — these do not become chargeable because
you asked a fourth time. Each retry burns issuer goodwill, and on card
networks a repeatedly-retried soft decline can convert into a hard one. In our
batch, the blind-retry baseline spends **30 attempts per run** on declines
that were never going to succeed.

**It mistimes the failures that can.** `insufficient_funds` on the 28th is not
the same event as `insufficient_funds` on the 3rd. One is a customer who is
broke until payday; the other is a customer who just got paid. Blind retries
treat them identically.

<p align="center">
  <img src="docs/problem-blind-retries.svg" alt="Razorpay retries at T+1, T+2 and T+3 on a fixed schedule, then halts the subscription. Salvage places one diagnosed attempt at the next payday window instead, which does not consume the retry budget." width="100%">
</p>

**The lever is documented, not clever.** Razorpay's own docs state that a manual
charge attempt on an `issued` invoice does not count toward a subscription's
remaining auto-retries. So a precisely-timed attempt can be added *on top of*
the T+1/T+2/T+3 safety net rather than spending it. That is the product's key
lever, and the reason a diagnosed attempt is strictly additive rather than a
substitute for the retries you already get.

And underneath both, in India, sits a regulatory layer that a retry loop has
no concept of at all: RBI's e-mandate framework requires a pre-debit notice at
least 24 hours before an auto-debit, caps un-authenticated auto-debit at
₹15,000 (₹1,00,000 for insurance, mutual funds and credit-card bills), and the
Fair Practices Code forbids recovery contact outside 08:00–19:00 IST or to any
third party. A dunning email fired by a cron job at 02:00 IST is not an
inefficiency. It is a violation.

> **Salvage sits between `payment.failed` and `subscription.halted`.** It
> diagnoses *why* a charge failed, chooses the right intervention and the
> right moment, forces every money action through hard compliance gates,
> and records the whole reasoning chain so a human can audit it afterwards.

---

## 2 · What Salvage is

Salvage is an **agent with deliberately bounded authority**. That phrase is
doing real work, so here is exactly what it means.

### The loop

```
   observe          a payment.failed webhook arrives, HMAC-verified on the raw body
      ↓
   classify         115 deterministic taxonomy rules map Razorpay's 110 published
                    error reasons onto 9 canonical failure classes
      ↓
   diagnose         an LLM reasons about the case — and only about the case
      ↓
   decide           a priority-ordered YAML rulebook proposes ONE action
      ↓
   permit           eleven compliance gates run, in order, every time
      ↓
   time             policy schedules it for when it is worth doing
      ↓
   re-permit        at fire time, all eleven gates run again against reality
      ↓
   execute          one door, one idempotency key, one pre-audit row first
      ↓
   record           append-only, hash-chained, renderable as a Decision Tape
```

### What the agent may do

Exactly ten actions exist. Nothing outside this list can execute, because
`execute_action()` validates against the enum before dispatch:

| | Action | What it is |
|---|---|---|
| A1 | `RETRY_NOW` | Charge the mandate immediately |
| A2 | `RETRY_SCHEDULED` | Charge at a policy-chosen future moment |
| A3 | `SEND_PRE_DEBIT_NOTICE` | The RBI-mandated 24h pre-transaction notification |
| A4 | `CREATE_REAUTH_LINK` | Customer-initiated re-registration of a dead instrument |
| A5 | `CREATE_AFA_LINK` | An AFA-authenticated flow for an over-threshold debit |
| A6 | `NUDGE` | A single contact, subject to the cooldown gate |
| A7 | `PAUSE_SUBSCRIPTION` | Stop the billing cycle |
| A8 | `ESCALATE_HUMAN` | Refuse to decide, and say so on the Exception Desk |
| A9 | `CLOSE_NO_ACTION` | Terminal: nothing further is worth doing |
| A10 | `SUPPRESS` | Terminal: the customer has opted out |

### What the agent may *not* do

This is the part that matters, and it is enforced structurally rather than
promised in a prompt.

**The LLM never picks an action.** `api/diagnose/` returns a `RootCause` — a
failure class, a confidence, an `is_recoverable` flag, up to three evidence
strings and a timing hint. That is its entire output surface. It has no field
in which to name an action, so it cannot name one. The action is chosen
*afterwards* by `api/policy/`, which contains no model at all.

**The LLM never sees a customer.** The prompt builder's only input type is
`DiagnosisContext`, and that dataclass has no field for a name, a contact, an
email or an amount. Leaking PII into a prompt is therefore a **type error at
the call site**, not an oversight that could hide inside a prompt template.

**The deterministic classifier outranks the model.** When the normaliser
returns anything other than `UNKNOWN`, its class wins and the model's is
recorded but discarded. `decisions.diagnosis.source` says which happened on
every single decision: `deterministic_override`, or `llm`.

**And we measured what that costs.** Across the batch, the LLM drove **2 of
~207 decisions per seed** (6 of 623 total). The LLM-enabled arm matched the
LLM-disabled arm on *every metric in every seed*, at roughly **40× the
decision latency**. We are reporting that as measured, because a boundary that
turns out to be load-bearing is a finding, not an embarrassment.

---

## 3 · Architecture

<p align="center">
  <img src="docs/architecture.svg" alt="The Salvage decision pipeline: ingest, normalise, diagnose, policy, the eleven-gate chain, executor and append-only audit, with the four boundaries marked." width="100%">
</p>

> The same diagram, interactive and with the reasoning attached, is served at
> **`localhost:5173/architecture.html`** when the web app is running.

Four boundaries do the real work. Each exists because a specific thing goes
wrong without it, and **each is checked mechanically** — `make verify` fails if
any is broken. `ARCHITECTURE.md` has the long form; this is the shape of it.

### ① The Razorpay SDK is imported in exactly one file

`api/executor/client.py`, and nowhere else in the repository.
`scripts/check_boundaries.py` AST-walks `api/`, `scripts/`, `sim/`, `eval/`
and `tests/` and fails the build otherwise.

This is the architectural answer to *"how do we know your agent can't go
rogue?"* — **there is one door, and the build breaks if someone cuts a
second one.** There are no exemptions, including for setup scripts:
`scripts/seed_razorpay.py` reaches Razorpay through the same client as
everything else.

> It briefly *had* an exemption. Day 0 shipped with `scripts/` outside the
> scan, and `make verify` stayed green throughout, because the exemption lived
> inside the enforcement mechanism. The scanned set is now derived from the
> repository rather than a hand-maintained list, so a directory added later
> cannot fall outside it.

### ② `events` and `audit` are append-only, in two layers

Their repositories expose `insert` and reads. No update, delete or replace
method exists on them, and `check_boundaries.py` fails the build if one is
added or if a mutating driver call targets either collection.

On top of that, every document carries `prev_hash` and `hash`, so a
modification made *outside* the application is still detectable.
`scripts/verify_chain.py` walks both chains and distinguishes a **MISMATCH**
(a document was edited) from an **ORPHAN** (one was deleted) from a **FORK**
(two writers appended to the same head).

The chain is tamper-**evident**, not tamper-**proof**. It detects
modification; it does not prevent it. A production system would need
append-only storage or a WORM bucket. That distinction is in `LIMITATIONS.md`
and we say it out loud rather than glossing it.

### ③ The LLM cannot choose a money action

Covered in [§2](#what-the-agent-may-not-do). Two structural facts back it
rather than a promise: `DiagnosisContext` has no PII field, and the
deterministic normaliser wins whenever it is not `UNKNOWN`.

### ④ The gate chain cannot be bypassed

`evaluate_chain()` runs all eleven gates in order on every decision —
*including the gates after one has already blocked*, because the Decision Tape
shows every verdict and not just the first refusal. No argument, flag or
config value reorders or skips any of them, and `G0_KILL` is first.

Two subtleties, both found by adversarial review rather than by design, and
both now the reason this boundary holds:

- **A BLOCK can convert into a remedy.** `G2_PREDEBIT` blocking a charge
  becomes a pre-debit notice. That substitute is a compliance-relevant action
  in its own right — a customer contact — and dispatching it on the *blocked*
  action's gate pass would be a full-chain bypass.
  `resolve_with_remediation()` re-gates every substitute from scratch, in a
  bounded loop of at most 3 hops.
- **When that loop exhausts its bound, nothing dispatches.** An earlier
  version fell back to dispatching an unverified action on that branch — the
  same bypass shape, reintroduced inside the fix for it. The exhaustion path
  now records a blocked decision and stops.

**Gates re-evaluate at fire time**, not only at schedule time. A retry
scheduled at 10:00 and fired at 19:30 is judged against 19:30's reality, which
is the entire point of a compliance window.

### Timing is policy's job, not the gates'

Gates decide what is *permitted*. `api/policy/timing.py` decides when a
permitted charge is *worth making* — the next payday window, or 24 hours after
the last attempt. It can only ever push an action **later**, never earlier and
never past a gate, and whatever it schedules is re-gated in full when it fires.

> This boundary was missing until day 5. Policy rules carried
> `timing: NEXT_PAYDAY_WINDOW` from day 3 and the pipeline dropped it, so every
> scheduled retry fired immediately — the baseline's timing, wearing Salvage's
> name. **The batch found it by losing to the baseline.**

---

## 4 · How it works: one decision, end to end

A `payment.failed` webhook arrives. Here is every step it passes through.

**1 · Ingest** — `api/ingest/`
HMAC-SHA256 verified against `RAZORPAY_WEBHOOK_SECRET` on the **raw request
body**, before any parsing or casting. Deduped on `rz_event_id`. Stored in
`events`, append-only and hash-chained.

**2 · Normalise** — `api/normalise/`
115 deterministic rules map Razorpay's 110 published `error.reason` values
onto **9 canonical failure classes**. Rules are ordered most-specific first:
exact `reason` match, then `description_contains` (for the common real case
where `reason`/`source`/`step` are all null), then `code` alone — used only for
`GATEWAY_ERROR`/`SERVER_ERROR`. No match returns `UNKNOWN`. **Never a guess.**

| Class | Recoverable? | Default play |
|---|---|---|
| `TEMPORARY_LIQUIDITY` | Yes | Retry, aligned to the next payday window |
| `ISSUER_HOLD` | Yes | Retry in ~24h |
| `CHANNEL_DOWNTIME` | Yes | Retry after the outage clears |
| `AUTH_ABANDONED` | Yes | Nudge, then retry |
| `LIMIT_EXCEEDED` | Sometimes | Retry later, or split |
| `CREDENTIAL_STALE` | Via customer | Re-auth link |
| `CREDENTIAL_DEAD` | No | Re-auth link, stop all retries |
| `MANDATE_INVALID` | No | AFA-authenticated re-registration |
| `UNKNOWN` | Unknown | **Escalate to a human. Do not guess.** |

**3 · Diagnose** — `api/diagnose/`
The one LLM call. It receives only `DiagnosisContext` (failure reason string,
attempt count and timestamps, prior failure classes, mandate type, prior
successful charge dates) and returns a `RootCause`. Pydantic validates with
`extra="forbid"`, so a model that hallucinates a field fails validation and
triggers a repair retry rather than passing silently. If the normaliser
returned anything but `UNKNOWN`, the normaliser wins.

**4 · Policy** — `api/policy/`
11 priority-ordered YAML rules, first match wins, in bands:
`1–19 Stop · 20–39 Escalate · 40–59 Re-auth · 60–79 Retry-scheduled ·
80–89 Retry-now · 90–98 Nudge · 99 catch-all`. Every rule carries a `because`
that explains the *payments reasoning* and a `cites` list pointing at its
source. `scripts/check_clauses.py` fails the build if either is missing.

```yaml
- id: POL-001
  priority: 10
  when:  { failure_class: CREDENTIAL_DEAD }
  then:  { action: CREATE_REAUTH_LINK, stop_all_retries: true }
  because: >
    Hard decline on a dead instrument. Retrying burns issuer goodwill and
    can convert a soft decline into a hard one; the only productive move is
    a customer-initiated re-registration, not another silent charge attempt.
  cites: ["razorpay/docs/errors/payments/cards", "industry:hard-decline-no-retry"]
```

**5 · Gates** — `api/gates/`
All eleven, in order, always. Each returns `ALLOW`, `BLOCK` or `REPLAN` with
its own `CLAUSE_REF` attached — *even on ALLOW*, so the record of which clause
was checked is complete either way. Any `BLOCK` blocks. Otherwise the latest
`REPLAN` wins. See [§5](#5--the-eleven-gates).

**6 · Remediation** — `resolve_with_remediation()`
If a gate blocked but the block has a remedy (`G2_PREDEBIT` → send the notice;
`G3_AFA` → create the AFA link), the substitute goes through **the entire
chain again** from scratch. Bounded at 3 hops. Exhaustion dispatches nothing.

<p align="center">
  <img src="docs/remediation.svg" alt="A proposed scheduled retry is refused by gates G2 and G3. Salvage substitutes a pre-debit notice and re-gates it from scratch, producing 22 gate verdicts on one decision." width="100%">
</p>

A refusal is not the end of the decision. It is the start of a different one —
and that one is checked just as hard.

**7 · Execute** — `api/executor/`
The only door. Writes its **pre-audit row first**, then dispatches.
`DuplicateKeyError` on the `(idempotency_key, phase)` unique index means
already executed — return the prior result, never re-dispatch. Then a `post`
row with the outcome.

**8 · Record** — `api/db/`
One decision cycle produces exactly one `decisions` document and the `audit`
rows for whatever it dispatched or refused, joined on `decision_id`. The
frontend renders that document **directly** rather than a projection invented
for it, so there is one definition of what a decision is.

`run_id` partitions everything: the live system writes to `None`, and each
eval batch to its own partition, **including its own hash chain**. A batch's
forty synthetic mandates share a database with real traffic but never a chain,
a case lookup, or the Command Deck.

---

## 5 · The eleven gates

Every gate module exports `CLAUSE_REF` and `RULE_TEXT` as module constants.
`scripts/check_clauses.py` fails the build if either is missing — so a gate
that cannot say which rule it enforces cannot ship. `COMPLIANCE.md` carries
the full verbatim mapping including scope caveats.

<p align="center">
  <img src="docs/gate-chain.svg" alt="The eleven-gate compliance chain G0 through G10, each with the rule it enforces and the clause it cites." width="100%">
</p>

| Gate | Enforces | Source | Verdicts |
|---|---|---|---|
| **G0_KILL** | Kill switch engaged → nothing executes, of any kind | Salvage Ops | `BLOCK` |
| **G1_WINDOW** | No customer contact before 08:00 or after 19:00 IST | RBI DOR.ORG.REC.65/21.04.158/2022-23 (12 Aug 2022) | `REPLAN` |
| **G2_PREDEBIT** | No auto-debit without a notice sent ≥24h prior | RBI E-mandate Framework 2026 | `BLOCK` → notice |
| **G3_AFA** | Un-authenticated auto-debit capped at ₹15,000 (₹1,00,000 for insurance / MF / CC-bill) | RBI E-mandate Framework 2026 | `BLOCK` → AFA link |
| **G4_BUDGET** | ≤4 Salvage-initiated attempts per case per rolling 14 days | Card network retry limits | `BLOCK` |
| **G5_COOLDOWN** | ≥48h between contacts; ≤3 contacts per rolling 7 days | Anti-harassment research | `REPLAN` |
| **G6_OPTOUT** | Opted out → no further contact or debit, permanently | RBI E-mandate Framework 2026 | `BLOCK` |
| **G7_THIRDPARTY** | Contact the account holder only — never an employer, relative or neighbour | RBI Fair Practices Code | `BLOCK` |
| **G8_POSTDEBIT** | A successful debit must be followed by a confirmation with grievance-redressal details | RBI E-mandate Framework 2026 | `BLOCK` |
| **G9_HALTED** | A halted subscription may not be auto-charged | Razorpay subscription state machine | `BLOCK` |
| **G10_IDEMPOTENT** | An idempotency key with an existing audit record never dispatches again | CLAUDE.md invariant 9 | `BLOCK` |

Two design notes worth stating plainly, because both are places where the code
is narrower than the clause it cites, and `COMPLIANCE.md` says so per-row:

- **G1 gates customer-contact actions only.** The cited circular governs
  contact and calling conduct, not silent auto-debit timing, so a silent
  `RETRY_NOW` is exempt from the window. That is a deliberate reading, not an
  oversight.
- **G2 checks lead time only.** It verifies a `SEND_PRE_DEBIT_NOTICE` audit row
  exists ≥24h before the charge. It does not yet verify the notice's *content*
  matched the amount and date now being charged.

### The ten non-negotiable invariants

These are `CLAUDE.md`'s contract with itself, and every one is checked
mechanically rather than by convention:

1. Only `api/executor/client.py` may import the Razorpay SDK.
2. The LLM never chooses a money action.
3. No PII in LLM prompts — there is a test for this.
4. Gates cannot be bypassed. `G0_KILL` is evaluated first, always.
5. Gates re-evaluate at fire time, not only at schedule time.
6. `events` and `audit` are append-only; their repositories expose `insert` only.
7. Every gate exports `CLAUSE_REF` and `RULE_TEXT`; every policy rule has `because` and `cites`.
8. `DRY_RUN` defaults true. Going live requires an explicit arm call with a confirm token.
9. Every executor dispatch carries an idempotency key and inserts its pre-audit row first.
10. Timestamps stored UTC, gate logic evaluated IST, zone always rendered.

### Three barriers stand between this and real money

All three would have to be removed deliberately:

1. **`DRY_RUN` defaults true**, read from the environment at startup. No HTTP
   route changes it.
2. **`execute_action()` reaches a live dispatch only when `dry_run` is false
   *and* the process has been armed** with the exact token in
   `ARM_CONFIRM_TOKEN`. `POST /control/arm` calls the real arm function and
   enforces the real token; an unset token cannot be satisfied by an empty
   string.
3. **`client.dispatch()` is an unimplemented stub.** Live Razorpay execution
   is out of scope for this submission, so the live branch raises rather than
   charging anyone.

The kill switch is the exact opposite, deliberately: it needs **no token**,
engages instantly from `POST /control/kill` or the status bar, and `G0_KILL`
is evaluated first in every chain. **Stopping never needs protecting.**

---

## 6 · The interface

Four screens. No component library — the palette is ink-on-paper, and the
Decision Tape is the single signature element.

| Screen | Route | What it is |
|---|---|---|
| **Command Deck** | `/` | Four counters, recovery by failure class, and the live decision feed over SSE |
| **Case View** | `#/case/:id` | The case stated in a compliance reviewer's terms, then Decision Tapes, newest first |
| **Exception Desk** | `#/exceptions` | What the agent *refused* to decide, and why |
| **How It Works** | `#/how` | The rulebook and gate chain, rendered from the live API |

### The Decision Tape

One decision cycle, rendered as a timecoded strip down a ruled spine. Each
step is a chit. **Gates that passed are quiet; gates that blocked are struck
through with the clause overprinted at an angle, like a rubber stamp.**

That contrast is the whole product in one image: the system considered an
action, and something written down stopped it.

On the Command Deck, **`Blocked by compliance` is given exactly the same
visual weight as `Recovered`.** That is deliberate and it is the argument of
the product: restraint is an outcome, not the absence of one.

The Exception Desk splits two things that look similar and are not. A case the
agent declined to act on is **the system working** — an `UNKNOWN` failure class
escalates to a human rather than guessing at a money action. A row in
`exceptions` is **the system failing** — the pipeline raised, or the hash chain
could not be appended. A system that only shows its successes is not showing
its work.

---

## 7 · Results: measured, and reported against ourselves

40 synthetic mandates × 3 seeds × 3 arms. **Decisions from the real pipeline;
outcomes from a calibrated simulator** (`sim/`). Reported as a **range across
seeds** — never a confidence interval, because N=3 does not support one.
Produced by `make batch`, committed verbatim as `eval/results.json`.

- **A — Baseline.** Razorpay's documented default: blind retries at T+1, T+2, T+3, plus one generic dunning email. No diagnosis, no gates.
- **B — Salvage.** The full stack.
- **C — Salvage, LLM disabled.** The ablation.

| | A · baseline | B · Salvage | C · Salvage, no LLM |
|---|---|---|---|
| Net recovery (range) | 20.9 – 46.1% | 30.9 – 40.9% | 30.9 – 40.9% |
| Net recovery (mean) | 35.4% | 34.8% | 34.8% |
| Attempts per recovery | 4.09 – 5.30 | **2.89 – 3.39** | **2.89 – 3.39** |
| Wasted attempts on hard declines | **30 per run** | **0** | **0** |
| Contacts outside 08:00–19:00 IST | **19 – 29** | **0** | **0** |
| Actions stopped by a gate | 0 | 53 – 63 | 53 – 63 |
| Compliance violations | — | **0** | **0** |
| Decision latency p50 / p95 | — | 262–312ms / 1032–1159ms | 5–7ms / 19–22ms |

### What this does *not* show

**It does not show that Salvage recovers more money.** The means are 35.4% and
34.8%, and the ±20% sensitivity sweep **reverses the ranking** — at ×0.8 the
baseline leads, at ×1.2 Salvage does. The recovery comparison sits inside the
noise of a calibration we wrote ourselves, and **no number here should be
quoted as a recovery uplift.** `sim/CALIBRATION.md` says the same in more
detail, including the sentence that matters most in it: *there are zero
`measured` values* — every calibration parameter is labelled `cited` or
`assumed`, because Razorpay's documented test-mode decline reasons did not
reproduce in this account.

**Arm A's ladder is doc-derived, not observed.** Day 1 could not reproduce
Razorpay's retry timing in test mode. A doc-derived baseline is weaker evidence
than a measured one, and that caveat applies to every row above.

### What it *does* show

These hold across every seed, regardless of calibration:

- **~35% fewer charge attempts** for comparable recovery.
- **Zero attempts spent on declines that cannot recover.** The baseline spends
  30 per run — it retries dead credentials three times each, because it does
  not know what it is retrying.
- **Zero compliance violations**, audited from the record of what was actually
  dispatched rather than from the gate verdicts that were *supposed* to
  guarantee it. The baseline's blind dunning email lands outside the RBI
  contact window on roughly a fifth of cases.
- **A much tighter spread across seeds** (30.9–40.9 against 20.9–46.1). Fewer,
  better-timed attempts produce a more predictable result.

And the finding we committed in advance to publishing whichever way it came
out: **the LLM changed nothing measurable.** Arm B matched arm C on every
metric in every seed, at ~40× the latency. Invariant 2 is exactly why.

---

## 8 · Verification — the gate

```bash
make verify
```

Nothing is "done" until this is green. It runs seven steps, and **every step
runs even after an earlier one fails** — a gate that stops at the first failure
tells you one thing per run; this one tells you everything that is wrong in a
single pass, which is what makes it usable for self-correction.

| # | Step | What it proves |
|---|---|---|
| 1 | `ruff check` + `ruff format --check` | Lint and format |
| 2 | `mypy --strict api/` | Full strict typing, no escape hatches |
| 3 | `pytest` | **536 tests**, with **100% branch coverage required** on `api/gates/` and `api/normalise/` |
| 4 | `scripts/check_boundaries.py` | Razorpay SDK confined to the executor; no writes to append-only collections |
| 5 | `scripts/check_clauses.py` | Every gate cites a clause; every policy rule explains itself |
| 6 | `scripts/verify_chain.py` | Event and audit hash chains intact — MISMATCH / ORPHAN / FORK |
| 7 | `scripts/verify_web.sh` | `tsc --noEmit`, eslint, vitest, vite build |

A missing tool is a **failure**, not a silent skip — an unrun check proves
nothing. And the rule that makes the whole thing worth having:

> **Do not weaken a check to make it pass. Fix the code.**

Checks 4–6 were written on day 2, *before* they were needed. They are what
makes self-correction possible: a failing boundary check tells you exactly what
you broke.

---

## 9 · Repository map

```
salvage/
├── api/                        5,400 LOC · FastAPI + Motor + Pydantic v2
│   ├── ingest/                 webhook receipt — HMAC on the raw body, dedupe
│   ├── normalise/              deterministic classifier · 100% branch coverage
│   ├── diagnose/               THE ONLY LLM · returns a diagnosis, never an action
│   ├── policy/                 YAML rulebook + timing · no model in here
│   │   └── rules/recovery.yaml 11 priority-ordered rules, each with because + cites
│   ├── gates/                  G0–G10 · 100% branch coverage · CLAUSE_REF + RULE_TEXT
│   ├── executor/               THE ONLY DOOR · sole Razorpay SDK importer
│   ├── scheduler/              fire-time gate re-evaluation
│   ├── db/                     repositories, append-only enforcement, hash chain
│   ├── routes/                 cases · control · metrics · SSE stream
│   └── pipeline.py             webhook → normalise → diagnose → policy → gates → execute
│
├── web/                        React + Vite + TS + Tailwind · no component library
│   ├── src/components/         CommandDeck · CaseView · DecisionTape · ExceptionDesk
│   └── public/architecture.html  the interactive walkthrough of the diagrams above
│
├── taxonomy/                   110 published reasons → 9 failure classes
├── sim/                        calibrated outcome simulator + CALIBRATION.md
├── eval/                       A/B/C harness, cost model, results.json
├── tests/                      7,000 LOC · 536 tests
├── scripts/                    verify.sh + the four mechanical checks
├── docs/                       the diagrams above, as standalone SVG
│
├── PRD.md                      the contract · requirements + data model · AUTHORITY
├── SALVAGE_SPEC.md             the reasoning behind the contract
├── ARCHITECTURE.md             the diagram, and why each boundary exists
├── COMPLIANCE.md               gate ↔ clause mapping, with scope caveats per row
├── LIMITATIONS.md              what this does not do, written harsher than a judge would ask
├── NOTES.md                    running log of every bug found and every surprise
└── CLAUDE.md                   the ten invariants, and how the repo is worked on
```

### Documents, and which to read first

| File | What it is | Read it if |
|---|---|---|
| `ARCHITECTURE.md` | The diagram and the four boundaries | You want the design in 10 minutes |
| `COMPLIANCE.md` | Gate ↔ clause mapping, verbatim, with scope | You want to check the regulatory claims |
| `LIMITATIONS.md` | Everything this does not do | You are about to ask us a hard question |
| `sim/CALIBRATION.md` | What every simulator number rests on | You want to interrogate §7's results |
| `PRD.md` | The contract. Requirements and data model | You want the authority, not the summary |
| `SALVAGE_SPEC.md` | The research and reasoning behind the PRD | You want the *why* under the *what* |
| `NOTES.md` | Every bug found, how, and the fix | You want to see how the work actually went |
| `CLAUDE.md` | The ten invariants and the working rules | You want to see the engineering discipline |

> When code and `PRD.md` disagree, **the PRD wins** — or the PRD changes in the
> same commit, with a reason.

---

## 10 · Setup

### Prerequisites

| | |
|---|---|
| **Python 3.12** | `pyproject.toml` declares `requires-python = ">=3.12"`; ruff and mypy target `py312` |
| **Node 20+** | For the Vite frontend |
| **MongoDB** | Atlas M0 free tier, or a local `mongod`. No Redis — unique indexes handle idempotency |
| **Razorpay test account** | Key ID, key secret and a webhook secret. Test mode only |
| **Groq API key** | Free tier. Optional — set `DISABLE_LLM=true` to run the deterministic path alone |

### Install

```bash
git clone <your-repo-url> salvage && cd salvage

make install                  # creates .venv, installs python deps + node_modules
```

If Python 3.12 is not your default interpreter, point `make` at it:

```bash
make install PYTHON=/opt/homebrew/bin/python3.12
```

### Credentials

```bash
cp .env.example .env
```

`.env.example` is the committed contract; `.env` is git-ignored and must never
be committed. Fill in these five, and leave the rest at their defaults:

```ini
RAZORPAY_KEY_ID=rzp_test_xxxxxxxxxxxxxx
RAZORPAY_KEY_SECRET=
RAZORPAY_WEBHOOK_SECRET=          # from Razorpay → Settings → Webhooks
MONGO_URI=mongodb+srv://USER:PASSWORD@cluster0.xxxxx.mongodb.net/
LLM_API_KEY=                      # Groq; or set DISABLE_LLM=true
```

The safety-relevant settings, and what they mean:

| Variable | Default | Meaning |
|---|---|---|
| `DRY_RUN` | `true` | **Leave it.** Nothing dispatches to Razorpay while true |
| `ARM_CONFIRM_TOKEN` | `change-me-before-arming` | Required, exactly, by `POST /control/arm` |
| `KILL_SWITCH` | `false` | Set true to freeze every action at `G0` |
| `DISABLE_LLM` | `false` | Runs the deterministic path alone — arm C |
| `SCHEDULER_TICK_SECONDS` | `0` | `0` disables the scheduler. Set `5` to watch fire-time re-gating live |
| `COMPLIANCE_TZ` | `Asia/Kolkata` | Gates reason in IST while the server stores UTC |

### Confirm the install

```bash
make verify                   # expect: verify: OK — 8/8
```

---

## 11 · Running it

### Development

```bash
make dev                      # api on :8000 + web on :5173, dry-run
```

Then open **http://localhost:5173**. Post a `payment.failed` webhook to
`POST /webhooks/razorpay` and a case opens, a decision is recorded, and the
Decision Tape renders it live over SSE.

Run the halves separately if you prefer:

```bash
make api                      # uvicorn only, :8000
make web                      # vite only, :5173
```

### Watching the scheduler re-gate at fire time

The default `SCHEDULER_TICK_SECONDS=0` disables the scheduler. To see
invariant 5 actually happen — a scheduled action being re-judged against the
clock at the moment it fires, not the moment it was queued:

```bash
DRY_RUN=true SCHEDULER_TICK_SECONDS=5 \
  .venv/bin/python -m uvicorn api.main:app --port 8000
```

### The A/B/C batch

```bash
make batch                    # → eval/results.json
```

40 mandates × 3 seeds × 3 arms. Decisions come from the real pipeline; outcomes
from `sim/`. Writes to its own `run_id` partition with its own hash chain, so it
never touches live traffic or the Command Deck. Takes ~80s per seed with the LLM
enabled, ~2s without.

### Receiving real Razorpay webhooks

Razorpay needs a public URL, and it must survive restarts. `make tunnel` reads
`PUBLIC_WEBHOOK_URL` from `.env` and always requests the same reserved ngrok
domain, so you register the webhook in Razorpay **once**:

```bash
make tunnel
# → https://<your-domain>/webhooks/razorpay   (subscribe to payment.failed only)
```

Set it once in **Razorpay → Settings → Webhooks**. Edit the webhook you already
have; do not add a second one.

### Seeding test-mode data

```bash
make seed                     # creates test-mode plans + subscriptions
```

> ⚠️ **Razorpay test-mode card tokens expire in 3 days.** Seeding plans and
> subscriptions early is fine. *Authenticating a card mandate* early is not —
> the token is dead three days later and you find out on the morning of the
> demo. Also: 30 Payment Links max per test business, so links are never
> created inside a batch run.

### Every command

| Command | What it does |
|---|---|
| `make install` | `.venv` + `node_modules` |
| `make dev` | API + web, dry-run |
| `make verify` | **The gate** — lint, types, tests, boundaries, clauses, chain, web |
| `make test` | pytest with coverage |
| `make chain` | Verify the audit hash chains only |
| `make batch` | The A/B/C eval → `eval/results.json` |
| `make seed` | Create test-mode plans/subscriptions in Razorpay |
| `make tunnel` | Expose the API on your reserved ngrok domain |
| `make clean` | Remove `.venv`, `node_modules`, caches |

### The API

| Method | Endpoint | |
|---|---|---|
| `POST` | `/webhooks/razorpay` | Webhook receipt. HMAC-verified on the raw body |
| `GET` | `/cases` | Case list with filters |
| `GET` | `/cases/{id}` | One case, with every decision |
| `GET` | `/cases/{id}/audit` | The audit trail for a case |
| `GET` | `/stream/decisions` | SSE — the live decision feed |
| `GET` | `/rulebook` | The policy rules and gate chain, as the API sees them |
| `GET` | `/exceptions` | What the agent refused to decide |
| `GET` | `/metrics/batch` · `/metrics/batch/{run_id}` | Batch results |
| `GET` | `/control/status` | Posture: dry-run, armed, kill switch |
| `POST` | `/control/arm` · `/control/disarm` | Arming requires the exact confirm token |
| `POST` | `/control/kill` | **No token required. Stopping never needs protecting.** |
| `GET` | `/health` | Liveness |

---

## 12 · Scope and limits

Deliberate cuts, documented rather than silent:

- **No live Razorpay dispatch.** `DRY_RUN` stays true and `client.dispatch()`
  is an unimplemented stub. Every code path up to that door is real.
- **No in-doubt reconciliation** and **no reconciliation sweep** — a dispatch
  whose outcome is genuinely unknown is not resolved by a later sweep.
- **Only `payment.failed` is handled.** That is the one event the product's
  thesis is about; the other five webhook types have no state-machine handling.
- **The hash chain is tamper-evident, not tamper-proof.**
- **The boundary checks are AST heuristics, not proofs.** A collection reached
  through an indirectly-named variable is not caught.
- **G6_OPTOUT fails open** when a case has no resolvable `customer_id`, and
  opt-out has no live trigger wired. A documented latent gap, not a live one.
- **G7_THIRDPARTY is a proven safety net, not a live filter** — no code path in
  this build ever sets `contact_target` to anything else.

`LIMITATIONS.md` is longer, harsher, and was written to be read before a judge
finds the same things.

### Out of scope, and staying that way

Checkout abandonment. B2B receivables. Voice. Chargebacks. Multi-gateway
routing — that is Optimizer's job, not ours.

**Depth on one loss class with honest numbers beats four shallow loops.**

---

<div align="center">

**Salvage** · Razorpay AI Buildathon 2026 · Track 03

*The LLM diagnoses. It never decides to move money.*

</div>
