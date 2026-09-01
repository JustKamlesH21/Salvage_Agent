# Salvage — Product Requirements Document

**v1.2 · MongoDB edition**
Razorpay AI Buildathon 2026 · Track 03 — AI Revenue Recovery

> Companion documents: `SALVAGE_SPEC.md` (architecture + research grounding), `CLAUDE.md` (repo invariants).
> **Changed in v1.2 (day 0-1):** `audit` uniqueness moved to `(idempotency_key, phase)`; the hash chain is partitioned by `run_id`; `run_id` added to the `events` and `audit` shapes. All three are explained in §8.3.

---

## 1. Problem

Razorpay Optimizer solves the **pre**-transaction problem: route each attempt to the gateway most likely to succeed. After an attempt fails, the platform default is a fixed timer — charge fails at T=0, subscription moves to `pending`, auto-retries fire on T+1, T+2, T+3, and if all fail the subscription moves to `halted`.

Four blind attempts, same schedule, same time of day, regardless of cause. Industry evidence says this is the wrong shape: insufficient-funds declines clear on payday rather than day 3, expired and reissued cards never recover on retry at all, and processor timeouts want an immediate attempt through a different path.

## 2. Product

**Salvage** sits between `payment.failed` and `subscription.halted`. It diagnoses why a recurring charge failed, chooses the right intervention and the right moment, forces every money action through hard RBI compliance gates, executes it on Razorpay test-mode APIs, and measures recovery against a baseline.

**Thesis, stated in one line:** *the LLM diagnoses; it never decides to move money.*

## 3. Users

| User | Need |
|---|---|
| Merchant finance ops | Recover involuntary churn without a manual dunning desk |
| Compliance / risk | Prove no automated action ever breached RBI rules |
| Buildathon panel | See bounded money actions, an audit trail, honest metrics, and one failure handled well |

## 4. Success criteria

The submission succeeds if all five hold:

1. Net ₹ recovered beats baseline on a 500-case cohort, reported with 95% CIs across 20 seeds.
2. Compliance violations across the entire batch: **exactly zero**.
3. Every money action traces to a policy rule id, a gate verdict, and a clause reference.
4. Two failure modes are demonstrated live, not described.
5. `LIMITATIONS.md` is written by us and is harsher than anything a judge would ask.

## 5. Scope

**In:** failed recurring charges on Razorpay Subscriptions (card and UPI Autopay mandates), from first `payment.failed` to recovery or terminal close.

**Out:** checkout abandonment, B2B receivables, voice, chargebacks, multi-gateway routing. Stated explicitly in the README.

## 6. Functional requirements

Priority levels are hard. No P1 work begins until every P0 has a passing test.

### P0

| ID | Requirement | Acceptance |
|---|---|---|
| FR-01 | Ingest Razorpay webhooks with HMAC verification on the **raw body** | Tampered payload rejected; test proves body is not parsed before verification |
| FR-02 | Idempotent event handling | Same `rz_event_id` replayed 100× produces one case and one set of actions |
| FR-03 | Immutable, hash-chained event and audit log | Chain verification script passes; any mutation detected |
| FR-04 | Deterministic failure normaliser | Every reason in Razorpay's published list maps to a `FailureClass` or explicitly to `UNKNOWN`; 100% branch coverage |
| FR-05 | LLM diagnosis with schema-constrained output | Invalid output → one repair attempt → `UNKNOWN` → exception. Never a money action |
| FR-06 | Deterministic policy engine, priority-ordered | First match wins; every rule carries `because` and `cites` |
| FR-07 | Compliance gate chain G0–G10, non-bypassable | Each gate independently tested; gates re-evaluate at fire time, not only at schedule time |
| FR-08 | Bounded action executor as sole Razorpay caller | Architectural check fails the build if any other module imports the client |
| FR-09 | Dry-run default, explicit arm step | Nothing reaches Razorpay unless armed with a confirm token |
| FR-10 | Global kill switch | Engaging it freezes all pending and scheduled actions within one scheduler tick |
| FR-11 | Scheduler with fire-time gate re-check | Test: action scheduled 10:00, fired 19:30, blocked by G1 |
| FR-12 | Outcome simulator with documented calibration | Every parameter has a `source` field; sensitivity ±20% does not flip the A/B ranking |
| FR-13 | Eval harness, arms A/B/C, 20 seeds | `eval/results.json` committed with means and CIs |
| FR-14 | Audit API exposing full decision tape | Every decision returns ordered gate verdicts including passes and suppressed alternatives |
| FR-15 | Command Deck screen | Money counters, per-class recovery, live decision feed |
| FR-16 | Case View with Decision Tape | Blocked gates render struck through with overprinted clause |
| FR-17 | Batch Evidence screen | A/B/C table with CIs, recovery curves, exception list, sensitivity chart |
| FR-18 | Exception list surfaced, not hidden | Reachable in one click from the deck |

