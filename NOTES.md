# NOTES

Running log of surprises, platform behaviour the docs did not mention, and bugs
found. Raw material for the "what broke" beat of the video and for
`LIMITATIONS.md`. Entries are append-only in spirit: correct one by adding to
it, not by deleting it.

Format per the `bug-evidence` skill: what happened, why, how it was found, fix,
class. Mark anything worth naming in the video with `**demo**`.

---

### 2026-09-01 — day 0, scaffolding

Three things surfaced while building the harness, before any feature code
existed. All three are design conflicts in the spec rather than bugs, and all
three are now resolved in `PRD.md` §8.3.

**1. `audit.idempotency_key` cannot be unique on its own.**
SPEC §5.6 writes a `pre` row and a `post` row carrying the same key, but PRD
§8.3 declared `{idempotency_key: 1} UNIQUE`. Under that index the post row is
unwritable. Changed to a compound unique index on `(idempotency_key, phase)`.
The dedupe property invariant 9 depends on is unchanged: a second `pre` insert
for the same key still raises `DuplicateKeyError`, which still means "already
executed".
*How it was found:* writing the repository tests before the executor existed.
*Class:* spec-drift.

**2. The hash chain needed a way to survive a concurrent append — and then
that guard turned out to be a batch bottleneck.**
Two writers reading the same chain head would both compute the same `prev_hash`
and fork the chain silently, so `prev_hash` became a unique index: the second
writer fails with `DuplicateKeyError` and retries against the new head. Same
"unique index instead of a lock" move the PRD already makes for idempotency.

A global unique index on `prev_hash` alone, though, means one chain per
collection — and §10 runs twenty seeds across three arms. Sixty runs appending
to one chain serialises every audit write in the batch behind a single
contended head, against NFR-02's three-minute budget. The index is now compound
on `(run_id, prev_hash)`: one chain per run, `run_id` null for live.
`verify_chain.py` walks and reports each partition separately, so a break names
the run it happened in.

Two consequences worth writing down:

- `run_id` had to be added to the `events` and `audit` shapes in PRD §8.2, and
  it is part of the hashed body — so a row cannot be moved between partitions
  undetected.
- The genesis sentinel stopped being merely tidy and became load-bearing.
  `GENESIS_HASH` was already an explicit 64 zeros rather than null, which is
  what makes partitioning safe: MongoDB indexes a missing field and an explicit
  null identically, so a null genesis under a compound index would be
  indistinguishable from a row whose `prev_hash` was never written — a
  malformed document would present as the head of a chain.

*How it was found:* review, not by a check. The harness had nothing that could
have caught it — no check reasons about write throughput. Noted as a hole.
*Class:* ordering / performance.

**2b. An exhausted chain append must be filed, not dropped.**
The retry loop originally raised a bare `RuntimeError` with no jitter.
Deterministic backoff makes two colliding writers re-collide on the same
schedule — a lockstep livelock rather than a resolution — so retries are now
full-jitter, and worst-case total is under a second. On exhaustion the
repository writes a `CHAIN_APPEND_EXHAUSTED` row to `exceptions` carrying the
undelivered document, *then* raises `ChainAppendExhaustedError`. A silently
dropped audit row is worse than a failed action: the action leaves a trace
elsewhere, the missing row leaves none.
*Class:* other — durability.

**3. 100% branch coverage of an empty package is a vacuous pass.**
`--cov=api/gates --cov=api/normalise --cov-fail-under=100` reports 100% of zero
statements and exits green while neither module exists.
`tests/test_coverage_floor.py` makes that a visible failure until there is code
to measure. It deletes itself, in effect, on days 3 and 5.
*How it was found:* reading the first green coverage report and not believing it.
*Class:* other — harness honesty.

**Resolved, day 1** (was open for day 6): `audit.phase` gets a third value,
`block`, alongside `pre`/`post`. Re-evaluation computes a new `decision.id` and
therefore a new `idempotency_key`, so a later ALLOW never collides with an
earlier BLOCK under `(idempotency_key, phase)`. See PRD §8.3.1. Decided now,
flagged by `spec-guardian`'s day-1 phase-close review, so day 6 builds
`execute_action()` against a settled shape instead of discovering the collision
mid-implementation.

**Toolchain:** Python 3.12 is not installed on this machine. The venv is 3.13.5.
`pyproject.toml` still declares `>=3.12` and targets `py312` for ruff and mypy.

---

### 2026-09-01 — day 1, Razorpay test setup

**The checker could not check the checker. **demo****

`scripts/check_boundaries.py` shipped on day 0 with
`SDK_SCAN_ROOTS = [ROOT / "api"]` and a comment exempting `scripts/` from the
Razorpay SDK scan — reasoning that a setup script is not a runtime path. CLAUDE.md
invariant 1 says "Only `api/executor/client.py` may import the Razorpay SDK. **No
exceptions.**" So the repo had an exception to an invariant that says it has none.

