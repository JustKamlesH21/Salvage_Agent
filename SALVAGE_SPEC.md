# SALVAGE — Build Specification v1.1

**Compliance-gated mandate & payment recovery agent**
Razorpay AI Buildathon 2026 · Track 03 — AI Revenue Recovery

> Companion documents: `PRD.md` (requirements + data model), `CLAUDE.md` (repo invariants), `KICKOFF.md` (Claude Code workflow).
> **Changed in v1.1:** data layer moved from Postgres to MongoDB. Redis dropped. Hash-chained audit added. §4.3 now defers to PRD §8.

---

## 0. One-liner

When a recurring charge fails, Salvage diagnoses *why*, chooses the *right* intervention at the *right* time, forces every money action through hard RBI compliance gates, executes it on Razorpay test-mode APIs, and proves how much money it recovered against a measured baseline.

**Positioning sentence for the video:** *"The LLM diagnoses. It never decides to move money."*

---

## 1. The wedge

Razorpay already solves the **pre**-transaction problem. Optimizer routes each attempt to the gateway most likely to succeed, monitoring success rates and creating temporary 20-minute gateway downtimes when SR drops.

Nobody solves the **post**-failure problem intelligently. Razorpay's own subscription retry is a fixed timer:

> T=0 charge fails → subscription moves to `pending` → auto-retry on T+1, T+2, T+3 → if all fail, subscription moves to `halted`.

Four blind attempts, same schedule, same time of day, regardless of cause. Industry research is unambiguous that this is the wrong shape:

- Insufficient-funds declines clear around payday, not on day 3. Retries should be spaced 3–5 days and aligned to salary dates (1st / 15th / month-start in India).
- Expired or reissued cards cause roughly 10–15% of recurring failures and **never** recover on retry — every attempt is wasted budget.
- Nighttime retries carry roughly 2% lower success due to tighter issuer risk controls; refusal rates peak at month-end just before payday.
- Processor timeouts want an immediate retry through a different path; temporary issuer holds want ~24h.
- Best practice converges on decline-code-routed categories (retry-now / retry-scheduled / retry-with-updater / dunning-required / do-not-retry) rather than per-code handling.

**Salvage is the brain that sits between `payment.failed` and `subscription.halted`.**

### 1.1 The doc-grounded trick (say this in the video)

From Razorpay's own docs:

> A manual charge attempt on an `issued` invoice **does not count toward the remaining retries** for a Subscription.

That means Salvage can place a *precisely timed* charge attempt (payday morning, 10:00 IST) **without burning the auto-retry budget**. The subscription keeps its T+1/T+2/T+3 safety net while Salvage adds intelligent attempts on top. This is a real, documented lever that a fixed-schedule system cannot use. It is also the most defensible "I read your docs properly" moment available in this track.

---

## 2. Scope

### In scope
One loss class: **failed recurring charges on Razorpay Subscriptions** (card + UPI Autopay mandates), from first `payment.failed` through recovery or terminal close.

### Out of scope (say so explicitly in the README)
Checkout abandonment. B2B receivables. Voice. Chargebacks. Multi-gateway routing (that's Optimizer's job, not ours).

**Rationale:** Track 03's bar punishes cherry-picking. Depth on one loop with honest numbers beats four shallow loops.

---

## 3. Feature list

### P0 — MVP, must ship (the demo dies without these)

| ID | Feature | Notes |
|---|---|---|
| F01 | Webhook ingest with HMAC signature verification | `payment.failed`, `subscription.pending`, `subscription.halted`, `subscription.charged`, `invoice.paid`, `payment_link.paid` |
| F02 | Idempotency guard | Unique index on `events.(rz_event_id, signature_valid)`; replay-safe — see PRD §8.3 day-2 fix for why compound |
| F03 | Immutable, hash-chained event log | Append-only repository + `prev_hash`/`hash` chain |
| F04 | Failure normaliser | `(code, reason, source, step)` → canonical `FailureClass` |
| F05 | LLM diagnosis with Pydantic-constrained output | Bounded enum, confidence, evidence |
| F06 | Deterministic policy engine | Priority-ordered YAML rules, rule ids, doc citations |
| F07 | Compliance gate chain | 11 gates, non-bypassable, returns ALLOW / BLOCK(clause) / REPLAN(ts) |
| F08 | Bounded action executor | Single choke point, audit before + after |
| F09 | Dry-run mode (default ON) | Global flag; nothing hits Razorpay unless explicitly armed |
| F10 | Global kill switch | Freezes all pending actions instantly |
| F11 | Scheduler | Executes scheduled actions at their gate-approved timestamp, re-checking gates at fire time |
| F12 | Outcome simulator | Per-class success curves for batch measurement |
| F13 | Eval harness (A/B/C) | Baseline vs Salvage vs Salvage-minus-LLM |
| F14 | Audit trail API + case timeline | Every decision, rule id, clause, suppressed alternatives |
| F15 | Frontend: Command Deck | Batch overview, live feed, money counters |
| F16 | Frontend: Case View with Decision Tape | The signature screen |
| F17 | Frontend: Batch Evidence | A/B/C comparison, metrics, exception list |
| F18 | Exception list | Every case Salvage refused to handle, with reason |