### P1

FR-19 Rulebook screen · FR-20 live Razorpay execution toggle · FR-21 payday inference from prior successful charge dates · FR-22 sensitivity analysis chart · FR-23 opt-out landing page · FR-24 Hinglish nudge templates.

FR-28 Reconciliation sweep — periodic poll of subscription state for drift (status, `paid_count`) no webhook explained; opens a case from the discrepancy. Found necessary on day 1: a mandate-authentication attempt above an undocumented Razorpay test-mode amount ceiling is rejected before any payment entity exists, so it fires **no webhook at all** — not `payment.failed`, nothing. Salvage is entirely webhook-driven, so this class of failure is otherwise invisible to it: money can leak with zero audit trail, in exactly the system whose thesis is an auditable record of every money-relevant event. Shares machinery with the in-doubt reconciliation path in the executor phase (§11 day 6) rather than being a separate subsystem. Logged, not built, for this submission — but named explicitly in `LIMITATIONS.md` rather than left implicit.

### P2 (only if Day 8 ends clean)

FR-25 bandit policy tuning · FR-26 cohort recovery-drift diagnostic · FR-27 card-updater simulation.

## 7. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-01 | Decision latency p95 under 2s excluding LLM call; LLM call capped at 8s with timeout fallback to deterministic path |
| NFR-02 | Batch of 500 cases × 14 simulated days completes in under 3 minutes |
| NFR-03 | No PII in LLM prompts — no name, contact, email, or amount |
| NFR-04 | All timestamps stored UTC, all gate logic evaluated in IST, zone always displayed |
| NFR-05 | Frontend responsive to 375px, visible keyboard focus, `prefers-reduced-motion` respected |
| NFR-06 | Secrets from environment only; `.env.example` committed, `.env` never |

## 8. Data model — MongoDB

MongoDB Atlas free tier (M0). Motor (async) for the API, PyMongo for scripts and the eval harness. Atlas M0 is a replica set, so multi-document transactions are available where needed — but the design deliberately avoids needing them.

### 8.1 Why this shape fits

Decisions are naturally document-shaped: one decision embeds its LLM diagnosis, its policy match, and its ordered array of gate verdicts. That is a single atomic write with no joins. Idempotency and immutability are enforced with unique indexes and a hash chain rather than transactions.

**Redis is dropped.** A unique index on `audit.(idempotency_key, phase)` does the executor's dedupe job and a unique index on `events.(rz_event_id, signature_valid)` does the webhook's — see §8.3 for why each is compound rather than on the single field alone. One less moving part.

### 8.2 Collections

```js
// events — append-only, raw webhook capture
{
  _id, rz_event_id,            // UNIQUE with signature_valid — the idempotency guard, §8.3
  run_id,                       // null for live; set for eval batches. v1.2 —
                                // partitions the hash chain, see §8.3
  event_type, payload,          // raw, unparsed-then-verified body
  signature_valid: Boolean,
  received_at, processing_state, // "received", always — see note below
  prev_hash, hash                // chain, scoped to run_id
}
```

**`processing_state` — day 2 correction.** The four values originally listed
here (`received | processed | failed | duplicate`) are not all storable on an
append-only, insert-only collection: `processed`/`failed` describe outcomes
that are only knowable after downstream work in later phases, which would
require mutating a row already written, and `events` exposes `insert` only
(§8.4, invariant 6). Every successful insert now stores `"received"` and
nothing else; a later phase that needs to record processing outcome does so
by referencing `rz_event_id` from its own collection, not by updating this
row. `duplicate` is never a stored value either — a genuine duplicate (same
`rz_event_id` replayed) produces no new document at all, so it is a response
classification the ingest endpoint returns, not a document state. See
`api/ingest/webhook.py`'s module docstring for the implementation and
NOTES.md's 2026-09-01 day 2 entry for how the gap was found.