`make verify` was green across both day-0 commits. It could not have been
anything else: the violation lived *inside the enforcement mechanism*. A checker
cannot detect a hole in its own scope, because the hole is exactly the region it
does not look at. Every other invariant here is protected by a check; the checks
themselves were protected by nothing.

It took a prompt from outside the system to surface it.

*How it was found:* not by any check — by review, from outside.
*Fix:* `SDK_SCAN_ROOTS` is now repo-wide with zero exemptions;
`scripts/seed_razorpay.py` reaches Razorpay through
`api.executor.client.get_razorpay_client()` like everything else. Added
`test_every_top_level_python_directory_is_scanned`, which derives the set of
top-level directories containing Python and asserts the scan covers all of them —
so a package added later cannot silently fall outside it. That test is a check on
the checker's *scope*, which is the one thing the checker could not check itself.
*Class:* other — harness honesty.

The general finding, worth saying out loud on camera: a self-checking system's
blind spot is its own configuration. The fix is not more checks, it is deriving
the check's scope from the repository rather than from a hand-maintained list.

**Test-mode card token expiry — a demo-killer found on day 1 rather than day 10.
**demo****

Razorpay test-mode card tokens are valid **3 days**. Concretely:

| If a mandate is authenticated on | The token is dead from |
|---|---|
| 2026-09-01 (today, day 1) | 2026-09-04 (day 4) |
| 2026-09-09 (day 9) | 2026-09-12 |
| 2026-09-10 (day 10, submission) | 2026-09-13 |

Submission is ~2026-09-11. **Anything authenticated today is dead six days before
it is needed.** The failure mode is silent: seeding succeeds, subscriptions look
right in the Dashboard, and the live portion of the video breaks on the morning of
the 10th with no time to re-seed.

Split adopted: plans, customers and subscription *objects* are created now, because
they do not expire. Card mandates are **authenticated on day 9 or 10 only**. Added
to CLAUDE.md under "Things that will bite you" so it is a repo rule, not a note
somebody has to remember to re-read.

*How it was found:* reading SPEC §5.8 against the calendar before writing the seed
script, which is the entire reason §5.8 says "know these before Day 1".
*Class:* other — platform constraint.

**Seed run — what exists in test mode now**

Created 6 plans and 4 subscriptions.
Account was a clean slate beforehand (0 plans, 0 subscriptions, 0 customers,
0 payments). Ids are in `.razorpay-manifest.json`, gitignored.

| Plan key | Razorpay id | Amount | Category |
|---|---|---|---|
| `insurance_term` | `plan_TWf805RpChtWTv` | ₹47,500 | insurance |
| `mf_elss` | `plan_TWf80GO5RcJeBW` | ₹95,000 | mf |
| `saas_business` | `plan_TWf7zjyA9QeYH7` | ₹15,000 | standard |
| `saas_enterprise` | `plan_TWf7zuE35jtzy4` | ₹15,001 | standard |
| `saas_pro` | `plan_TWf7zYYJmbIL22` | ₹1,499 | standard |
| `saas_starter` | `plan_TWf7zOZSuIvNJA` | ₹299 | standard |

| Subscription key | Razorpay id | Plan | Status | Purpose |
|---|---|---|---|---|
| `sub_business` | `sub_TWf80TVDUE1ABy` | saas_business | created | fixture:charged+invoice_paid |
| `sub_enterprise` | `sub_TWf82A8VpuRH7d` | saas_enterprise | created | fixture:payment_failed |
| `sub_halt_clock` | `sub_TWf84YVgmkUqE4` | saas_pro | created | fixture:halted — started day 1 |
| `sub_mf` | `sub_TWf83MDmAonvRT` | mf_elss | created | fixture:afa_category_flip |

**Payment links consumed: 0 of 30.** Two are budgeted for fixture capture
(one paid, one spare). 28 remain for day 6 executor work and the ~8 demo cases.
Update this line every time a link is created — the cap is per test business
and permanent.

**Card tokens created: 0.** No mandate has been authenticated, deliberately.
Expiry arithmetic, for whenever the first one is:

| Authenticated on | Token dead from |
|---|---|
| 2026-09-01 (day 1) | 2026-09-04 |
| 2026-09-09 (day 9) | 2026-09-12 |
| 2026-09-10 (day 10) | 2026-09-13 |

Fixture-capture mandates (steps 5-7 of the runbook) will be authenticated
whenever those steps are run and are *expected* to expire — they are not demo
cases. Demo mandates: day 9-10 only.