### P1 — ship if time allows (each adds real panel value)

| ID | Feature | Why |
|---|---|---|
| F19 | Frontend: Rulebook screen | Human-readable gates + citations; makes compliance visible |
| F20 | Live Razorpay test-mode execution toggle | Real `plink_` / `inv_` ids in the audit trail |
| F21 | Payday model per customer segment | Salary-date inference from prior successful charge dates |
| F22 | Sensitivity analysis on simulator params | ±20% on success curves, show conclusions hold |
| F23 | Opt-out landing page | Honours the RBI-mandated opt-out from pre-debit notice |
| F24 | Hinglish nudge templates | Track 03 explicitly names Hinglish; cheap credibility |
| F28 | Reconciliation sweep | Day 1: a mandate-auth rejection above an undocumented Razorpay amount ceiling fires **no webhook at all**. Salvage is entirely webhook-driven, so this class of failure is invisible without a periodic poll for state drift no event explained. Shares machinery with the in-doubt reconciliation path (§5.6). See `PRD.md` FR-28, `NOTES.md`, `LIMITATIONS.md`. |

### P2 — stretch, only if everything above is done and tested

| ID | Feature |
|---|---|
| F25 | Bandit-style policy tuning over simulated outcomes |
| F26 | Cohort-level "why is recovery dropping" diagnostic |
| F27 | Card-updater simulation for `CREDENTIAL_STALE` |

**Discipline rule:** do not start any P1 until every P0 has a passing test. Do not start P2 at all unless Day 8 ends clean.

---

## 4. Domain model

### 4.1 Canonical failure taxonomy

Built from Razorpay's published error reason list (`code / description / source / step / reason`), collapsed into policy-relevant classes. **This mapping table is a repo artifact — `taxonomy/failure_classes.yaml` — with a source column pointing at Razorpay docs.**

| FailureClass | Example Razorpay reasons | Retryable | Default play |
|---|---|---|---|
| `TEMPORARY_LIQUIDITY` | `insufficient_funds` | Yes | Schedule to payday window |
| `ISSUER_HOLD` | bank declined, temporary hold | Yes | Retry ~24h later |
| `CHANNEL_DOWNTIME` | gateway/bank downtime, network error, `GATEWAY_ERROR` | Yes | Retry within hours |
| `AUTH_ABANDONED` | incorrect OTP, payment timeout (10 min limit) | Yes | Re-auth link, not blind retry |
| `LIMIT_EXCEEDED` | per-txn or velocity limit breached | Conditional | Split / reschedule / AFA link |
| `CREDENTIAL_STALE` | card expired, card not enabled for online, reissued | **No** | Card update / re-auth link |
| `CREDENTIAL_DEAD` | lost, stolen, account closed, invalid card | **No** | STOP retries, dunning only |
| `MANDATE_INVALID` | mandate revoked / paused / expired | **No** | Re-registration flow (needs AFA) |
| `UNKNOWN` | unmapped or absent reason | No | → exception list, never guessed |

`UNKNOWN` going to the exception list rather than a guess is a deliberate honesty feature. Say so out loud.

### 4.2 Action catalog (the only things Salvage can do)

| ID | Action | Razorpay surface |
|---|---|---|
| `A1` | `RETRY_NOW` | Manual charge on `issued` invoice |
| `A2` | `RETRY_SCHEDULED(ts)` | Manual charge on `issued` invoice at ts |
| `A3` | `SEND_PRE_DEBIT_NOTICE` | Notification (simulated channel + logged artifact) |
| `A4` | `CREATE_REAUTH_LINK` | Payment Links API |
| `A5` | `CREATE_AFA_LINK` | Payment Links API (amount > AFA threshold) |
| `A6` | `NUDGE(channel, template)` | SMS / email / WhatsApp, max 1 per 48h |
| `A7` | `PAUSE_SUBSCRIPTION` | Subscriptions API pause |
| `A8` | `ESCALATE_HUMAN` | Queue entry, no automated contact |
| `A9` | `CLOSE_NO_ACTION` | Terminal, with reason |
| `A10` | `SUPPRESS` | Opt-out honoured, permanent |

Anything not in this list cannot be executed. The executor validates the action id against the enum before dispatch.

### 4.3 Data model

**MongoDB.** Full collection shapes, indexes, immutability strategy and concurrency patterns live in **`PRD.md` §8** — that is the single authority. Do not duplicate it here.

The three properties the rest of this document assumes:

1. **Idempotency comes from unique indexes**, not a cache. `events` is unique on `(rz_event_id, signature_valid)`, and `audit` is unique on `(idempotency_key, phase)` — see PRD §8.3 (v1.2 for the audit pair, the day-2 fix for the events pair) for why each is compound rather than on a single field. No Redis in the stack.
2. **`events` and `audit` are append-only and hash-chained.** Their repositories expose `insert` only, and each document carries `prev_hash` / `hash`. `scripts/verify_chain.py` proves the chain intact — run it in the video.
3. **Case state transitions are single-document atomic** via `find_one_and_update` with a state predicate. No multi-document transactions are needed anywhere in the design.