**An invalid signature is stored, not rejected before insert.** `signature_valid: Boolean`
is a field on every document, not only on valid ones: a forged or
misconfigured delivery is itself forensic data in a tamper-evident audit
trail, and rejecting it pre-insert would mean the append-only log has no
record that the delivery ever arrived. Only a valid signature is eligible
for anything past storage — no case, no decision, no downstream read of
`events` may act on a row with `signature_valid: false`.
`EventRepository.by_rz_event_id()` enforces this at the read layer by
filtering to `signature_valid: true`. This is the reason `rz_event_id`'s
uniqueness (§8.3) has to be compound with `signature_valid` rather than
global: two rows, one invalid and one valid, are a normal and expected
shape for one event id, not a bug. Found underspecified at day-2 phase
close (spec-guardian): this was previously only inferable from the field's
presence, not stated as a requirement.

```js

// cases — one per recovery episode; attempts embedded (bounded ≤ 12)
{
  _id, run_id,                   // null for live, set for eval batches
  subscription_id, customer_id, invoice_id,
  amount_paise, currency, mandate_type,   // card | upi_autopay
  category,                                // standard | insurance | mf | cc_bill
                                            // sourced from the Razorpay plan's
                                            // notes.category at ingest time —
                                            // Razorpay has no native concept of
                                            // it; it is ours (day 1, seed_razorpay.py)
  state,                                   // open | recovering | recovered | terminal | exception
  opened_at, closed_at, outcome, recovered_paise,
  attempts: [{
    attempt_no, source,                    // rz_auto | salvage_manual
    attempted_at, result, failure_class, raw_reason
  }],
  salvage_attempt_count,                   // drives G4_BUDGET
  last_contact_at, contact_count_7d        // denormalised for G5_COOLDOWN
}

// decisions — one per decision cycle, fat and self-contained
{
  _id, case_id, run_id, created_at,
  diagnosis: { failure_class, confidence, is_recoverable,
               evidence: [String], timing_hint, source },  // llm | deterministic_override
  llm_raw,                                  // verbatim, even when rejected
  policy: { rule_id, proposed_action, proposed_at },
  gate_results: [{ gate, verdict, clause_ref, detail, evaluated_at }],
  final: { action, at, verdict },           // allow | block | replan
  suppressed: [{ action, blocked_by, clause_ref }]
}

// audit — append-only, hash-chained, the only record of money actions
{
  _id, case_id, decision_id, ts, actor,     // system | scheduler | human
  run_id,                                    // null for live; set for eval batches
  phase,                                     // pre | post
  action, idempotency_key,                   // UNIQUE with phase — see §8.3
  request, response, dry_run: Boolean,
  clause_ref, note,
  prev_hash, hash                            // chain, scoped to run_id
}

// customers
{ _id: customer_id, contact_hash, email_hash,
  inferred_payday_dom, opt_out_at, tz }

// exceptions
{ _id, case_id, reason_code, detail, ts, resolved_by }

// runs — eval batch metadata and computed metrics
{ _id, arm, seed, cohort_config, started_at, finished_at, metrics }
```

### 8.3 Indexes

```js
events:    { rz_event_id: 1, signature_valid: 1 } UNIQUE   // day 2 — see below
           { received_at: -1 }
           { run_id: 1, prev_hash: 1 } UNIQUE   // v1.2 — see below
           { run_id: 1, _id: -1 }               // v1.2 — chain head lookup
cases:     { state: 1, opened_at: -1 }
           { subscription_id: 1 }
           { run_id: 1, state: 1 }
decisions: { case_id: 1, created_at: 1 }
           { run_id: 1 }
audit:     { idempotency_key: 1, phase: 1 } UNIQUE   // v1.2 — was { idempotency_key: 1 }
           { case_id: 1, ts: 1 }
           { run_id: 1, prev_hash: 1 } UNIQUE   // v1.2 — see below
           { run_id: 1, _id: -1 }               // v1.2 — chain head lookup
customers: { _id: 1 }   // customer_id as _id; MongoDB creates this implicitly
exceptions:{ case_id: 1 }
```

