# Architecture

Four boundaries do the real work here. Everything else is plumbing. Each one
exists because a specific thing goes wrong without it, and each one is checked
mechanically rather than by convention — `make verify` fails if any is broken.

```
  Razorpay ──webhook──▶ INGEST ──────────────── HMAC on the RAW body
                          │                     hash-chained, append-only
                          │                     dedupe on rz_event_id
                          ▼
                     NORMALISE ───────────────── deterministic, 100% branch cov
                          │                      114 published reasons → 9 classes
                          ▼
                     DIAGNOSE ────────────────── ◀── THE ONLY LLM
                          │                      returns a diagnosis, never an action
                          │                      no name, contact, email or amount
                          ▼                      normaliser overrides it when confident
                      POLICY ─────────────────── priority-ordered YAML
                          │                      every rule cites its reason
                          ▼
                  ┌── GATES G0…G10 ───────────── ◀── NON-BYPASSABLE
                  │       │                      all eleven run, always, in order
                  │       │                      G0_KILL first, no exceptions
                  │       ▼                      re-evaluated at FIRE time
                  │   remediation ──┐            a substitute action is re-gated
                  │       │◀────────┘            bounded; exhaustion dispatches nothing
                  │       ▼
                  └──▶ EXECUTOR ───────────────── ◀── THE ONLY DOOR
                          │                      sole Razorpay SDK importer
                          │                      insert-first idempotency
                          ▼                      dry-run default + arm token
                    AUDIT (append-only, hash-chained)
                          │
                          ▼
              DECISION TAPE ──▶ web        EVAL ──▶ results.json
```

## The four boundaries

**The Razorpay SDK is imported in exactly one file.**
`api/executor/client.py`, and nowhere else in the repository.
`scripts/check_boundaries.py` AST-walks `api/`, `scripts/`, `sim/`, `eval/` and
`tests/` and fails the build otherwise. This is the architectural answer to
*"how do we know your agent can't go rogue"*: there is one door, and the build
breaks if someone cuts a second one.

There are **no exemptions**, including for setup scripts.
`scripts/seed_razorpay.py` reaches Razorpay through
`api.executor.client.get_razorpay_client()` like everything else. It briefly had
one — day 0 shipped with `scripts/` outside the scan — and `make verify` stayed
green throughout, because the exemption lived inside the enforcement mechanism.
`test_every_top_level_python_directory_is_scanned` now derives the scanned set
from the repository rather than trusting a hand-maintained list, so a directory
added later cannot fall outside it. See NOTES.md.

Two boundaries, not one. `client.py` owns the SDK import; `execute_action()`
owns the bounded A1-A10 action catalog. Setup work — creating plans, customers,
subscriptions — passes through the first and never the second. A bounded list
that grows to hold setup verbs has stopped being bounded.

**`events` and `audit` are append-only, in two layers.**
Their repositories expose `insert` and reads; no update, delete or replace
method exists on them, and `check_boundaries.py` fails the build if one is
added or if a mutating driver call targets either collection. On top of that,
every document carries `prev_hash` and `hash`, so a modification made outside
the application is still detectable — `scripts/verify_chain.py` walks both
chains and distinguishes a MISMATCH (a document was edited) from an ORPHAN (one
was deleted) from a FORK (two writers appended to the same head).

The chain is tamper-*evident*, not tamper-*proof*. It detects modification; it
does not prevent it. A production system would need append-only storage or a
WORM bucket. That distinction is in `LIMITATIONS.md` and should be said out loud
rather than glossed over.

**The LLM cannot choose a money action.**
`api/diagnose/` returns a `RootCause` — a failure class, a confidence, an
`is_recoverable` flag and a timing hint. That is the entire surface. The action
is chosen afterwards by `api/policy/`, a priority-ordered YAML rule set with no
model in it, and then permitted or refused by the gate chain. Two structural
facts back this up rather than a promise: `DiagnosisContext` has no field for a
name, contact, email or amount, so a PII leak into a prompt is a type error at
the call site rather than an oversight inside a prompt builder; and when the
deterministic normaliser returns anything but `UNKNOWN`, its class wins and the
model's is recorded but discarded.

The day-5 batch measured what that costs: across arm B the LLM drove **2 of
~207 decisions**, and arm B matched the LLM-disabled arm C on every metric in
every seed, at roughly 40× the decision latency. The boundary is not
theoretical — it is load-bearing enough that removing the model changes
nothing.

**The gate chain cannot be bypassed.**
`evaluate_chain()` runs all eleven gates in PRD §9's order on every decision,
including the gates after one has already blocked, because the Decision Tape
shows every verdict and not just the first refusal. No argument, flag or config
value reorders or skips any of them, and `G0_KILL` is first.

Two subtleties, both found by review rather than by design, both now the
reason this boundary holds:

- A gate's BLOCK can be converted into a *remedy* — G2_PREDEBIT blocking a
  charge becomes a pre-debit notice. That substitute is a compliance-relevant
  action in its own right, and dispatching it on the blocked action's gate
  pass would be a full-chain bypass. `resolve_with_remediation()` re-gates
  every substitute from scratch, in a bounded loop.
- When that loop exhausts its bound without settling, **nothing dispatches**.
  An earlier version fell back to dispatching an unverified action on that
  branch — the same bypass shape, reintroduced in the fix for it. The
  exhaustion path now records a blocked decision and stops.

Gates are re-evaluated at **fire time**, not only when an action is scheduled.
A retry scheduled at 10:00 and fired at 19:30 is judged against 19:30's
reality, which is the entire point of a compliance window.

**Timing is policy's, not the gates'.**
Gates decide what is *permitted*. `api/policy/timing.py` decides when a
permitted charge is *worth making* — the next payday window, or 24 hours after
the last attempt. It can only ever push an action later, never earlier and
never past a gate, and whatever it schedules is re-gated in full when it fires.

This boundary was missing until day 5. Policy rules carried `timing:
NEXT_PAYDAY_WINDOW` from day 3 and the pipeline dropped it, so every scheduled
retry fired immediately — the baseline's timing, wearing Salvage's name. The
batch found it by losing to the baseline. Targets are anchored to the case's
own history rather than to `now`, because a target recomputed from `now` on
every cycle walks forward forever while each individual step looks reasonable.

## What the pipeline writes

One decision cycle produces exactly one `decisions` document (PRD §8.2's
shape) and the `audit` rows for whatever it dispatched or refused. The
frontend renders that document directly rather than a projection invented for
it, so there is one definition of what a decision is. The join between them is
`decision_id`, carried in the decision's own body — it was not, until a review
found the join was structurally impossible despite comments claiming otherwise.

`run_id` partitions everything: the live system writes to `None`, and each eval
batch to its own partition, including its own hash chain. A batch's forty
synthetic mandates share a database with real traffic but never a chain, a case
lookup, or the Command Deck.