**Payload vs SPEC §4.1 / §5.7: not yet checkable.** No real delivery has been
captured, so the assumption that `payment.failed` carries
`error_code / error_description / error_source / error_step / error_reason` on
the payment entity is still an assumption. Day 3's taxonomy is built on that
shape. The first captured `payment.failed` settles it, and the answer goes here
before any work starts on `taxonomy/failure_classes.yaml`.

**HMAC parity with Razorpay: run, and it FAILED — 33 of 33, gap still open.**
`tests/local_only/test_real_signature.py` ran against all 33 real captures with
`RAZORPAY_WEBHOOK_SECRET` from `.env`. Every single one failed to verify;
the negative-case test (a tampered body against the real secret) correctly
failed too, confirming the test itself works. A 33/33 failure rate, uniform
rather than scattered, points at a constant mismatch rather than data
corruption — `capture_webhooks.py` never touches the raw bytes (see its
docstring), so intermittent corruption would show as intermittent failures,
not total ones.

**Most likely cause:** `.env`'s `RAZORPAY_WEBHOOK_SECRET` was updated after the
Dashboard webhook was already created with a different secret value, and the
two were never re-synced.

**Not yet fixed.** Doesn't block committing fixtures — every committed fixture
is self-signed with the throwaway `salvage_fixture_webhook_secret`, which is
unaffected by this. But it means the actual question — *does our HMAC
implementation match Razorpay's* — is still open, not closed, going into day 2.
**Before day 2 builds ingest, re-sync the secret** (Settings → Webhooks → the
webhook → check/copy its secret into `.env`) and re-run
`tests/local_only/test_real_signature.py` to confirm parity for real.
*Class:* other — configuration, not a code bug, but a real gap to close before
trusting HMAC verification against production traffic.

---

### 2026-09-01 — day 1, mandate authentication, real Razorpay finding **demo**

**"This card is not eligible for recurring payments."**

Attempted to authenticate `sub_business` (runbook step 5) with
`4111 1111 1111 1111` — Razorpay's generic one-time-success test card. Rejected
at the payment sheet: *"This card is not eligible for recurring payments.
Please use a different card."*

**The mechanism:** a one-time payment succeeding and a card being
tokenizable for recurring/e-mandate use are two different capabilities in
Razorpay test mode. Neither SPEC §5.8 nor the runbook drew that distinction —
both implicitly assumed "a success card" was interchangeable for both flows.
It is not. A card must be specifically marked recurring-eligible in Razorpay's
test card list to authenticate a subscription mandate at all.

**Why this matters beyond today:** it is a second, independent test-mode
constraint alongside the 3-day token expiry and the 30-link cap — all three
belong in the same "know these before you're mid-demo" category SPEC §5.8
exists for, and none of the three were discoverable by reading the docs
casually; each was found by hitting it.

**Fix:** use a card from Razorpay's current test-card list explicitly marked
*Recurring: Yes*, pulled from the live docs rather than hardcoded here — that
list is exactly the kind of thing that goes stale in a repo and stays current
on Razorpay's site.

*How it was found:* live attempt, runbook step 5, first real mandate auth try.
*Class:* other — platform constraint, not a Salvage bug.
*Action:* runbook step 5 amended below to name the constraint and point at the
live source instead of a fixed card number.

---

### 2026-09-01 — day 1, resolved: the recurring-eligible test card

Follow-up to the "not eligible for recurring payments" entry above. Fetched
Razorpay's live **Test Card Details** page
(razorpay.com/docs/payments/payments/test-card-details/) rather than guessing
from memory — that list is exactly the kind of thing that goes stale if
hardcoded from training data.

**Confirmed card: `4718 6091 0820 4366`** (Visa, domestic) — the only card on
that page's "Test Cards for Subscriptions" table, i.e. the only domestic card
Razorpay documents as valid for subscription/mandate authentication.
`4111 1111 1111 1111` is not on that table; it is a general one-time-payment
success card, which is the whole mechanism behind the earlier rejection.

Runbook steps 5 and 6 updated to use this card by number rather than pointing
at "go check the docs" — a name beats a pointer once the fact is actually
confirmed, but the runbook still says to re-verify against the live page if
Razorpay's list ever changes, since this repo's copy will not update itself.

*Class:* other — platform constraint, now resolved rather than merely logged.

---

### 2026-09-01 — day 1 close: fixtures captured, Phase 1 findings **demo**

33 real webhook deliveries captured, redacted, and committed to
`tests/fixtures/`. All 6 required event types present:
`payment.failed`, `subscription.charged`, `invoice.paid`, `subscription.halted`,
`payment_link.paid`, plus bonus real coverage of `payment.authorized`,
`payment.captured`, `order.paid`, `subscription.authenticated`,
`subscription.activated`. **Missing: `subscription.pending`** — never appeared
across 33 captures, including both times a charge failed. Accepted as an open
gap rather than chased further; it carries no error data, so it doesn't block
the normaliser or taxonomy, only Phase 2's state-machine handling.