---

## 5. Backend architecture

```
Razorpay Test Mode ──webhooks──▶ [1] INGEST
                                     │  HMAC verify (raw body) · unique-index dedupe
                                     │  raw → events (append-only, hash-chained)
                                     ▼
                                 [2] NORMALISE
                                     │  (code,reason,source,step) → FailureClass
                                     │  unmapped → exception, never guessed
                                     ▼
                                 [3] DIAGNOSE  (LLM, bounded)
                                     │  Groq/Llama → Pydantic RootCause
                                     │  enum-constrained · sees no ₹ amount, no PII
                                     │  deterministic rules WIN on conflict
                                     ▼
                                 [4] POLICY ENGINE  (100% deterministic)
                                     │  priority-ordered YAML
                                     │  STOP → ESCALATE → REAUTH →
                                     │  RETRY_SCHEDULED → NUDGE → NO_ACTION
                                     ▼
                                 [5] COMPLIANCE GATES  (hard, chained)
                                     │  G0..G10 → ALLOW | BLOCK(clause) | REPLAN(ts)
                                     ▼
                                 [6] EXECUTOR  (single choke point)
                                     │  insert pre-audit (idempotency key) · dispatch
                                     │  · insert post-audit
                                     │  DRY_RUN default · kill switch
                                     ▼
                        ┌────────────┴────────────┐
                        ▼                         ▼
                  [7] SCHEDULER            [8] OUTCOME ENGINE
                  APScheduler,             live: Razorpay webhook
                  gate re-check            batch: calibrated simulator
                  at fire time                     │
                        └────────────┬─────────────┘
                                     ▼
                                 [9] EVAL HARNESS  →  [10] AUDIT API → Frontend
```

### 5.1 Ingest

- `POST /webhooks/razorpay`
- Verify HMAC on the **raw body** — do not parse or cast before verifying. Razorpay's docs are explicit about this and it is a classic bug.
- Idempotency: insert into `events` and catch `DuplicateKeyError` on `rz_event_id`. Duplicate → 200 + `{"status":"duplicate"}`, no processing. Insert-first makes double-processing structurally impossible rather than checked-then-raced.
- Persist raw payload before any business logic. If step 2+ throws, the event is still recoverable.
- Return 200 fast; process async. Webhook URLs must use port 80/443 — use ngrok/cloudflared for local dev.

### 5.2 Normaliser

Pure function. No LLM. Table lookup against `failure_classes.yaml`. Returns `(FailureClass, confidence=1.0)` or `UNKNOWN`. 100% branch coverage required — this is the cheapest place to demonstrate rigour.

### 5.3 Diagnosis — the LLM's only job

**Contract:**

```python
class RootCause(BaseModel):
    failure_class: FailureClass  # enum, hard-constrained
    confidence: float  # 0..1
    is_recoverable: bool
    evidence: list[str]  # max 3, each ≤ 120 chars
    suggested_timing_hint: TimingHint  # enum: NOW | HOURS_24 | PAYDAY | NEVER
```

**Rules:**
1. The LLM receives: failure reason string, attempt history (count + timestamps + prior classes), mandate type, customer's prior successful charge dates. It does **not** receive the amount, the customer's name, contact, or any PII. There is a test for this.
2. If the deterministic normaliser returned anything other than `UNKNOWN`, the normaliser wins. The LLM output is stored in `decisions.llm_raw` for comparison but does not drive the decision.
3. Output that fails Pydantic validation → one repair retry → then `UNKNOWN` → exception list. Log both attempts.
4. `suggested_timing_hint` is a *hint*. The policy engine may ignore it. It never sets a timestamp directly.

This is the ablation arm C in the eval: run the whole system with step 3 disabled and show what the LLM is actually worth. If it turns out to be worth little, **report that honestly** — that finding is more impressive than a fake uplift.

### 5.4 Policy engine

Deterministic. YAML rules, evaluated in priority order, first match wins.

```yaml
- id: POL-001
  priority: 10
  when:
    failure_class: [CREDENTIAL_DEAD, MANDATE_INVALID]
  then:
    action: CREATE_REAUTH_LINK
    stop_all_retries: true
  because: "Hard decline. Retrying burns issuer goodwill and can convert a
            soft decline into a hard one."
  cites: ["razorpay/docs/errors/payments/cards", "industry:hard-decline-no-retry"]

- id: POL-004
  priority: 40
  when:
    failure_class: TEMPORARY_LIQUIDITY
    attempt_count: {lte: 3}
  then:
    action: RETRY_SCHEDULED
    timing: NEXT_PAYDAY_WINDOW      # resolves to dom 1 / 15 / month-start, 10:00 IST
    channel_note: "manual invoice charge — does not consume subscription retry budget"
  because: "Insufficient-funds declines clear on salary credit, not on a fixed
            day-3 timer. Nighttime and pre-payday attempts underperform."
  cites: ["razorpay/docs/payments/subscriptions/payment-retries",
          "industry:payday-aligned-retry"]

- id: POL-007
  priority: 70
  when:
    failure_class: CHANNEL_DOWNTIME
  then:
    action: RETRY_NOW
    max_delay_minutes: 45
  because: "Processor-side outages typically clear within hours; the
            authorization window is short."
  cites: ["razorpay/docs/errors/payments/cards#downtime"]
```