**v1.2 changes, made on day 0 while building the data layer.** Both were found
by writing the repository tests before the executor existed.

1. **`audit` uniqueness moved from `idempotency_key` to `(idempotency_key, phase)`.**
   SPEC §5.6 writes a `pre` row *and* a `post` row carrying the same
   idempotency key. Under a unique index on the key alone the post row is
   unwritable, so the specified executor could not have run. The dedupe
   property invariant 9 depends on is unchanged: a second `pre` insert for the
   same key still raises `DuplicateKeyError`, which still means *already
   executed, return the prior result, never re-dispatch*.

   Consequence for the executor: `prior_result(key)` must distinguish "post row
   exists" from "pre row exists but post row does not". The second case means
   the dispatch is in flight or died mid-flight, and must not be read as "never
   attempted".

2. **`(run_id, prev_hash)` is UNIQUE on both chained collections, and the hash
   chain is partitioned per run.** Two writers reading the same chain head
   would both compute the same `prev_hash` and fork the chain silently. With
   the index, the second insert fails with `DuplicateKeyError` and the
   repository retries against the new head — the same *unique index instead of
   a lock* move §8.1 already makes for idempotency, turning an invisible
   integrity failure into a loud one.

   The index is compound on `run_id` rather than global on `prev_hash` because
   §10 runs twenty seeds across three arms. Sixty runs appending to a single
   chain would serialise every audit write in the batch behind one contended
   head, against NFR-02's three-minute budget. One chain per run makes those
   appends independent. `run_id` is `null` for the live chain, and
   `scripts/verify_chain.py` walks and reports every partition separately — a
   break in one run's chain does not invalidate the others.

   `run_id` is therefore added to the `events` and `audit` shapes in §8.2, and
   it is part of the hashed body: the chain commits to which run a row belongs
   to, so a row cannot be moved between partitions undetected.

3. **The first document's `prev_hash` is an explicit sentinel — 64 zeros, never
   `null`.** MongoDB indexes a missing field and an explicit `null`
   identically, so under the compound index a `null` genesis would be
   indistinguishable from a document whose `prev_hash` was never written: a
   malformed row would present as the head of a chain. This was already how
   `api/db/chain.py` was written; it is recorded here because partitioning is
   what makes it load-bearing rather than merely tidy.

4. **A chained append that exhausts its retries is filed, never dropped.** The
   repository retries the head race with full-jitter backoff — deterministic
   backoff makes two colliding writers re-collide on the same schedule — and on
   exhaustion writes a `CHAIN_APPEND_EXHAUSTED` row to `exceptions` carrying
   the undelivered document, *then* raises `ChainAppendExhaustedError`. The
   caller never sees an exhausted append as a successful one. A silently
   dropped audit row is worse than a failed action: the action leaves a trace
   elsewhere, the missing row leaves none.