**Payment links consumed: 1 of 30.** `plink_TWkzpIT0B82OiQ`, reused for both a
decline attempt and the final paid attempt rather than spending two — a payment
link accepts a retry after a failed attempt. 29 remain.

**Card tokens created and their real 3-day expiry:**

| Card token | last4 | Subscription | Created (UTC) | Dead from (UTC) |
|---|---|---|---|---|
| `card_TWkJxareLJjBrt` | 4366 | `sub_business` (`sub_TWf80TVDUE1ABy`) | 2026-09-01 10:52 | 2026-09-04 10:52 |
| `card_TWkOU7zmIRscvm` | 4366 | `sub_enterprise` (`sub_TWf82A8VpuRH7d`) | 2026-09-01 10:56 | 2026-09-04 10:56 |
| `card_TWl0UHQhBItr7s` | 0001 | payment link (decline attempt) | 2026-09-01 11:32 | 2026-09-04 11:32 |
| `card_TWl2zj130w7h2J` | 4366 | payment link (paid) | 2026-09-01 11:34 | 2026-09-04 11:34 |

None of these are demo-case mandates — all four are fixture-capture only, per
the day-1 rule, and are expected to expire on schedule. `sub_mf` and
`sub_halt_clock` remain unauthenticated.

**Real payloads vs SPEC §4.1 — three findings, none of them small.**

1. **`error_source`/`error_step`/`error_reason` are not reliably populated.**
   The first `payment.failed` we captured (forced via the Dashboard's
   "Charge this now" trigger) had only `error_code` and `error_description`
   set; the other three were `null`. A later one, via a genuine decline card on
   a Payment Link, had all five fields populated. **Conclusion: the normaliser
   cannot assume all five fields exist — it must handle a partial shape as a
   real, non-rare case, not an edge case.** This is a design input for day 3,
   not a nice-to-have.

2. **Razorpay's documented test-card decline reasons did not reproduce.**
   `4100 2800 0008 0001` is documented as producing `error_code: insufficient_fund`.
   What we actually got: `error_code: BAD_REQUEST_ERROR`,
   `error_reason: payment_failed` — generic, not the specific reason the docs
   promised. One data point only; we did not get a second decline to confirm
   whether this generalises. **Open question for whoever builds the taxonomy:**
   test-mode declines may only ever produce this one generic reason, meaning
   `taxonomy/failure_classes.yaml`'s specific reason-string mappings
   (`insufficient_funds`, `card_expired`, etc.) may be untestable against real
   test-mode traffic at all, and can only be exercised via the simulator (§5.9)
   or synthetic fixtures built by hand — not verified against a live capture.

3. **The Dashboard's "Charge this now" test trigger does not simulate a
   real decline.** Its only observed failure mode, twice, identically:
   `BAD_REQUEST_ERROR` / "Mandate debit not as per frequency" — Razorpay's own
   mandate-timing rule rejecting a charge forced before the invoice's natural
   due date. This is a test-mode artifact of forcing a charge early, not a
   simulation of a card or bank decline. **It cannot be used to generate
   taxonomy-relevant failure fixtures**, only to test the halt/recovery state
   machine.

**Zero-retry halt, no `subscription.pending` — contradicts the T+1/T+2/T+3
model everywhere it's stated.** `sub_business` went `active` → one forced
failure → `halted`, immediately, 8 seconds apart, with zero `subscription.pending`
event in between. PRD §1, §11 and SPEC §1 all describe Razorpay's own retry as
T=0 fail → `pending` → T+1/T+2/T+3 → `halted` — four attempts, not one, with an
intermediate state. **Two explanations, not yet distinguished:** either the
Dashboard's manual test-charge trigger bypasses the normal retry ladder
entirely (most likely, since it's explicitly a test tool, not a real payment
attempt), or the T+1/T+2/T+3 model doesn't apply the way the docs describe. This
needs resolving before Phase 2's out-of-order test is trusted, since that test
assumes the normal state progression. **Also observed: `halted` is not
terminal** — a further successful "Charge this now" brought `sub_business`
straight back to `active`. Recovery-from-halted is real and should be a case in
the state machine, not an oversight.

**HMAC parity with Razorpay: tested, and it failed — 33/33.** See the entry
above. Real gap, open for day 2, not closed by this session.

*How each was found:* live capture against a real test-mode account, not
inferred from docs. *Class:* spec-drift (findings 1, 3, halt sequence) /
platform constraint (finding 2) / configuration (HMAC).