Every rule carries `because` and `cites`. These strings render in the UI. A judge clicking any decision sees *why* and *on whose authority*. `scripts/check_clauses.py` fails the build if either field is missing.

### 5.5 Compliance gates — the differentiator

Chained, ordered, non-bypassable. Each returns `ALLOW`, `BLOCK(clause, detail)`, or `REPLAN(new_ts, clause)`. **The LLM cannot see these, cannot call them, and cannot override them.**

| Gate | Rule | Authority |
|---|---|---|
| `G0_KILL` | Global kill switch engaged → block everything | Ops |
| `G1_WINDOW` | Customer contact only 08:00–19:00 IST. Applies to SMS, email, WhatsApp, automated reminders and calls alike — the channel does not change the obligation | RBI Fair Practices Code, recovery agent conduct |
| `G2_PREDEBIT` | No auto-debit attempt without a pre-transaction notice sent ≥24h prior, containing merchant name, amount, debit date/time, mandate reference and an opt-out facility | RBI Digital Payments E-mandate Framework 2026 (21 Apr 2026) |
| `G3_AFA` | Auto-debit without AFA permitted only up to ₹15,000/txn; ₹1,00,000 for insurance premiums, mutual fund subscriptions and credit-card bill payments. Above threshold → must route to an AFA-authenticated link, never a silent retry | same framework |
| `G4_BUDGET` | Max 4 Salvage-initiated attempts per case within a 14-day window (on top of Razorpay's own T+1/T+2/T+3). Excessive retries trigger issuer fraud controls and can convert soft declines to hard | Card network retry limits + industry practice |
| `G5_COOLDOWN` | Min 48h between customer contacts; max 3 contacts per 7 days | Anti-harassment, marginal-value-of-nth-email research |
| `G6_OPTOUT` | Opt-out is permanent and immediate. No further automated contact, ever | E-mandate Framework 2026 |
| `G7_THIRDPARTY` | Never contact employer, relatives, neighbours, or any non-account contact | RBI FPC |
| `G8_POSTDEBIT` | Every successful collection must be followed by a post-transaction confirmation including grievance-redressal details | E-mandate Framework 2026 |
| `G9_HALTED` | If subscription is `halted`, no auto-charge may be attempted — only manual invoice charge or customer-initiated re-auth | Razorpay subscription state machine |
| `G10_IDEMPOTENT` | Refuse any action whose idempotency key already exists in `audit` | Correctness |

**Implementation note:** gates are re-evaluated **at fire time**, not only at schedule time. A retry scheduled for 10:00 that gets picked up at 19:30 because of a scheduler backlog must be blocked, not executed. Build a test for exactly this.

**Every BLOCK writes an audit document with the clause reference.** The count of blocked-by-compliance actions is a headline metric on the dashboard, not a hidden log line.

Every gate module exports `CLAUSE_REF` and `RULE_TEXT`. The build fails if either is missing.

### 5.6 Executor

```python
def execute_action(case, decision, action, *, dry_run: bool) -> ActionResult:
    assert action.id in ACTION_CATALOG          # bounded set
    idem = f"{case.id}:{decision.id}:{action.id}"
    if kill_switch.engaged():                   # G0_KILL first, always
        return blocked(...)
    try:
        audit.insert(phase="pre", idempotency_key=idem, ...)   # BEFORE dispatch
    except DuplicateKeyError:
        prior = audit.prior_result(idem)        # post row, or None
        if prior is None:
            return in_flight(idem)              # pre row but no post row: the
                                                # dispatch is running or died
                                                # mid-flight. NOT "never tried".
        return prior                            # already executed, never re-dispatch
    result = simulate(action) if dry_run else razorpay_client.dispatch(action, idem)
    audit.insert(phase="post", response=result, ...)
    return result
```

Nothing else in the codebase may call the Razorpay client. `scripts/check_boundaries.py` AST-walks every top-level Python directory — `api/`, `scripts/`, `sim/`, `eval/`, `tests/` — and fails the build if the SDK is imported outside `api/executor/client.py`. There are no exemptions; `scripts/seed_razorpay.py` goes through `get_razorpay_client()` like everything else. Say this in the video — it is the architectural answer to *"how do we know your agent can't go rogue."*

### 5.7 Razorpay API surface used

| Purpose | Endpoint |
|---|---|
| Plans / subscriptions setup | `POST /v1/plans`, `POST /v1/subscriptions` |
| Fetch invoices for a subscription | `GET /v1/subscriptions/:id/invoices` |
| Manual charge on issued invoice | Invoice charge (does not consume retry budget) |
| Re-auth / AFA link | `POST /v1/payment_links` with `expire_by`, `reminder_enable`, `notify{sms,email}`, `reference_id`, `notes`, `callback_url` |
| Pause / resume | `POST /v1/subscriptions/:id/pause` / `/resume` |
| Test charge trigger | Dashboard, test mode only |

### 5.8 Test-mode constraints — know these before Day 1

These are real limits that will break your demo if discovered on Day 9:

1. **Card tokens are valid 3 days in test mode.** Subsequent debits only work inside that window. Plan the live-API portion of your demo around it; the batch runs on the simulator.
2. **Max 30 Payment Links per business in test mode.** Budget them. Never generate links inside a batch run.
3. **Manual charging of a domestic card is not supported.** So for domestic card mandates, the recovery path is pre-debit notice → Payment Link → customer-initiated. The manual-invoice-charge lever applies where supported. *Document this constraint explicitly in LIMITATIONS.md — a judge who knows the platform will check.*
4. **Razorpay's own auto-retry is T+1, T+2, T+3.** Your scheduling must interleave, not collide.
5. **Payment Link reminders only dispatch 11:00–12:00 and 15:00–17:00.** Convenient: both windows sit inside G1.
6. **Webhook URLs must use ports 80/443.**
7. A **test charge on a subscription** can only be triggered in test mode, from the Dashboard.

### 5.9 Outcome engine

Two modes, switchable:

- **LIVE** — real Razorpay test-mode webhooks drive outcomes. Used for the ~8 hand-run demo cases in the video.
- **SIM** — calibrated simulator drives outcomes. Used for the 500-case batch, because test mode will not produce realistic decline distributions or payday effects.

**Simulator model** — `P(success | failure_class, attempt_no, days_since_fail, hour_of_day, dom, mandate_type)`

Base rates by class, then multiplicative modifiers:

| Factor | Modifier | Grounding |
|---|---|---|
| Payday window (dom 1–3, 15–16) | ×1.0 | balances highest at month-start / mid-month |
| Pre-payday (dom 26–31) | ×0.72 | refusal rates peak just before payday |
| Night (19:00–08:00) | ×0.98 | ~2% lower success at night |
| Weekend | ×0.94 | lower retry success |
| `CREDENTIAL_STALE` / `CREDENTIAL_DEAD`, any retry | ×0.00 | hard declines never recover on retry |
| Attempt 3+ same class | ×0.85 decay | diminishing returns |

Every number lives in `sim/calibration.yaml` with a `source:` field marked *measured*, *cited*, or *assumed*. Write `sim/CALIBRATION.md` distinguishing the three honestly. **Then run F22 sensitivity analysis: ±20% on every modifier, show the ranking of A vs B doesn't flip.** That single chart pre-empts the loudest objection a judge can raise.

### 5.10 Eval harness

**Cohort:** 500 synthetic mandates. Realistic mix — roughly 40% `TEMPORARY_LIQUIDITY`, 15% `CREDENTIAL_STALE`, 10% `CHANNEL_DOWNTIME`, 8% `ISSUER_HOLD`, 7% `AUTH_ABANDONED`, 6% `CREDENTIAL_DEAD`, 5% `MANDATE_INVALID`, 4% `LIMIT_EXCEEDED`, 5% `UNKNOWN`. Amounts log-normal, ₹199–₹95,000, with a deliberate slice above the ₹15,000 AFA line and a slice in the insurance/MF category to exercise G3's ₹1L branch.

**Arms:**
- **A — Baseline.** Razorpay default: T+1, T+2, T+3, then halt. Plus one generic dunning email.
- **B — Salvage.** Full stack.
- **C — Ablation.** Salvage with LLM diagnosis disabled (normaliser + policy only).

**Protocol:** 20 seeds per arm, same seeds across arms. Report mean ± 95% CI.

**Metrics table (this is the money slide):**

| Metric | Why it's there |
|---|---|
| ₹ recovered | headline |
| Recovery rate (% of at-risk value) | normalised headline |
| **Net ₹ recovered** (minus attempt + contact + escalation cost) | the honest number |
| Attempts per successful recovery | efficiency |
| Contacts per customer | customer-experience cost |
| Median time-to-recovery | speed |
| **Compliance violations** | must be exactly **0** |
| Actions blocked by gate (by clause) | shows the gates do work |
| Exception rate | honesty |
| Wasted attempts on hard declines | the specific thing baseline does badly |
| Decision latency p50/p95 | it's a real system |

**Cost model** (`eval/costs.yaml`, all assumptions documented): gateway fee ~2% on success, failed attempt ₹0 direct but carries issuer-goodwill risk, SMS ₹0.15, WhatsApp ₹0.35, email ₹0.01, human escalation ₹40.

Reporting **net** recovery rather than gross is the single clearest signal of an honest builder in this track.

---

## 6. Frontend specification

### 6.1 Who it's for and what it must do

Audience: a Razorpay panel watching a 5-minute video, then clicking around a repo. The UI has exactly one job — **make every automated money decision legible and obviously bounded.** It is not a marketing page. It is a control room with a paper trail.

### 6.2 Design direction

The subject's own world is Indian regulated payments: mandate ledgers, RBI circulars, clause numbers, stamped approvals. That vernacular — the **stamped official record** — is the design language. Not a SaaS dashboard, not a fintech gradient.

**Signature element: the Decision Tape.** A horizontal, timecoded strip per case. Each step is a chit. Gates that passed are quiet. Gates that blocked are **struck through with the RBI clause overprinted at a slight angle, like a rubber stamp**. This is the one place boldness gets spent. Everything else stays disciplined.

**Palette** (deliberately not the warm-cream/terracotta or the black/acid-green defaults):

```
--ink        #12263A   deep official blue, primary text and chrome
--paper      #FBFAF7   cool off-white ground
--rule       #D6D2C8   hairline ledger rule
--stamp      #B3261E   BLOCKED, violation, stop
--ledger     #1F6F4A   RECOVERED, allowed
--amber      #B26B00   REPLANNED, pending, scheduled
--dim        #6B7A88   metadata, timestamps
```

**Type:**
- Display — **Bricolage Grotesque** (variable; used with restraint, headings and the money counters only)
- Body — **Public Sans** (institutional register, fits the regulatory subject)
- Data — **Martian Mono**, tabular figures, for every ₹ amount, id, timestamp and clause reference. Amounts must align vertically down a column. This is a ledger.

**Motion:** one orchestrated moment only — the Decision Tape draws left-to-right on case open, and blocked chits get their stamp overprinted a beat after the chit lands. Nothing else animates. Respect `prefers-reduced-motion`.

**Persistent chrome:** a status bar pinned top with `DRY RUN` / `ARMED` state and the kill switch. When armed, the bar changes colour. The judge should never be unsure which mode they're watching.

### 6.3 Screens

**S1 — Command Deck** *(landing)*

```
┌──────────────────────────────────────────────────────────────┐
│ SALVAGE          ⬤ DRY RUN            [ARM]  [KILL SWITCH]   │
├──────────────────────────────────────────────────────────────┤
│  ₹ AT RISK        ₹ RECOVERED       IN FLIGHT    BLOCKED BY  │
│  14,82,400        9,17,650          38 cases     COMPLIANCE  │
│                   61.9%                          47 actions  │
├───────────────────────────────┬──────────────────────────────┤
│  RECOVERY BY FAILURE CLASS    │  LIVE DECISION FEED          │
│  ▓▓▓▓▓▓▓▓░░ TEMPORARY_LIQ 78% │  10:04:12 case_8813          │
│  ▓▓▓▓▓░░░░░ ISSUER_HOLD   52% │    RETRY_SCHEDULED → 10:00   │
│  ░░░░░░░░░░ CREDENTIAL_DEAD 0%│  10:04:09 case_8812          │
│  ▓▓▓░░░░░░░ AUTH_ABANDON  31% │    ⊘ BLOCKED  G3_AFA         │
│                               │  10:04:07 case_8811          │
│  [attempts saved vs baseline] │    NUDGE hi-IN → 11:00       │
└───────────────────────────────┴──────────────────────────────┘
```

The `BLOCKED BY COMPLIANCE` counter is deliberately given equal weight to `RECOVERED`. That framing — restraint as a first-class outcome — is the whole thesis of the product.

**S2 — Case View** *(the signature screen)*

Header: customer ref (hashed), ₹ amount, mandate type, category, subscription state, current attempt count vs budget.

Then the **Decision Tape**:

```
  ─── DECISION TAPE ── case_8812 ── ₹18,000 ── card mandate ────────

  23:14  EVENT      payment.failed · insufficient_funds · customer
    │
  23:14  NORMALISE  → TEMPORARY_LIQUIDITY                    [det]
    │
  23:14  DIAGNOSE   recoverable · conf 0.82 · hint PAYDAY    [llm]
    │                evidence: attempt 1 · prior 6 charges clean
    │
  23:14  POLICY     POL-004 → RETRY_SCHEDULED
    │
  23:14  GATES
    │    ├ G0_KILL       ✓
    │    ├ G1_WINDOW     ⊘ BLOCKED   ╱RBI FPC · 08:00–19:00╱
    │    ├ G3_AFA        ⊘ BLOCKED   ╱₹18,000 > ₹15,000 · AFA reqd╱
    │    ├ G2_PREDEBIT   ⊘ BLOCKED   ╱no notice ≥24h · E-mandate 2026╱
    │    └ verdict       REPLAN
    │
  23:15  EXECUTE    A3 SEND_PRE_DEBIT_NOTICE  → queued 08:00
         EXECUTE    A5 CREATE_AFA_LINK        → plink_R4mK…
         SCHEDULE   A2 RETRY_SCHEDULED        → 01 Sep, 10:00 IST

         suppressed: A1 RETRY_NOW (G1), A2 @23:14 (G2, G3)
  ──────────────────────────────────────────────────────────────────
```

Each chit is clickable → drawer with the full decision document, the rule's `because` text, and the citation. **The struck-through blocked chits with overprinted clauses are the screenshot that goes in the README.**

**S3 — Batch Evidence**

A/B/C comparison table with CIs. Recovery-curve chart over the 14-day window, three lines. Below it, the **exception list** — every case Salvage refused, with reason code, rendered plainly and not hidden behind a tab. Below that, the sensitivity chart from F22.

**S4 — Rulebook** *(P1)*

The policy YAML and the gate chain, rendered human-readable. Each rule shows id, condition, action, `because`, and citation. A judge should be able to read the entire decision logic of the system in ninety seconds without opening the repo.

**S5 — Exception Desk** *(P1)*

Queue of `ESCALATE_HUMAN` and `UNKNOWN` cases with the context a human operator would need. Demonstrates that the agent knows its own edges.

### 6.4 Frontend stack

React + Vite + TypeScript. Tailwind with the tokens above defined as CSS custom properties. Recharts for the two charts. `EventSource` (SSE) from `GET /stream/decisions` for the live feed — SSE not websockets, less to go wrong on demo day. No component library; the visual identity is the point and shadcn defaults would erase it.

**Quality floor, unannounced:** responsive to 375px, visible keyboard focus, reduced-motion respected, empty states that tell you what to do next rather than saying "no data."

### 6.5 Copy rules

Active voice. The button says what happens. `Arm` produces a banner that says `Armed`. Errors state what happened and what to do, in the interface's voice — never apologise, never vague. Amounts always `₹` with Indian grouping (`₹9,17,650`). Timestamps always IST with the zone shown, because half the compliance logic is about clocks.

---

## 7. API contract

```
POST   /webhooks/razorpay           ingest (HMAC verified)
GET    /cases?state=&class=         list
GET    /cases/:id                   case + full decision tape
GET    /cases/:id/audit             raw audit documents
GET    /stream/decisions            SSE live feed
GET    /metrics/batch/:run_id       A/B/C metrics + CIs
GET    /exceptions                  exception desk
GET    /rulebook                    policies + gates, rendered
POST   /control/arm                 dry_run → live (requires confirm token)
POST   /control/kill                engage kill switch
POST   /sim/run                     start a batch run
GET    /sim/run/:id/status          progress
```

---

## 8. Repo structure

```
salvage/
├── README.md                  ← architecture, results, screenshots, how to run
├── PRD.md                     ← requirements + MongoDB data model (authority)
├── SALVAGE_SPEC.md            ← this file
├── CLAUDE.md                  ← repo invariants for Claude Code
├── ARCHITECTURE.md            ← the diagram + why each boundary exists
├── LIMITATIONS.md             ← written by you, brutally honest
├── COMPLIANCE.md              ← gate ↔ clause mapping table with sources
├── NOTES.md                   ← running log of surprises and bugs found
├── api/
│   ├── db/                    collections, repositories, chain helpers
│   ├── ingest/                webhook, hmac, dedupe
│   ├── normalise/             taxonomy lookup + tests
│   ├── diagnose/              llm client, schemas, repair loop
│   ├── policy/                engine + rules/*.yaml
│   ├── gates/                 g0..g10, each its own module + test
│   ├── executor/              client.py — the ONLY razorpay caller
│   ├── scheduler/
│   └── audit/
├── taxonomy/failure_classes.yaml
├── sim/                       simulator, calibration.yaml, CALIBRATION.md
├── eval/                      harness, costs.yaml, results.json, charts
├── web/                       React frontend
├── tests/                     unit + the named failure tests below
└── scripts/
    ├── seed_razorpay.py       creates plans/subs/mandates in test mode
    ├── check_boundaries.py    razorpay SDK confined to executor
    ├── check_clauses.py       every gate cites, every rule explains
    ├── verify_chain.py        hash chains intact
    └── verify.sh              the gate
```

---

## 9. The failure tests (name these in the video)

Track 03 and the program both explicitly ask what broke. Build these deliberately so you have real answers:

1. **Duplicate webhook** — same `rz_event_id` replayed → unique-index dedupe prevents a double charge. Demo this live.
2. **Out-of-order delivery** — `subscription.halted` arrives before `payment.failed` → state machine handles it, doesn't crash.
3. **Malformed LLM output** — force an out-of-enum `failure_class` → repair retry → `UNKNOWN` → exception list, not a bad money action.
4. **Scheduler drift** — a retry scheduled for 10:00 fires at 19:30 → `G1_WINDOW` re-evaluates at fire time and blocks it.
5. **Razorpay 5xx** — retry with the same idempotency key per Razorpay's documented 5xx guidance; no duplicate charge.
6. **Clock/timezone** — server in UTC, gates in IST. Write the test that would have caught the bug.

Pick #1 and #4 for the video. #4 is the more sophisticated one and almost nobody will have it.

---

## 10. Build order

| Day | Ship | Done when |
|---|---|---|
| 0 | Repo scaffolding, MongoDB layer, verification harness, agents, skills | `make verify` runs and fails honestly |
| 1 | Razorpay test setup, plans/subs, ngrok, capture real payloads | Real JSON committed to `tests/fixtures/` |
| 2 | Ingest: HMAC, dedupe, hash chain | Duplicate-replay test passes; chain verifies |
| 3 | Taxonomy YAML + normaliser + full unit tests | 100% branch coverage; unmapped count reported |
| 4 | Policy engine + rules | First-match-wins tested; all rules cite |
| 5 | **Compliance gates G0–G10 + clause refs** | Every gate tested incl. fire-time re-eval |
| 6 | Executor, dry-run, kill switch, scheduler, audit writer | Boundary check passes |
| 7 | Simulator + calibration.yaml + CALIBRATION.md | Sensitivity run produces a chart |
| 8 | Eval harness, run A/B/C × 20 seeds, commit results **as produced** | `eval/results.json` committed with CIs |
| 9 | Frontend S1 + S2 (Command Deck + Decision Tape) | The blocked-chit screenshot exists |
| 10 | S3, README, ARCHITECTURE.md, LIMITATIONS.md, 5-min video | Submitted |

**Slip rule:** if you're behind on Day 8, cut S3 and P1 features. Never cut Day 5 or Day 8. The gates and the honest numbers *are* the submission.

---

## 11. Video beat sheet (5:00)

| Time | Beat |
|---|---|
| 0:00–0:25 | The gap. Razorpay Optimizer routes *before* the attempt. After it fails, the default is four blind retries then halt. Show the T+1/T+2/T+3 doc line. |
| 0:25–0:50 | Thesis: "The LLM diagnoses. It never decides to move money." Show the architecture diagram, point at the deterministic boundary. |
| 0:50–2:00 | **Live case walkthrough.** ₹18,000, insufficient funds, 23:14. Three gates fire. Replan. Show the Decision Tape with the overprinted clauses. |
| 2:00–2:40 | The doc-grounded lever: manual invoice charge doesn't consume the subscription's retry budget, so Salvage adds precisely-timed attempts without spending the safety net. |
| 2:40–3:40 | **Batch results.** A/B/C table with CIs. Net recovery, not gross. Zero compliance violations. Wasted-attempts-on-hard-declines comparison. Sensitivity chart. |
| 3:40–4:20 | **What broke.** Duplicate webhook replay, live. Scheduler drift → fire-time gate re-eval. Then `verify_chain.py` — tamper-evident audit trail. |
| 4:20–4:45 | Exception desk. What the agent refused to handle and why. |
| 4:45–5:00 | Limitations, verbatim from LIMITATIONS.md. Kill switch. End. |

Volunteering weaknesses before the panel finds them is the highest-leverage move available in the defence round. Budget real time for it.

---

## 12. Known limitations to write up honestly

- Batch outcomes come from a simulator, not from real issuer behaviour. Calibration sources are documented; sensitivity analysis included; the ranking is robust to ±20% but the absolute ₹ figure is not a prediction.
- Payday inference is a heuristic on prior successful charge dates, not real salary data.
- Manual charging of domestic cards is not supported in Razorpay, so that recovery path is Payment-Link-mediated rather than silent.
- Test mode caps (3-day token validity, 30 payment links) constrain how much of the batch could run against the live API.
- The audit hash chain is tamper-*evident*, not tamper-*proof* — it detects modification, it does not prevent it. A production system would need append-only storage or a WORM bucket.
- Compliance gates encode our reading of the E-mandate Framework 2026 and the Fair Practices Code. They are engineering controls, not legal advice, and a production deployment needs counsel review.
- The LLM's marginal contribution is measured in arm C and reported as measured, whatever the number is.

---

## 13. Sources

**Razorpay:** subscription payment retries (T+1/T+2/T+3, halted state); test subscriptions (manual charge does not consume retries, 3-day test token validity, test-mode charge trigger); error codes and the downloadable payment error reasons list; Payment Links API (`expire_by`, `reminder_enable`, `notify`, `reference_id`, 30-link test cap, reminder dispatch windows); webhooks (raw-body signature verification, port 80/443); Optimizer dynamic routing (20-min SR-triggered gateway downtime); Razorpay's own smart-routing paper.

**Regulatory:** RBI Digital Payments — E-mandate Framework, 2026 (notified 21 April 2026): AFA on registration, ₹15,000 no-AFA cap with ₹1,00,000 for insurance/MF/credit-card bills, 24-hour pre-transaction notification with opt-out, post-transaction notification with grievance redressal, no customer charges, acquirer responsibility for merchant compliance. RBI Fair Practices Code / recovery agent conduct: 08:00–19:00 contact window across all channels including automated messaging, no third-party contact.

**Industry practice:** decline-code-routed retry categories; payday-aligned retry timing (dom 1 / 15 / month-start; refusal peaks pre-payday); ~2% lower nighttime success; expired/reissued cards at 10–15% of recurring failures and non-recoverable by retry; 4–8 attempts over 15–30 days as the practical ceiling; card-network retry limits and the soft-to-hard decline conversion risk.

*Put full URLs in the repo README. Cite them in the video's on-screen text.*