**Day 2 fix — `events.rz_event_id` uniqueness made compound with
`signature_valid`.** A single index unique across every row meant a forged
delivery (stored as forensic data — §8.2's "invalid signature is stored, not
rejected" note above) permanently occupied its
`rz_event_id`: after a webhook secret rotation, Razorpay's legitimately-signed
retry of that same event would collide on the same key, get classified
`duplicate`, and never be recorded as valid. `(rz_event_id, signature_valid)`
UNIQUE lets one invalid row and one valid row coexist per event id —
intentionally: `EventRepository.by_rz_event_id()` is scoped to
`signature_valid: true` so the forgery is never the row returned. A second
identical forgery at the same id still collides on the same
`(id, false)` pair, but `rz_event_id` comes from the unauthenticated
`X-Razorpay-Event-Id` header, so this bounds a *replay at one id*, not the
number of ids an attacker can create — an idempotency property, not a rate
limit on growth. (An earlier version of this fix used two partial unique
indexes instead of a compound one; `compliance-auditor` found that shape left
documents with a missing/non-boolean `signature_valid` with no uniqueness
constraint at all, and broke index usage for an unqualified
`find({rz_event_id})` on real MongoDB. The compound index has neither
problem.) Found and fixed on the day-2 ingest diff; see NOTES.md's day 2
entries and `api/db/collections.py`'s module docstring.

**The legacy-index migration mechanism.** Replacing an index name does not
remove the old one from a database that already ran an earlier startup —
`create_index`/`create_indexes` only ever add. `api/db/collections.py`
tracks every index name this project has ever declared and since superseded
in `_LEGACY_INDEXES` (append-only — an entry is never removed, since an
already-deployed database may still carry it), and `apply_indexes()`
creates the current shape *before* dropping any legacy name, not after:
dropping first would leave a window with no uniqueness constraint at all,
during which a genuine duplicate could land and make every future create
fail permanently. Create-first is safe only when the legacy index is at
least as strong as its replacement — true for every shape this project has
declared so far, verified against real MongoDB, not assumed — and if that
precondition is ever violated by a future migration, the safe outcome is
exactly what happens: the create fails, the app does not start, and the
legacy guard is left fully intact rather than silently lost. An import-time
check rejects any future index rename that reuses a name already listed in
`_LEGACY_INDEXES`, since create-before-drop would otherwise create and
immediately destroy it on every startup. `tests/db_history.py` is the
permanent registry of historical index shapes this mechanism is tested
against; add an entry there whenever an index is renamed, dropped, or
reshaped. Found and fixed across four rounds of `compliance-auditor` review
on the day-2 diff; see NOTES.md's day-2 entries for the full account.

**How a duplicate-key error of unknown shape is classified.** `_is_chain_race()`
(`api/db/repositories.py`) decides whether a `DuplicateKeyError` on a
chained collection is a concurrent-writer race on the hash chain (retried)
or a genuine business-key duplicate (surfaced immediately, unretried). When
the error carries no structured `keyPattern` at all — a shape neither real
MongoDB nor mongomock actually produces, but not provably impossible —
it defaults to treating it as a *possible* chain race rather than a
confirmed duplicate. A wrong retry costs a few attempts and a visible
exception-desk row on exhaustion; a wrong "confirmed duplicate" tells
Razorpay to stand down (200) for an event that may never have been stored —
silent, unrecoverable loss. Between a loud wrong answer and a quiet one,
this project fails toward the loud one, always. The same rule governs the
`DuplicateKeyError` branch in `api/ingest/webhook.py`: a duplicate-key
collision on an *invalid-signature* delivery answers 401, not the 200 a
plain duplicate would get, for the identical reason — see
`tests/ingest/test_webhook_ingest.py::TestInvalidSignature` for the
regression.

### 8.3.1 The third audit phase

`audit.phase` is `pre | post` in §8.2, uniqued with `idempotency_key` in §8.3.
A gate BLOCK also writes an audit document (SPEC §5.5: "every BLOCK writes an
audit document with the clause reference") — decided here, on day 1, rather
than left for day 6 to discover the collision:

**`phase` gets a third value, `block`.** A BLOCK is not a pre-dispatch
intention and not a post-dispatch result; it is neither, and forcing it into
`pre` would collide with a genuine pre-row for the same `idempotency_key` if
the action is later allowed at re-evaluation and actually dispatched — exactly
the case invariant 9 exists to prevent. A `block` row is terminal for that
decision cycle: nothing dispatches, so nothing follows it under the same key.

Re-evaluation after a BLOCK computes a **new** idempotency key
(`case.id:decision.id:action.id`, SPEC §5.6 — a new `decision.id` per
evaluation), so a later ALLOW never collides with the earlier BLOCK. The
`(idempotency_key, phase)` unique index holds without change.

### 8.4 Immutability

MongoDB will not enforce append-only, so we do it in two layers:

1. **Application layer** — `events` and `audit` are written through repository functions that expose `insert` only. No update or delete method exists on those repositories. An architectural check greps for `update_one`/`delete_one` against those collections and fails the build.
2. **Hash chain** — every `events` and `audit` document carries `prev_hash` and `hash = sha256(prev_hash + canonical_json(body_without_hash))`. `scripts/verify_chain.py` walks both chains and reports any break.

The hash chain is not security theatre here. It is the thing that lets you say on camera: *"this audit trail is tamper-evident, and here is the command that proves it."* Run it in the video.

### 8.5 Concurrency

Case state transitions use `find_one_and_update` with a state predicate — optimistic, single-document atomic, no transaction needed:

```python
db.cases.find_one_and_update(
    {"_id": case_id, "state": "open"},
    {"$set": {"state": "recovering"}},
    return_document=ReturnDocument.AFTER,
)
```

A `None` return means someone else moved it. Handle that path; do not assume it cannot happen.

Executor idempotency is insert-first: attempt the `audit` pre-row insert with the idempotency key, and treat `DuplicateKeyError` as "already executed, return the prior result." This makes double-execution structurally impossible rather than checked-then-raced.

## 9. Compliance gates

The product's defining feature. Full clause mapping lives in `COMPLIANCE.md`; summary:

| Gate | Rule | Authority |
|---|---|---|
| G0_KILL | Kill switch engaged → block all | Ops |
| G1_WINDOW | Customer contact only 08:00–19:00 IST, every channel including automated messaging | RBI Fair Practices Code |
| G2_PREDEBIT | No auto-debit without a pre-transaction notice ≥24h prior carrying merchant name, amount, debit date/time, mandate reference, opt-out | E-mandate Framework 2026 |
| G3_AFA | No-AFA auto-debit ≤ ₹15,000; ≤ ₹1,00,000 for insurance premiums, mutual funds, credit-card bills. Above → AFA link, never a silent retry | E-mandate Framework 2026 |
| G4_BUDGET | Max 4 Salvage attempts per case per 14 days, on top of Razorpay's own three | Card network limits; soft→hard decline conversion risk |
| G5_COOLDOWN | ≥48h between contacts, ≤3 contacts per 7 days | Anti-harassment |
| G6_OPTOUT | Opt-out permanent and immediate | E-mandate Framework 2026 |
| G7_THIRDPARTY | Never contact employer, relatives, neighbours | RBI FPC |
| G8_POSTDEBIT | Post-transaction confirmation with grievance-redressal details after every successful collection | E-mandate Framework 2026 |
| G9_HALTED | No auto-charge on a halted subscription | Razorpay state machine |
| G10_IDEMPOTENT | Refuse any action whose idempotency key exists | Correctness |

Every gate module exports `CLAUSE_REF` and `RULE_TEXT`. The build fails if either is missing.

## 10. Metrics

Reported per arm, mean ± 95% CI over 20 seeds:

₹ recovered · recovery rate · **net ₹ recovered** after costs · attempts per successful recovery · contacts per customer · median time-to-recovery · **compliance violations (must be 0)** · actions blocked by gate, broken out by clause · exception rate · wasted attempts on hard declines · decision latency p50/p95.

Net rather than gross is the honesty signal. Cost assumptions live in `eval/costs.yaml` with sources.

## 11. Release plan

| Day | Milestone | Gate to advance |
|---|---|---|
| 1 | Razorpay test setup, real webhook fixtures captured | Fixtures committed |
| 2 | Ingest: HMAC, idempotency, hash chain | Replay test passes; chain verifies |
| 3 | Taxonomy + normaliser | 100% branch coverage |
| 4 | Policy engine | First-match-wins tested; all rules cite |
| 5 | **Gates G0–G10** | Each gate tested incl. fire-time re-eval |
| 6 | Executor, dry-run, kill switch, scheduler | Boundary check passes |
| 7 | Simulator + calibration + sensitivity | Chart produced |
| 8 | **Eval harness, A/B/C run** | `results.json` committed as-run |
| 9 | Frontend S1 + S2 | Blocked-chit screenshot exists |
| 10 | S3, docs, video | Submitted |

**Slip rule:** cut P1 and screen S3 before cutting Day 5 or Day 8. The gates and the honest numbers are the submission.

## 12. Risks

| Risk | Mitigation |
|---|---|
| Test-mode card tokens expire in 3 days | Live API used only for the ~8 demo cases; batch runs on simulator |
| 30 Payment Links cap in test mode | Never generate links inside a batch run |
| Manual charge of domestic cards unsupported | That path routes via Payment Link; documented in LIMITATIONS.md |
| Simulator seen as self-serving | Every parameter sourced; sensitivity analysis published; net not gross |
| LLM adds no measurable value | Arm C measures it; report whatever the number is |
| Scope creep into other loss classes | Out-of-scope list is in the README and is final |
