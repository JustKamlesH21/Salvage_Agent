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

---

### 2026-09-01 — day 1 correction: HMAC parity is confirmed, not failed

The "33/33 failure" entry above was wrong, and it was wrong for a specific,
useful reason. Root cause: `tests/local_only/test_real_signature.py` had its
own hand-rolled `.env` line parser
(`line.partition("=")[2].strip().strip('"').strip("'")`). `.env`'s
`RAZORPAY_WEBHOOK_SECRET` line carried an inline comment —
`zovva5-Vejhoh-kogmyc         # you choose this when creating the webhook` —
and `.strip()` only removes whitespace from the string's ends, not a comment
sitting after real content. The parser read the comment as part of the secret.

**The actual production config loader was never wrong.**
`api/config.py`'s `Settings()` (pydantic-settings, wrapping python-dotenv)
correctly parsed the same line down to `zovva5-Vejhoh-kogmyc` — confirmed by
direct check before touching anything. So Phase 2's ingest code would have had
the correct secret from day one; only this diagnostic script's second,
redundant parser was broken.

**This matters as a general lesson, not just a fixed bug.** The instruction to
check in this order — trailing whitespace, wrong secret, header name, raw bytes
— is exactly right, and "uniform failure across every payload = wrong key, not
implementation bug" is a good heuristic. But it has a gap this case exposed:
uniform failure can also mean *a diagnostic tool read the wrong bytes for the
key*, which looks identical to a wrong key from the outside and isn't caught by
"check the secret is right" if the check re-implements its own reading of it.
The fix — reuse the real loader instead of writing a second parser — is the
generalizable takeaway: never hand-roll a second path to a value production
code already loads correctly.

**Fix:** `real_webhook_secret()` now imports `api.config.settings()` directly.
Re-ran `tests/local_only/` against all 33 real captures: **34/34 pass** (33
payloads + the tampered negative case). HMAC parity with Razorpay is
genuinely confirmed. Phase 2 ingest can trust this signing path.

*How it was found:* checked in the exact order requested — repr()'d the .env
value first, and the comment was visible immediately.
*Class:* other — test-harness bug, not a code or secret bug.

---

### 2026-09-01 — day 1: halt-sequence investigation, levers tried

Trying to distinguish two hypotheses for why `sub_business` went
`active` → `halted` in 8 seconds with zero `subscription.pending` (see day-1
close entry): (1) manually triggered charges sit outside the normal
T+1/T+2/T+3 retry ladder entirely, or (2) Razorpay halts immediately on a
non-retryable validation error and only runs the ladder on soft declines.

Every lever tried, and what each one actually produced:

| Lever | What we hoped for | What actually happened |
|---|---|---|
| Dashboard "Charge this now", repeated | A different failure type on a recurring charge | Only ever produces one error, twice, identically: `BAD_REQUEST_ERROR` / "Mandate debit not as per frequency". No lever to choose a different reason. |
| Success/Fail toggle during subscription authentication | An auth-stage decline, cleanly isolated from the frequency artifact | **Does not exist.** That toggle only appears on one-time/Payment Link checkout, not on subscription mandate authentication. Confirmed by direct attempt on `sub_mf`. |
| Authenticating `sub_mf` (₹95,000) at all | A working recurring subscription above ₹47,500, to test further | Blocked pre-flight: "Amount exceeds maximum amount allowed." **Produces zero webhooks** — no `payment.failed`, nothing. See the reconciliation-gap finding below. |
| Authenticating a ₹47,500 probe subscription | Narrow the amount cap found on `sub_mf` | Succeeded fully. **Cap is between ₹47,500 and ₹95,000**, not yet narrower than that. |
| Daily-period plan (`period: "daily", interval: 1`) | A short natural billing cycle, inside the 3-day token life | Rejected by Razorpay: `"Interval provided is less than the minimum interval (7) allowed for the given period (daily)"`. **7 days is the platform's minimum cycle length, in test mode, full stop** — there is no way to get a shorter one. |
| Weekly-period probe (`period: "weekly", interval: 1`) | The shortest cycle Razorpay allows | Created: `sub_TWlaybreMaeqRd`. **Structurally, its 7-day cycle is longer than the 3-day token life** — by the time this cycle fires, the token will already be dead regardless of what we do. Authenticated, clock running; result lands within this session or shortly after. |

**Conclusion so far:** test mode gives us no lever to produce a genuinely
different *type* of recurring-charge failure on demand. The only two available
recurring-failure mechanisms are (a) the mandate-frequency artifact from
forcing a charge early, and (b) whatever happens when the weekly probe's cycle
collides with its own expired token — which is itself a natural failure, just
not the specific decline reason (insufficient funds, etc.) SPEC's taxonomy
lists. Documented as a genuine platform limitation, not a lack of trying — see
`LIMITATIONS.md`.

*Class:* platform constraint.

---

### 2026-09-01 — day 1: silent-failure gap found by driving the platform

**`sub_mf`'s pre-flight rejection produces zero webhooks.** Not
`payment.failed`, not anything. Confirmed by checking the capture receiver's
count before and after two separate attempts — no change either time. This is
structurally different from a real decline, which creates a payment entity and
fires `payment.failed`: the amount-cap rejection happens before any payment
attempt exists to report.

**This is a real hole in the product, not just in the test setup.** Salvage is
entirely webhook-driven — every gate, every decision, every audit row starts
from an inbound event. A mandate can fail repeatedly for a reason that never
produces a webhook, and Salvage never learns. Money leaks with zero audit
trail, silently, in exactly the system whose whole thesis is an auditable
paper trail for every money-relevant event.

**Not building this now.** Logged as a P1 requirement — see `PRD.md` FR-28 —
with the note that it was found by driving the real platform, not by reading
docs, because a doc read would never have surfaced a rejection that generates
no event to read about.

*How it was found:* checking receiver capture count before/after a rejected
mandate authentication attempt, expecting to see nothing and confirming it.
*Class:* other — a genuine architectural gap, found the honest way.

**Weekly probe authenticated. Report-back date: 2026-09-07.**
`sub_TWlaybreMaeqRd` (`sub_weekly_halt_probe`) authenticated successfully with
the recurring-eligible card. `current_end` (next due) is
**2026-09-07 18:30 UTC**. Its card token dies ~2026-09-04 — three days before
the cycle fires, exactly the collision predicted above. Check back on/after
2026-09-07 for whether the natural charge attempt produces
`subscription.pending` (supporting hypothesis 1: manual charges bypass the
retry ladder, scheduled ones use it) or halts immediately like the forced one
did (supporting hypothesis 2 differently, or suggesting the ladder doesn't
distinguish forced-vs-scheduled at all). Whichever way it lands is the
clean, quotable answer to the halt-sequence question.

**`sub_halt_clock` also authenticated**, for completeness. Next due
2026-09-30 — past submission, won't report in time, kept only as a longer-term
comparison point if the project timeline ever extends. The weekly probe
(above) is the one that will actually answer the halt-sequence question.

---

### 2026-09-01 — day 2: the recurring pattern, named **demo**

Four times now, independently, across three different days: **the checking
machinery has a blind spot the checking machinery cannot see, because the
blind spot is the boundary of what it was built to check.**

1. **The boundary checker exempted itself.** Day 0's `check_boundaries.py`
   shipped with `scripts/` carved out of the Razorpay-SDK scan, on the
   reasoning that a setup script isn't a runtime path. `make verify` stayed
   green through two full days of commits — it could not have caught this,
   because the exemption lived inside the enforcer, not in the code it was
   enforcing against. A prompt from outside the system found it.

2. **The diagnostic re-implemented the thing it was diagnosing.** Day 1's
   HMAC-parity test wrote its own small `.env` parser rather than importing
   `api.config.settings()`, and that second parser didn't strip an inline
   comment the real parser handles correctly. The test failed 33/33 in a
   pattern that looked exactly like a wrong secret — the textbook signature
   the debugging checklist says to check first — and the checklist was right
   to say check it first; the mistake was in a piece of infrastructure that
   duplicated, rather than reused, the thing under test.

3. **A static check cannot see a design-level violation of the invariant it
   enforces.** `check_boundaries.py` greps for `update_one`/`delete_one`
   against `events`/`audit` and fails the build if it finds one — a real,
   working check. But PRD §8.2 lists `processing_state` as
   `received | processed | failed | duplicate`, and three of those four
   values are literally impossible to record on an append-only row without
   mutating it after insert. No static check flags a *schema* that implies a
   mutation nobody has written yet. That gap surfaced during day 2's planning,
   before any code was written, only because it was reasoned through by hand.

4. **Day 0's own chain tests passed by accident, not by proof.** Every
   hand-built fixture in `tests/test_repositories.py` used a datetime with
   exactly zero microseconds — `dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.UTC)`
   — which happens to survive BSON's millisecond truncation with nothing
   lost. A real `datetime.now(dt.UTC)` call, used for the first time in day
   2's ingest code, almost never lands on an exact millisecond, and every one
   of day 2's chain-integrity tests failed immediately on the very first run
   against real (mocked) storage. The chain's own hash-then-store-then-reread
   round trip had never actually been exercised with realistic data — day 0's
   test suite was green because its fixtures were convenient, not because the
   code was correct. Fixed in `api/db/chain.py`'s `_encode()` (truncate to
   millisecond precision before hashing, matching what Mongo will actually
   persist) and pinned with a real BSON round-trip test,
   `tests/test_chain.py::TestBsonRoundTrip`.

**The shape is the same every time:** a checker's test data, scope, or
implementation quietly avoided the exact condition that would have exposed the
gap, and the gap was only found once something outside that narrow path — a
real capture, a real timestamp, a real prompt — actually exercised it.

**The generalisable fix is the same every time, too, and it is not "add
another check":** derive scope and inputs from the real system rather than
from a hand-maintained, convenient stand-in for it. `check_boundaries.py` now
derives its scan roots from the actual repository tree
(`top_level_python_dirs()`), not a hand-written list. The HMAC test now
imports the real config loader instead of re-parsing `.env` itself. The chain
now hashes at the precision the real database actually stores. Three of four
fixes are already this shape; the fourth (processing_state) has a documented
scoping decision rather than a mechanical fix, because no check can currently
derive "this schema implies a mutation" automatically — that one stays a
standing risk to reason about by hand at each new phase, not something day 2
closed.

This is the strongest "what broke" material in the project so far. Four
independent instances of one root cause, found across four different pieces
of infrastructure, each one caught by hitting the real thing rather than by
reading about it. Say the general version on camera, then show one instance
concretely (recommend #1 or #4 — #1 is the cleanest one-sentence story, #4 has
the most satisfying "here's the failing test, here's the one-line fix" beat).

*Class:* other — harness honesty, project-wide pattern.

---

### 2026-09-01 — day 2: a real bug, found by `compliance-auditor` on its own diff

`compliance-auditor`, reviewing the day-2 ingest diff before phase close (per
CLAUDE.md — every money-path-adjacent diff gets this), flagged
`_is_chain_race()` in `api/db/repositories.py`. The original version matched
`"prev_hash" in str(exc)` against the stringified `DuplicateKeyError` — which
matches the substring anywhere in the exception's text, including inside the
colliding *value*, not just the index name. `rz_event_id` is a business key
sourced straight from the `X-Razorpay-Event-Id` header, which Razorpay's HMAC
signature does not cover (it signs the body only). An attacker who set that
header to a string containing the literal text `prev_hash` could get a
genuine duplicate-webhook replay misclassified as a chain-head race:
`insert()` would burn all 8 retries with backoff, raise
`ChainAppendExhaustedError`, return a false `STORAGE_FAILED` (500) telling
Razorpay to keep retrying a webhook that had already been durably stored, and
file a bogus row to the exception desk.

*Class:* correctness / compliance — a real bypass-adjacent bug, not a design
question. *How it was found:* `compliance-auditor` reviewing a diff that
looked done (tests green, boundaries green) — the finding a bare `make
verify` pass could not have surfaced, because the substring match was
never wrong for any of the fixture data the tests used. *The fix:* read the
driver's structured `exc.details["keyPattern"]` — the index *definition*
(`{"run_id": 1, "prev_hash": 1}`), not attacker-reachable data — instead of
the message string. Pinned by `tests/test_repositories.py::TestAppendExhaustion::test_a_business_key_collision_is_not_retried`
and the equivalent HTTP-layer case in `tests/ingest/test_router.py`. Added a
second regression using a real (not constructed) mongomock error —
`test_a_business_key_collision_whose_value_contains_prev_hash_is_not_misread`,
event id `evil-prev_hash-evil` — so the proof rests on the actual driver
error shape, not an assumption about it.

---

### 2026-09-01 — day 2: a second real bug, plus a fifth instance of the harness-blind-spot pattern

**The bug.** `events.rz_event_id` had one unique index, global. An invalid
signature is stored, not rejected (§8.2 note above) — deliberately, as
forensic evidence. But that means a forged delivery permanently occupies its
`rz_event_id`: after a webhook-secret rotation, Razorpay's own
legitimately-signed retry of that same event collides on the same key,
gets classified `duplicate`, and is never stored as valid. The event
vanishes rather than recovers — silently, since `duplicate` returns 200 and
Razorpay has no reason to keep retrying a 200. *Class:* correctness — a
genuine design gap in the idempotency scheme, not a bypass, but one that
would have surfaced for real the first time a webhook secret rotates during
the project, which CLAUDE.md's own "things that will bite you" list does not
mention as a risk. *Fix:* the unique constraint on `rz_event_id` is now two
partial indexes, one scoped to `signature_valid: true` and one to `false`
(`api/db/collections.py`). One invalid row and one valid row can coexist per
event id; a second identical forgery at the same id still collides within
its own partition, so a replayed forgery still can't grow the collection
without bound. PRD §8.3 updated in the same commit.

**The fifth instance.** Implementing the fix surfaced a fifth case of day 2's
recurring pattern (see the first day-2 entry above): `apply_indexes()` built
every index via one bulk `db[name].create_indexes(IndexModel[...])` call.
Against real MongoDB this is correct and passes `partialFilterExpression`
through untouched. Against mongomock 4.3.0 — what every single test in this
suite runs on — `create_indexes` silently drops every index option except
`unique`, `sparse`, `name` and `expireAfterSeconds` before storing the index;
`partialFilterExpression` is accepted and discarded with no error. Every
existing test that exercised `rz_event_id` uniqueness would have kept passing
with the new partial-index code in place while testing a *plain* unique
index underneath — the harness would have said the fix worked while quietly
testing something else. Found by writing the regression test for the fix
itself and checking, by hand, what index `mongomock` actually stored
(`db.delegate['events']._store.indexes`) — not something `make verify` would
ever have caught, because every test passed either way.

*The fix, same shape as the four before it:* `apply_indexes()` now calls the
driver's `create_index()` once per declared index instead of bulk
`create_indexes()`. `create_index` (singular) honours
`partialFilterExpression` correctly in both mongomock and the real driver, so
the same code path that ships to Atlas is what the test suite actually
proves. Verified directly against `Repositories.events.insert()` before
writing the pytest version:
`TestSignatureValidPartitionedIdempotency::test_a_valid_retry_after_an_invalid_row_for_the_same_event_id_succeeds`
would have failed under the old bulk-call form (the "valid" insert would
have collided with the "invalid" one under a non-partial index) and passes
under the fix.

*Running count:* five independent instances of "the checking machinery's own
scope, test data, or implementation quietly avoided the exact condition that
would expose the gap" — four in the first day-2 entry, this one the fifth,
found in a different piece of infrastructure (the test double library
itself) than any of the first four. Worth saying on camera as one line, not
five: *the tests all passed, and the fix still didn't work, because the tool
the tests run on doesn't support the feature being tested and doesn't say so.*

---

### 2026-09-01 — day 2: a second `compliance-auditor` pass, six more findings, and a new kind of test harness

A fresh, independent `compliance-auditor` pass over the `rz_event_id`
partial-index fix above — asked explicitly to reproduce every claim, not read
it — found six problems, two of them real production bugs (F1, F2), one a
real gap in a *different* fix on the same diff (F3), one a hardening call
worth making anyway (F6), and two documentation corrections (F5, plus F4 as
part of F2's fix). All six addressed in the same commit as the original fix;
none shipped separately.

**F1 — HIGH, and a genuinely different species of bug from the five logged
above.** The partial-index fix replaced `uq_rz_event_id` in
`api/db/collections.py`'s `INDEXES`, but `create_index`/`create_indexes` only
ever *add* — the old index doesn't vanish because a newer one replaced it in
source. On the real Atlas cluster this project has been running against
since day 0, `uq_rz_event_id` already exists from every earlier startup, so
deploying the fix as written would have left the global constraint fully
enforced *alongside* the new partial ones — the exact rotation bug the fix
exists to close, still live, on the only database that matters.

The first five day-2 findings all share one shape: a checker's own scope,
test data, or implementation quietly avoided the exact condition it should
have caught. F1 is not that. Every test in this suite builds a fresh,
empty mongomock database — there is no checker whose *scope* excludes
`uq_rz_event_id`, because there is no code path, anywhere, that constructs a
database with day-0's index still on it. A fresh fixture cannot represent
"a database that already ran an earlier version of this code" by
construction, not by oversight. Five instances of a narrow checker; this one
a test environment with no history at all. Different bug, different fix:
not "make the check look at more," but "give the test suite something with
a past to look at."

**The fix — a migration-test harness, built for reuse.** `tests/db_history.py`
holds `HISTORICAL_INDEXES`, one entry per index shape this project has ever
shipped as current (today: `"day0_global_rz_event_id"`, the pre-fix global
index), plus `seed(db, version)` to apply one directly, bypassing the current
`apply_indexes()`. `tests/test_repositories.py`'s `seeded_db` fixture
composes that seed with the *current* `apply_indexes()`, reproducing exactly
what a real upgrade does. `TestIndexMigration`'s regression test seeds the
day-0 shape, stores a forgery, and asserts a legitimate retry at the same
event id succeeds — it failed against the partial-index fix (confirmed
before touching `collections.py` again) and passes once `apply_indexes()`
explicitly drops superseded index names first. Seven more phases of schema
change are coming; this is the first entry in what should be a running list,
not a one-off.

**F2 — HIGH. The two partial indexes had an uncovered document partition.**
`partialFilterExpression: {signature_valid: true}` and `{...: false}` between
them cover only documents where the field is present and exactly boolean. A
document with the field missing, `null`, or any other type matches neither
filter and had *no* uniqueness constraint at all — never reachable today
(`verify_signature` always returns a strict `bool`), but a structural
weakening of a guard that used to be unconditional. Also: MongoDB only uses a
partial index when the query provably matches its filter, so `by_rz_event_id`'s
unqualified `find({rz_event_id})` would have silently fallen back to a
collection scan on real Atlas — invisible in mongomock, which doesn't model
that optimizer behaviour. **Fix:** one compound unique index,
`(rz_event_id, signature_valid)`, no partial filter — no uncovered partition,
and usable by any query on its prefix. This also let `apply_indexes()` go
back to a single bulk `create_indexes()` call, since a plain compound unique
index (unlike a partial one) survives mongomock's bulk path.

**F3 — MEDIUM, a gap in the *other* half of the same diff.**
`EventRepository.by_rz_event_id()` was an unfiltered `find_one({rz_event_id})`.
Fine when at most one row could exist per event id; wrong the moment the
uniqueness fix allows two. Natural insertion order returns whichever landed
first — the forgery, in the exact rotation scenario the fix is built around
— directly contradicting the ingest module's own stated rule ("only a valid
signature is eligible for anything past storage"). No caller exists yet, but
day 3's case creation will be one. **Fix:** scoped to `signature_valid: True`.

**F5 — LOW, a claim that overstated itself.** The docstrings and PRD said the
new index "bounds growth" without qualification. `rz_event_id` is
attacker-reachable (Fix 1's whole premise), so an attacker sidesteps the
bound entirely by varying the id. Corrected everywhere to: bounds a *replay
at one id*, not the number of ids an attacker can create — an idempotency
property, not a rate limit. Also added the sentence that was missing: a
forgery and a genuine event can now coexist at the same id by design, and F3
is what stops that from leaking into a lookup.

**F6 — LOW, a hardening call rather than a live bug.** `_is_chain_race`
defaulted to "not a chain race" when a `DuplicateKeyError` carried no
`keyPattern` at all — theoretical, since both mongomock and real MongoDB
(since 4.2) always send one, but the wrong default in principle: it means an
error with no evidence either way is announced as `IngestOutcome.DUPLICATE`,
a 200 telling Razorpay to stand down for an event that may never have been
stored. **Flipped:** no `keyPattern` now defaults to *treat as a possible
chain race* — retried, and failed loudly via `ChainAppendExhaustedError` on
exhaustion if it wasn't one. A wrong retry costs a few attempts and a visible
exception-desk row; a wrong `DUPLICATE` costs a silently lost event. Between
a loud wrong answer and a quiet one, fail toward the loud one.

*How all six were found:* a second `compliance-auditor` pass explicitly
briefed to reproduce every claim in the first pass's fix rather than read it
— it ran the migration scenario against a database seeded with the old
index, checked what document shapes the partial filters actually covered,
and grepped for callers of `by_rz_event_id`. *Class:* correctness /
compliance (F1, F2, F3) and hardening (F5, F6) — F1 the standout, since it is
the first entry in this project's notes that isn't "a checker's scope missed
this" but "no checker could have had the state needed to see this at all."

---

### 2026-09-01 — day 2: a third `compliance-auditor` pass, and the remediation of a migration bug had its own migration bug

Fixing F1 (the missing legacy-index drop) introduced a new one. Sent back for
a third, still-more-adversarial pass — briefed explicitly to try to break the
fix and to stress the new migration harness itself, since a harness this
project will lean on for seven more phases deserved that scrutiny before
being trusted. It found three more: one real (F7), one theoretical but worth
closing (F8), and one coverage gap in the harness it was itself asked to
stress (F9).

**F7 — HIGH. The fix for "the old guard never gets removed" removed it at
the wrong end of a window.** `apply_indexes()` dropped the legacy index
*before* creating the replacement. Between those two statements,
`events.rz_event_id` carried no uniqueness constraint at all — a window that
did not exist before this remediation, since the pre-F1 code only ever
added indexes and never dropped anything. A duplicate `rz_event_id` landing
in that window (a rolling deploy, a SIGKILL, a transient Atlas blip between
the two calls — none of it exotic) makes `create_indexes(unique=True)` fail
on every subsequent startup, permanently: `apply_indexes()` raises out of
the FastAPI lifespan, and the collection is left with FR-02's idempotency
guard absent for good. Reproduced end to end against mongomock with a
day-0-seeded database taking a live write in the window.

**The fix is one line moved, but the reasoning matters more than the
line.** Create the replacement index first, drop the legacy one after. Safe
here specifically because every legacy index this project has ever declared
is a special case of what replaces it — a global unique constraint is
strictly stronger than the compound one that supersedes it, so any dataset
already satisfying the old index also satisfies the new one, and the create
step cannot fail because the old guard is still live. The general rule this
leaves for the next migration: create-before-drop is only safe when the new
constraint is implied by the old one holding; a future migration to a
*stricter* index needs the reverse order, or a window is unavoidable and
must be a documented, accepted risk rather than a default.

**F8 — LOW-MEDIUM. The drop itself was a check-then-act.** Two processes
booting at once (a rolling deploy's overlap, `uvicorn --workers N`) can both
see the legacy index via `list_indexes()` and both call `drop_index()`; the
loser's call fails, and on mongomock that failure carries `code=None` (the
same "not the IndexNotFound code real MongoDB sends" quirk F1's original fix
already had to route around) so it propagates unhandled and crashes that
instance's boot. Fixed by re-checking existence after a failed drop:
still-gone means a benign lost race, still-present means a real failure, and
only the second case re-raises.

**F9 — LOW, and it's about the harness, not the index code.** The
two-partial-index shape that existed briefly between the global index and
the compound one was never committed (confirmed:
`git log --all -S "uq_rz_event_id_valid"` returns nothing) but was a
working-tree-current shape for long enough that a real dev cluster could
plausibly carry it — and `_LEGACY_INDEXES` already names both of its index
names. `tests/db_history.py` had no historical entry for that shape, so the
harness's own claim ("add an entry whenever an index is renamed, dropped, or
reshaped") had gone unmet for its own most recent example. Added
`"day2_partial_rz_event_id"`, plus a test that a database carrying *every*
legacy shape at once (global index and both partials, simultaneously) still
converges to exactly the current compound index.

**The pattern across F1/F7/F8/F9, stated once:** fixing a migration bug is
itself a migration, and a migration is exactly the kind of code this
project's own fresh-fixture test suite structurally cannot verify without
the harness built for that purpose. F7 shipped inside the very fix meant to
close F1. The harness (`tests/db_history.py`) is now the thing that catches
this category going forward — but only for the shapes someone remembers to
add, which is why F9 exists too. *Class:* correctness (F7), hardening (F8),
test-coverage (F9). *How found:* a compliance-auditor pass briefed to try to
break its own prior fix and stress-test the harness, rather than confirm
either.

---

### 2026-09-01 — day 2: a fourth `compliance-auditor` pass — the F7/F8 fixes had no test that would catch their own reversion, and F9's fixture was proving the wrong thing

Sent for one more pass, briefed to try to break the F7-F9 remediation itself
and to find a tenth issue if one existed. It found four: two high, both
about test validity rather than production behaviour; one medium, a
documentation claim that turned out to be false for one of the two legacy
shapes it covered; one low, a name-collision hazard the F7 reordering
introduced as a side effect.

**F10 — HIGH. The F7 and F8 fixes shipped with zero tests that detect their
own reversion.** Proven, not asserted: compliance-auditor moved the
`_drop_legacy_indexes()` call back above the create loop — reintroducing F7
verbatim — and the full suite stayed green. It then deleted the entire F8
try/except/re-check block, leaving a bare `drop_index()` call, and the suite
stayed green again. `TestIndexMigration`'s tests only assert the *converged*
end state, which is identical under either ordering on a database with no
conflicting rows in it, and `seeded_db` calls `apply_indexes()` as one
opaque step, so no test could see the intermediate ordering at all. Two
compliance-auditor passes' worth of fixes were one careless edit away from
silent reversion, in the migration path for the collection FR-02's
idempotency guard depends on.

**Fix:** `tests/test_collections.py` (new), testing `apply_indexes()`'s
internals directly rather than only its converged output. One trap
documented there for whoever extends it: `db[name]` returns a **fresh
wrapper object on every access** in mongomock_motor
(`db["events"] is db["events"]` is `False`), so monkeypatching a method on
one such wrapper silently does nothing on the next access. The ordering
test instead wraps the whole database object (`_RecordingDB`), proxying
`__getitem__` itself so the call log survives across accesses. Confirmed
this test fails against both reintroduced bugs before restoring the fixes,
then confirmed it passes again after.

**F11 — HIGH, and a recurrence of F1's exact failure class inside its own
remediation.** `tests/db_history.py`'s `seed()` used the same bulk
`create_indexes()` call that `api/db/collections.py`'s own docstring already
identifies as dropping `partialFilterExpression` under mongomock. Seeding
`"day2_partial_rz_event_id"` therefore silently produced two plain
global-unique indexes, not the real partial pair — the migration test for
that shape was passing because it was quietly re-testing the day-0 shape a
second time under different names. Confirmed by sabotage: removing both
partial names from `_LEGACY_INDEXES` failed only the *third*
(all-legacy-shapes-at-once) test; the dedicated partial-shape test stayed
green regardless, because its assertions never actually depended on the
partial semantics being real.

This is the same root cause as F1 (a fixture that cannot represent the
state it claims to) recurring *inside the fix for F1*, one level deeper: the
harness meant to make migration bugs visible was itself not exercising what
it named. **Fix:** `db_history.seed()` now creates each index individually
via `create_index()`, the same fix `apply_indexes()` briefly used and then
reverted (see the fourth day-2 entry) — except here it stays, because
`seed()` has no bulk-path performance reason to avoid it. The partial-shape
migration test also gained the same explicit index-name assertion the
all-legacy-shapes test already had, so passing now actually requires the
drop to have happened.

**F12 — MEDIUM. The safety argument for create-before-drop was backwards in
words and false in one case.** The docstring said the legacy index is
*"weaker"* than its replacement — backwards; the safety condition is that
the legacy index be **stronger**, so the create step cannot fail while it's
still enforcing a superset of the new constraint. True for the global
shape. **Not true for the partial shape**, and this is the one that
matters: a partial index has no opinion on documents outside its filter, so
two documents with a missing or non-boolean `signature_valid` are *both*
accepted under the partial pair while violating the compound index that
replaces it. Reproduced against real MongoDB (mongomock does not model
partial-index constraint semantics at all, so it cannot show this):

```
two docs, same rz_event_id, signature_valid missing -> both accepted under the partials
apply_indexes() (create-first) -> E11000 on the compound index create
```

Behaviourally this was never a live bug — `verify_signature()` always
returns a strict `bool`, so no real document reaches this state — and the
failure mode it describes is the *safe* one: the create raises, the FastAPI
lifespan fails to start, and the legacy guard is left fully intact, never
dropped. The bug was that the docstring asserted this couldn't happen
instead of describing what happens if it does. **Fixed:** wording corrected,
the per-shape distinction spelled out, and a new local-only, real-`mongod`
test suite (`tests/local_only/test_real_mongo_migration.py`, same
run-by-hand convention as `test_real_signature.py`) added specifically
because this is the second time in this project a mongomock approximation
has actively hidden a real-MongoDB behaviour rather than merely failing to
model it. **Run against a live local mongod 8.3.7: 3/3 pass**, including the
E11000 reproduction above.

**F13 — LOW, a side effect of the F7 reordering itself.** Create-before-drop
means a currently-declared index name that also happened to appear in
`_LEGACY_INDEXES` would be created, reported as created, and then
immediately dropped, silently, on every startup — under the old drop-first
order the create would have won instead. No collision exists today, but
`_LEGACY_INDEXES` is append-only by design (never shrinks), so this is a
real hazard for a future index rename that reuses an old name. **Fixed:** a
name-disjointness check, run at import time so a future collision fails
loudly and immediately rather than silently eating an index on every boot.

*How all four were found:* a fourth compliance-auditor pass, explicitly
briefed to try to break the F7-F9 fixes and to try to find a tenth issue —
not to confirm the third pass's "holds up" verdict. *Class:* test-coverage
(F10, F11 — the harness itself was the bug, twice), documentation (F12, but
with a real-MongoDB-only reproduction behind it), hardening (F13). Running
total across four passes on the same ~150 lines of index code: thirteen
findings, none of them in the money path (no gate, policy, or executor code
exists yet to bypass), all of them in exactly the kind of infrastructure
this project's own thesis says deserves the most scrutiny — the append-only,
idempotency-guaranteeing layer everything else is built on.

---

### 2026-09-01 — day 2: a fifth `compliance-auditor` pass, one finding, and a clean verdict

Explicitly briefed this time to give a direct clean-or-not verdict rather
than hunt for a fifth round's worth of findings on principle. It reproduced
F1 through F13 as genuinely fixed — including reverting F7 and F8 again, by
hand, against the *new* F10 tests this time, and watching them fail
correctly — and found exactly one new issue, low severity, not in the index
logic at all.

**F14 — LOW. The evidence file for F12 was gitignored and could never be
committed, while production code cites it by path.** `.gitignore` excluded
the whole of `tests/local_only/`, written for `test_real_signature.py`'s
actual reason (needs a real secret and uncommitted captures). The new
`test_real_mongo_migration.py` needs neither — only a reachable local
`mongod` — but inherited the same blanket exclusion, meaning the file that
`api/db/collections.py`'s own docstring points to as proof of F12 would
vanish in any fresh clone, and the "3/3 pass" recorded above would become
unreproducible the moment this working copy is gone. **Fixed:** narrowed
`.gitignore` to the one file that actually needs it
(`tests/local_only/test_real_signature.py`), leaving
`test_real_mongo_migration.py` committed normally; `norecursedirs` in
`pyproject.toml` is still what keeps the whole directory out of the
standard run regardless of what's tracked in it. Also generalized
`TestApplyIndexesOrdering` (F10) to iterate `_LEGACY_INDEXES`' own
collections rather than hard-coding `"events"`, per this pass's note that
`_LEGACY_INDEXES` is append-only and may one day name a different
collection.

**The verdict, stated plainly because CLAUDE.md asks for honest reporting,
not just honest findings:** clean. Every fix across all five passes now has
a test that fails when the fix is undone — checked by removing each one and
watching the suite catch it. Thirteen real findings (fourteen counting this
one) across four rounds of adversarial review on roughly 150 lines of index
migration code, zero of them in a money path that doesn't exist yet (gates
land day 5, executor day 6), all of them in exactly the layer this project's
own submission argument rests on: the append-only, idempotency-guaranteeing
data layer. This is the strongest single-thread demonstration in the
project so far of what "use the subagents, don't skip them because the
change looks small" (CLAUDE.md) actually buys — a two-file diff that looked
finished after fix #1 and needed thirteen more rounds of scrutiny to
actually be so.

---

### 2026-09-01 — day 2 phase close: `spec-guardian` found a real bug the index-audit loop missed, D3

Five rounds of `compliance-auditor` scrutinised the storage layer
(`api/db/collections.py`, `api/db/repositories.py`) until it was airtight —
and the bug that actually reached a user was one layer up, in
`api/ingest/webhook.py`, which none of those five passes were briefed to
read closely because it hadn't changed since day 2 opened. `spec-guardian`,
checking the whole diff against the PRD rather than re-auditing the index
code, found it in one pass.

**D3 — the exact "silent loss" failure mode the whole index remediation
exists to prevent, still present one function away.** `ingest_webhook()`'s
`except DuplicateKeyError` branch returned `IngestOutcome.DUPLICATE` (→ 200)
unconditionally, regardless of the *current* delivery's own signature
validity. The compound `(rz_event_id, signature_valid)` index correctly
lets a corrected retry land as a new row after a secret rotation — but only
if that retry doesn't itself collide with something. A second (or third)
delivery still signed with the *wrong* secret — the realistic shape of "the
secret is misconfigured," not an attacker — collides with the first
invalid row on the `(id, false)` pair, and the old code answered that 200,
telling Razorpay to stand down. The corrected retry, once the secret is
actually fixed, then arrives on an event Razorpay has already stopped
redelivering: permanently unrecoverable, silently.

The code's own two neighbouring pieces of reasoning already said the right
thing and were not consulted: `api/ingest/router.py`'s status-map comment
("never 200 unless the event is durably stored... invalid signature and
storage failure both need Razorpay to keep retrying") and
`api/db/repositories.py`'s `_is_chain_race` docstring ("between a quiet
wrong answer and a loud one, only the loud one is survivable"). **Fix:** the
`DuplicateKeyError` branch now checks the current delivery's
`signature_valid` before choosing an outcome — `INVALID_SIGNATURE` (401) if
it's false, `DUPLICATE` (200) only if true. Pinned by two new tests in
`tests/ingest/test_webhook_ingest.py::TestInvalidSignature`: a bare replay
of an invalid delivery stays 401 three times running, and an end-to-end
misconfigured-secret story (two wrong-secret deliveries, then one corrected
one) proves the corrected retry is actually recovered as `RECEIVED`.
Confirmed both fail against the pre-fix code and pass after, by hand.

**Documentation drift, fixed in the same commit per CLAUDE.md's rule:**
PRD §8.1 and §8.2 still described `events.rz_event_id` as a single global
unique index after §8.3 had already been corrected — the contradiction had
apparently been sitting in the PRD since the very first index fix. PRD §8.3
cited §8.4 for the "invalid signature is stored" decision; §8.4 says nothing
about it — that decision had never actually been written into the PRD as a
requirement, only inferred from a schema field's presence (now added as its
own note in §8.2). SALVAGE_SPEC §3 (F02) and §4.3 carried the same stale
global-index claim. Two new PRD §8.3 paragraphs now document the legacy-index
migration mechanism and the `_is_chain_race`/invalid-signature "fail loud"
rule — both real, load-bearing decisions that previously existed only in
code comments. README.md and `tests/fixtures/README.md` had stale fixture
counts (33, last updated day 1 close) against an actual 54; README also
still claimed "no feature code yet" with `api/ingest/` already three modules
in — the kind of claim spec-guardian called "the cheapest possible
unsupported claim for a judge to falsify." `LIMITATIONS.md` gained an entry
for the one safety claim (create-before-drop's precondition, for the
partial-index legacy shape) that is verified against real MongoDB only by
hand, not by `make verify` or CI.

*How found:* `spec-guardian`, run as the last step of phase close per the
`phase-close` skill, reading the diff against the PRD rather than against
its own prior findings. *Class:* correctness (D3 — a real bug, the only one
of this entire day-2 saga to survive five rounds of storage-layer audits by
living one function outside their brief) plus documentation drift (the
rest). The lesson worth stating plainly: an adversarial review scoped to
"the index code" will not find a bug in the code that calls it, no matter
how many rounds it goes. `spec-guardian`'s job — checking the *whole* diff
against the *contract* — is not redundant with `compliance-auditor`'s even
when both run at the same phase close; this is the concrete case where
skipping one would have shipped a real bug.

---

### 2026-09-02 — replan: submission compressed from a 10-day to a 3-day window

**Original target:** submit ~2026-09-11 (Day 10 of the original PRD §11
plan). **New target:** submit 2026-09-04, hard deadline 2026-09-05. Days 1–2
(Razorpay test setup, ingest) are already built and closed; three days of
work remain, not eight.

**The trade, stated once so the reasoning is visible later and not just the
outcome:** statistical rigor in the eval (the original 500-case/20-seed/
95%-CI protocol) and in-doubt reconciliation in the executor (resolving a
crash between dispatch and the post-audit write) are both cut, in exchange
for keeping the full agent loop — normalise, diagnose, policy, all eleven
gates, the executor — complete and demoable, plus enough frontend (Command
Deck + Decision Tape) to make the compliance thesis visible on screen. A
narrower system that fully works and is honestly measured beats a broader
one that is thin everywhere or measured with a rigor the timeline can't
actually back up. Between "smaller and true" and "bigger and overclaimed,"
this project takes smaller and true — same instinct as the net-vs-gross
recovery number and the doc-derived-baseline admission already in
`LIMITATIONS.md`.

**What changed, concretely** (full detail in PRD §11 and §6, SALVAGE_SPEC
§5.10/§10/§11, `LIMITATIONS.md`):

- Eval: 500 cases × 20 seeds × arms A/B/C with 95% CIs → 30–50 cases × 3–5
  seeds × arms A/B (C conditional on Day 3's diagnose call being cheap to
  toggle), reported as a range across seeds, not a CI.
- S3 Batch Evidence screen cut; its A/B/C table moves into README.md as
  markdown.
- Executor in-doubt reconciliation cut; the pre/post audit idempotency
  guarantee (invariant 9) is unaffected, but nothing yet resolves a
  pre-row-with-no-post-row left by a mid-dispatch crash. Logged in
  `LIMITATIONS.md`, not silently absent.
- All P1 (FR-19–FR-24) and P2 (FR-25–FR-27) features are out of scope,
  marked in place in PRD §6 rather than deleted, so `spec-guardian` doesn't
  read their absence as unexplained drift.
- `compliance-auditor` cadence changes from Day 4 onward: one pass per
  money-path phase, findings fixed same session, not the iterative
  multi-round loop day 2's storage layer got (five rounds, thirteen
  findings — see the entries above). Three days don't afford that cadence
  twice.

*Why now:* the replan happens at the start of Day 3, before any Day
3-forward feature code is written, so the compressed plan is the one being
built against from the first line rather than retrofitted after the fact.
*Class:* other — scope/schedule, not a bug.

---

### 2026-09-02 — day 3: normalise + diagnose + policy built, spec-guardian's
first real-money-path-adjacent review, and two correctness bugs found before
the code ever reached a gate **demo**

Built `taxonomy/failure_classes.yaml` (114 rules — 110 of Razorpay's
published `error.reason` values plus a description-only fallback for the
Dashboard forced-charge artifact and two `error_code`-only fallbacks),
`api/normalise/` (pure `classify()`, 100% branch coverage — the pyproject
gate is now measuring real code, not an empty package), `api/diagnose/`
(Groq call, Pydantic-constrained `RootCause`, one repair retry, `DISABLE_LLM`),
and `api/policy/` (11 priority-ordered rules, every one cited). Full design
reasoning is in the approved plan this session started from; this entry is
what changed after that plan, not a restatement of it.

**The taxonomy was built from Razorpay's docs fetched live this session**
(`razorpay/docs/payments/payment-gateway/rainy-day/errors/error-reasons`,
`razorpay/docs/errors/payments/list`, `razorpay/docs/errors/payments/cards`),
not from training data — SPEC §13's own warning about this list going stale
if hardcoded. `taxonomy/razorpay_published_reasons.yaml` commits the fetched
list independently of the classification file, so FR-04's acceptance
("every reason... maps to a FailureClass or explicitly to UNKNOWN") is a
checked property (`tests/normalise/test_taxonomy.py::test_every_published_reason_has_a_rule`),
not an assertion resting on the same file it's checking.

**The honest coverage number, since FR-04/SPEC §10 ask for it:** of 110
published reasons, 56 classify into one of the 8 real `FailureClass` values
and 54 classify `UNKNOWN` by design — 28 are merchant/integration errors
(not a customer payment failing at all), 24 are real Razorpay reasons for a
flow Salvage's domain never triggers (raw card/VPA re-entry on a stored
mandate that's never re-collected, or Cardless EMI — out of PRD §5's scope),
2 are genuinely ambiguous and flagged rather than force-fit. **Only 1 of 110
has a real fixture behind it** — `payment_failed` (fixture 029). The other
109 are doc-only, because Phase 1 already found Razorpay's documented test
declines don't reproduce in this account (day-1 close entry, above). Recorded
in `LIMITATIONS.md` now with the real numbers, not the placeholder text it
carried from Phase 1.

**Two judgment calls confirmed with the user before building, not decided
alone:** generic no-detail declines (`card_declined`, `payment_declined`,
`debit_declined`) → `ISSUER_HOLD` rather than `CREDENTIAL_DEAD` (asymmetric
risk: a wrong retryable guess costs one attempt, a wrong terminal guess
silently stops all recovery on a case that was actually fine); and
`payment_failed` (the one real fixture) → `CHANNEL_DOWNTIME`, using its
`source: gateway` field as the tiebreaker.

**`spec-guardian`, run per CLAUDE.md's phase-close workflow, found two real
correctness bugs — both the exact asymmetric-risk mistake the judgment calls
above were built to avoid, just missed on two other entries:**

1. **`international_transaction_not_enabled` was misfiled as `CREDENTIAL_DEAD`.**
   It's a capability switch the customer can turn back on — the same shape
   as `card_not_enrolled`/`card_disabled_for_online_payments`, both correctly
   `CREDENTIAL_STALE`. Filed under `CREDENTIAL_DEAD` it would have set
   `stop_all_retries: true` on a recoverable case the first time a real
   payload hit it. Moved to `CREDENTIAL_STALE`; same no-blind-retry handling,
   different (non-permanent) class. *Class:* correctness.

2. **`MANDATE_INVALID` was routed through the same policy rule as
   `CREDENTIAL_DEAD` (`POL-001` → `CREATE_REAUTH_LINK`), but SPEC §4.1's own
   table says `MANDATE_INVALID`'s default play is "re-registration flow
   (needs AFA)"** — i.e. `A5 CREATE_AFA_LINK`, not `A4 CREATE_REAUTH_LINK`.
   Inherited from copying SALVAGE_SPEC.md §5.4's worked example verbatim
   without checking it against §4.1's own class table, which disagreed with
   it. Split into `POL-001` (`CREDENTIAL_DEAD` only) and a new `POL-010`
   (`MANDATE_INVALID` → `CREATE_AFA_LINK`), both still `stop_all_retries`.
   *Class:* correctness / spec-drift — the two SPEC sections contradicted
   each other and the code had silently picked one side.

**Five more findings, all documentation/honesty gaps, all fixed in this
commit:** NFR-01's wording said "LLM call capped at 8s" but the code applies
that cap per HTTP request with one repair retry, so the real worst case per
decision is ~16s — PRD §7 now says this explicitly rather than overclaiming
a tighter bound than the code delivers. SPEC §5.3 rule 3 ("log both
attempts") wasn't implemented — `llm_raw` kept only the last attempt after a
repair; `api/diagnose/client.py` now joins both, labelled, into the one
`llm_raw` string PRD §8.2 defines. The taxonomy's fallback-rule ordering
(reason-keyed rules must sort before the code+description fallback) was
convention, not a checked property — now pinned by a test. `POL-002`/
`POL-099` cited a `industry:` slug (`fail-closed-on-uncertain-diagnosis`)
that names an internal design principle, not an industry practice claim or a
Razorpay doc — re-cited to point at `SALVAGE_SPEC.md §4.1` directly instead,
under a new `salvage-spec:` citation form (documented where it's used, not
silently invented). SALVAGE_SPEC.md §5.4's worked example (`POL-004`,
`priority: 40`) is now annotated as illustrative-only, since the real file
uses the policy-rule-authoring skill's priority bands instead — SPEC's
number sits inside the skill's Re-auth band despite being a retry-scheduled
rule, a conflict between the two documents that predates this phase; the
skill's bands won because it's the actively-maintained authoring guide.

**One documented-and-deferred design decision:** `Diagnosis.source` stays
`"llm"` even on the rare path where the normaliser is `UNKNOWN` and the LLM
fails validation or times out on *both* attempts — there's no diagnosis to
attribute either way, but the field only has two values (PRD §8.2) and an
LLM call genuinely was attempted, which "deterministic_override" doesn't
describe either. The honest fact lives in `evidence[0]`, not `source`.
Flagged in PRD §8.2 rather than silently resolved; a three-state `source`
would say this more precisely but that's a schema change for another day.

**Two open questions surfaced by spec-guardian, deliberately NOT resolved
this session — Day 4's call, not mine to make alone under a compressed
timeline:**

- **No policy rule can ever propose `SEND_PRE_DEBIT_NOTICE` (A3) or
  `CREATE_AFA_LINK` (A5) for a case that isn't already `MANDATE_INVALID`,
  because `PolicyContext` deliberately carries no amount** — that routing
  was scoped to the gate chain (G2_PREDEBIT/G3_AFA, PRD §9) by this
  session's approved plan, on the reasoning that "gates decide what is
  permitted, policy decides what is worth doing." SPEC §5.5's formal gate
  contract (`ALLOW`/`BLOCK`/`REPLAN(new_ts, clause)`) doesn't obviously
  support a gate *substituting* a new action the way SPEC §6.3's Case View
  mock shows (three actions executing after one REPLAN) — that's a real
  tension inside SPEC itself, not something this phase's policy work
  created. Whoever builds Day 4's gates needs an answer before G3_AFA can
  do anything with a >₹15,000 case; this note is so it isn't discovered
  fresh that morning.
- **`PolicyContext.attempt_count`'s source is unbound.** PRD §8.2's `cases`
  shape has both `attempts: [{source: rz_auto|salvage_manual}]` and a
  separate `salvage_attempt_count` field "drives G4_BUDGET" — `POL-009`'s
  `channel_note` asserts the Salvage-only reading (matching CLAUDE.md's "do
  not accidentally design away" instruction on this exact lever), but
  nothing wires it yet, since case/attempt tracking doesn't exist before
  Day 4. If it's ever wired from total attempts including Razorpay's own
  T+1/T+2/T+3, POL-009 fires after roughly one Salvage attempt instead of
  four, which is exactly the lever CLAUDE.md warns not to design away.

*How found:* the taxonomy, is_recoverable table and priority-band decisions
were built and cross-checked by hand against SPEC §4.1/§5.4/§5.5 during the
build; `spec-guardian`, run once per this phase's money-path-adjacent diff
(CLAUDE.md's Day 4-onward cadence rule, applied here a day early since this
diff already touches the policy engine), found the two real bugs and the
five documentation gaps a self-review missed. *Class:* correctness (the two
misfiled classes) / spec-drift (the SPEC-vs-skill priority bands, the
SPEC-self-contradiction on `MANDATE_INVALID`) / harness honesty (the
taxonomy coverage numbers, the fallback-ordering test, the published-reasons
fixture) / documentation (NFR-01's wording, the citation re-point).

`make verify`: green on lint, format, `mypy --strict`, boundaries, chain, and
web. Red on exactly the two Day-4 placeholders (pytest's `api/gates`
coverage-floor check, the clause check's 11-gate list) — the scaffold
telling the truth, not a regression. 292 tests pass; `api/normalise` holds
100% branch coverage.

---

### 2026-09-03 — day 4: gates G0-G10, executor, pipeline wired — `make verify`
8/8 for the first time, and a real full-chain bypass found and closed before
anything shipped **demo**

Built all eleven gates (`api/gates/`), the executor (`api/executor/`,
including a new `arm.py` — see below), a pure fire-time scheduler
(`api/scheduler/`), and `api/pipeline.py` — the webhook → normalise →
diagnose → policy → gates → executor → audit wiring Day 3 explicitly left
for today. `make verify` went 8/8 for the first time in the project: lint,
format, `mypy --strict`, 100% branch coverage on `api/gates` *and*
`api/normalise`, boundaries, clauses (all eleven gates cite, all eleven
policy rules explain), chain, web build. 426 tests pass.

**Two open questions Day 3 left explicitly for today were resolved, not
deferred again** (both in the approved plan, both cross-checked against
`.claude/skills/gate-authoring` which I hadn't read yet on Day 3):

1. *How does a gate-BLOCKed action become the compensating action SPEC
   §6.3's Case View mock shows (a pre-debit notice, an AFA link)?* Resolved
   with a **remediation map** in `api/gates/registry.py` — `{"G2_PREDEBIT":
   SEND_PRE_DEBIT_NOTICE, "G3_AFA": CREATE_AFA_LINK}` — outside both policy
   and gates, so the ₹15,000/24h thresholds each gate owns are never
   duplicated. The gate-authoring skill itself settled the companion
   question of *whether policy should know about amount at all*: "G3_AFA
   above the threshold does not replan... it blocks, and the policy engine
   proposes an AFA link instead" — read only today, and it directly informed
   this design (policy still doesn't see amount; the remediation map is the
   layer that reacts to *which* gate blocked, per the skill's own framing of
   gates deciding what's permitted and policy/remediation deciding what's
   done about it).
2. *Whose attempt count feeds G4_BUDGET/POL-009 — Salvage's own, or
   Razorpay's T+1/T+2/T+3 too?* Resolved: `PolicyContext.attempt_count` and
   `GateContext.salvage_attempts_in_window` are both derived from `audit`
   rows for `RETRY_NOW`/`RETRY_SCHEDULED` specifically — Razorpay's own
   auto-retries never touch `audit` at all, so they structurally cannot
   inflate this count. The lever CLAUDE.md warns not to design away is
   intact by construction, not by discipline.

**G1_WINDOW's scope was confirmed with you before building**, not decided
alone: gates customer-contact actions only (notices, links, nudges), exempt
for silent `RETRY_NOW`/`RETRY_SCHEDULED` — the cited RBI circular
(DOR.ORG.REC.65/21.04.158/2022-23) governs recovery-agent *contact*, not
auto-debit timing, even though SPEC §6.3's illustrative mock shows G1
blocking a plain retry. Same treatment as Day 3's POL-004 priority-number
finding: SPEC's mock is flavour, not a literal contract this deviates from.
**Scheduler scope was also confirmed**: `fire_due_jobs()` is a pure, fully
tested function (including the literal 10:00-scheduled/19:30-fired/G1-replans
test you asked for by name); no live APScheduler thread was wired in —
deliberately, per that confirmation, though `compliance-auditor` later
sharpened *why* that matters (see F6 below).

**Regulatory sourcing for `RULE_TEXT`** — the gate-authoring skill is
explicit that this must be a quotation, not a summary from memory, and to
stop and say so if the clause can't be found. Fetched live this session
(WebSearch, since no available tool can pull the raw RBI PDFs): RBI circular
DOR.ORG.REC.65/21.04.158/2022-23 (12 Aug 2022) for G1's 8am–7pm window;
the RBI Digital Payments E-mandate Framework, 2026 (RBI/DPSS/2026-27/396, 21
Apr 2026 — matches PRD's existing citation) for G2/G3/G6/G8, cross-corroborated
by multiple independent secondary sources on the ₹15,000/₹1,00,000 figures
and the 24h pre-debit and post-debit-alert requirements. `COMPLIANCE.md` says
plainly this is corroborated secondary sourcing, not verbatim primary text —
a materially stronger basis than memory, but a different claim, and the file
doesn't blur the two.

**`compliance-auditor`, run once per this session's instruction (no
iteration), found eleven issues — two of them critical, and the two that
matter most are both full-chain-bypass-shaped, which is exactly the
"reachable execute_action() without a full gate traversal" question you
asked to be told about first if it turned up:**

**F1 — CRITICAL. The remediation action itself was never gated.** When
`resolve_final_action()` substituted a remedy (say, `SEND_PRE_DEBIT_NOTICE`
for a blocked `RETRY_NOW`), the pipeline dispatched *that* action directly —
`evaluate_chain()` was never re-run against it. Reproduced concretely: a
notice dispatched outside G1's window with no scheduler involved at all; an
opted-out customer still contacted because the co-occurring G3_AFA block's
remedy was chosen over G6_OPTOUT's terminal `SUPPRESS`; a cooldown-violating
notice sent because G5 doesn't apply to the *proposed* charge action being
gated, only to the remedy that never got checked. The shipped e2e test had
locked in exactly this shape as "correct" (dispatching a notice on a case
G8_POSTDEBIT had just said has no reachable channel). **Fixed:** both
`api/pipeline.py` and `api/scheduler/runner.py` now re-gate any remedy
fresh, in a bounded loop (`MAX_REMEDIATION_HOPS = 3`, proven to settle in one
hop today since G2/G3's only remedies are contact actions and G2/G3 only
gate charge actions) — REPLAN reschedules, a further BLOCK falls to a
terminal action, ALLOW dispatches only once verified on its own account.
`suppressed` now accumulates across hops instead of being overwritten by
whichever hop settles last (a second bug this fix's own first draft
introduced and the pipeline test caught immediately).

**F2 — CRITICAL. The idempotency key could never collide.**
`f"{case_id}:{decision_id}:{action.value}"` used a fresh `uuid4()`
`decision_id` on every call, so `G10_IDEMPOTENT` and the executor's own
insert-first `DuplicateKeyError` guard were structurally dead on this path —
reprocessing the same failed payment would double-dispatch, with nothing but
Phase 2's event-level dedupe (not wired to this pipeline at all) standing in
the way. **Fixed:** the key is now `f"{case_id}:{payment_key}:{action}"`
where `payment_key` is the webhook's own payment id (falling back to
`order_id`, then the case id) — stable across reprocessing of the *same*
underlying payment, while still varying naturally across genuinely distinct
retry attempts, since each one is its own payment entity.

**Five more findings, all fixed the same session:** F3 (amount read from a
case document written once and never updated, so a later, larger failure on
the same case could pass G3_AFA on a stale smaller figure) — `amount_paise`
is now an explicit, per-call argument sourced from the current webhook
payload; the remaining staleness (`category`, `subscription_status`,
`salvage_attempt_count`) is a real Day-4 scope boundary, documented in
LIMITATIONS.md's new "Money path" section rather than silently accepted. F4
(invariant 8's arm mechanism didn't exist at all — `DRY_RUN=false` alone
reached live dispatch, contradicting CLAUDE.md and the Makefile/README's own
claims) — new `api/executor/arm.py`, `execute_action()` now refuses the live
branch without it, docs corrected to describe the mechanism that actually
exists rather than a `/control/arm` route that doesn't. F5 (budget/cooldown
counted only `post` audit rows, undercounting a dispatch that died
mid-flight — unsafe direction, since under-counting means over-attempting) —
now counts `pre`+`post`, deduplicated by key. F7 (G2's `RULE_TEXT` cited
notice-content requirements — merchant name, amount, mandate ref — the code
doesn't verify, only the 24h lead time) — narrowed to what's actually
checked; a false citation is worse than a narrower true one, same principle
Phase 3's citation fixes already established. F9 (the bounded-catalog check
was a bare `assert`, stripped under `python -O`) — explicit `ValueError`.

**Two findings kept out of code, deliberately, and documented rather than
rushed:** F6 (a `REPLAN` verdict is recorded and then dropped — no
`ScheduledJob` is ever persisted, so `fire_due_jobs()` — fully built and
tested — has no caller at all, live or offline) and F8 (`G6_OPTOUT` fails
open when a case has no resolvable `customer_id`, the common shape for real
captures — but nothing calls `CustomerRepository.set_opt_out()` yet, so
failing closed unconditionally would break dispatch for most real payloads
to close a door nothing can currently open). Both in LIMITATIONS.md's new
"Money path (Day 4)" section with the reasoning, not just the gap.

**One collision found and fixed along the way, worth naming because it's a
sharp Python trap:** `api/executor/__init__.py` re-exports the `arm`
function under that same bare name, which shadows the `api.executor.arm`
*submodule* as an attribute of the package — meaning `import api.executor.arm
as x` inside `executor.py` resolved to the function, not the module, even
though `sys.modules['api.executor.arm']` genuinely holds the real submodule.
`from api.executor.arm import is_armed` (naming the function directly,
bypassing the package's own attribute table) sidesteps it. Documented
in-line since the trap is permanent as long as the package re-exports a name
that collides with one of its own submodules.

*How found:* `compliance-auditor`, briefed explicitly to answer "does
anything reach `execute_action()` without a full gate traversal" as the
single most important question. *Class:* correctness / compliance (F1, F2 —
both real bypasses, not design questions), hardening (F4, F9), correctness
(F5, F3's amount half), documentation (F7, COMPLIANCE.md rewritten to match
what each gate actually checks), scope discipline (F6, F8, F3's remaining
half — logged, not silently absent).

**`spec-guardian`, run after all `compliance-auditor` fixes landed, found
something worth stating plainly first: two of its findings (G2, G3) were a
reintroduction of F1's exact bypass shape — same hole, different branch,
introduced by my own first fix for F1.**

**G2/G3 — the hop-exhaustion fallback dispatched an unverified action, the
same class of bug F1 exists to prevent.** F1's fix re-gated every remedy in
a bounded loop, but the branch that runs when the loop exhausts its hop
budget without settling — in *both* `api/pipeline.py` and
`api/scheduler/runner.py`, independently, since the loop was duplicated
between them — fell back to dispatching `CLOSE_NO_ACTION` directly, with no
gate check on that fallback either. An unreachable-in-practice case today
(G2/G3's only remedies are contact actions, and contact actions trivially
clear G2/G3), but the loop is a defensive bound precisely for cases that
aren't reachable *today*, and "the safety net has a hole shaped exactly like
the thing it's supposed to catch" is the finding, not the reachability
question. **Fixed** by extracting the whole loop into one shared,
independently-tested function, `api/gates/registry.py`'s
`resolve_with_remediation()`, used by both callers: it now tracks a
`dispatchable` flag, and when the hop bound is hit unsettled, `dispatchable`
is `False` and *nothing* dispatches — the case is recorded blocked, not
routed to an unverified fallback. `tests/gates/test_registry.py`'s new
`TestResolveWithRemediation` proves this with a synthetic `gate_fn` that
never settles (real gates can't be forced to oscillate, since G2/G3 only
gate charge actions and every remedy is a contact action — proven by trying,
not assumed), asserting `dispatchable is False` directly.

**G1 — the real remediation loop had zero test coverage through either real
caller.** Every existing pipeline/scheduler test either executed directly or
replanned directly; none hit a BLOCK with a remedy. Closed by the same
extraction as G2/G3 (the shared function is directly unit-testable with fake
gate outcomes) plus one new integration test,
`tests/scheduler/test_runner.py`'s
`test_blocked_job_remediates_and_dispatches_the_verified_remedy`, which
fires a `RETRY_NOW` job with no pre-debit notice on record through the real
`fire_due_jobs()` and asserts the remedy is re-gated and only then executed.

**D2 — `gate_results` only recorded the first pass's verdicts, not the
remedy hop's.** A Decision Tape needs to show *why* the original action was
blocked and *why* the action that actually dispatched was found compliant —
recording only the first eleven verdicts hid the second gate pass entirely.
**Fixed**: `resolve_with_remediation()` accumulates every hop's verdicts;
`decisions.gate_results` now has 22 entries on a one-hop remediation (11 +
11), not 11.

**D3 — `decisions._id` was never written into its own document body.**
`decision_id` was generated and used to build the idempotency key but never
included in `decision_doc` itself — recoverable via the collection's own
`_id`, but the Decision Tape rendering it should not need a second lookup to
show its own id. **Fixed**: `decision_doc["decision_id"]` is now set
explicitly.

**D4 — `build_gate_context()` had unsafe silent defaults for
`amount_paise` and `kill_switch_engaged`.** Both were optional arguments
that would silently pass `0`/`False` if a caller forgot to supply them —
`amount_paise=0` always clears G3_AFA's threshold, `kill_switch_engaged=False`
always clears G0_KILL. Exactly the shape of bug that shouldn't be possible
by omission in the gate context builder. **Fixed**: both are now required,
positional-or-keyword arguments with no default; `process_payment_failed()`
resolves `dry_run`/`kill_switch_engaged` from `settings()` when the caller
passes `None`, never from a hardcoded literal.

**D5 — `PolicyContext.attempt_count` and `DiagnosisContext`'s history were
wired to `case.salvage_attempt_count`, a field nothing increments.** This is
the opposite of what "day 4: two open questions resolved" above claims was
built from the start — it wasn't; the case document's counter is written
once at creation and never updated, so every decision saw `attempt_count=0`
regardless of real history, meaning G4_BUDGET/POL-009 could never actually
fire on a real repeated case. **Fixed**: both now derive from real `audit`
rows, filtered to Salvage's own `RETRY_NOW`/`RETRY_SCHEDULED` actions (new
`_salvage_charge_attempts_in_window()` / `_prior_failure_classes()` helpers
in `api/pipeline.py`) — the design the plan described, now actually wired
that way rather than only intended that way. The "Two open questions"
section above is accurate as of *this* fix, not as of first implementation;
noted here so the record doesn't imply it was right the first time.

**D6 — `g4_budget.py`'s docstring said "case-derived," describing the bug
D5 fixed as the intended design.** Corrected to state plainly that the
window is audit-derived, Salvage-only.

**D1 — PRD §8.3.1 and SALVAGE_SPEC §5.6's idempotency-key pseudocode were
stale after F2's fix**, still showing `f"{case.id}:{decision.id}:{action.id}"`
— the exact formula F2 found structurally incapable of colliding. **Fixed**:
both now show `f"{case_id}:{payment_key}:{action.id}"` with a paragraph
explaining why `decision.id` had to come out.

**D7/D8/D9 — three stale-documentation findings, all corrected:** D7,
`.env.example` still described a working `POST /control/arm` route (mirrors
F4's fix, which corrected README.md but missed this file). D8, README's
status banner still said "day 3 complete" / "`make verify` fails" after day
4 shipped 8/8. D9, `LIMITATIONS.md`'s Measurement section claimed a ±20%
sensitivity analysis "is published" — it is not; `sim/` is scaffolding only
as of day 4, the simulator is day 7/8 work. All three corrected in place.

**D10 — this very entry's "Two open questions" section, as first drafted,
implied G4_BUDGET was audit-derived by original design rather than fixed
into that shape by D5.** Corrected above rather than left to read as a
cleaner history than actually happened — per CLAUDE.md's own instruction
that bugs found and fixed are demo material, not embarrassments to smooth
over in the log that's supposed to be the honest record of them.

**Five undocumented design decisions, now written into PRD.md §8.2/§9
rather than living only in code and this log:** G1_WINDOW's contact-actions-
only scope, G8_POSTDEBIT's reinterpretation (no A11 confirmation action
exists in the catalog, so it gates the *next charge* on channel
availability instead), G6_OPTOUT's carve-out for `CLOSE_NO_ACTION`/
`SUPPRESS` (an opted-out case must still be closeable), `final.action`'s
possible divergence from `policy.proposed_action` via remediation, and
`MAX_REMEDIATION_HOPS = 3`'s existence and fail-closed behaviour on
exhaustion.

*Class:* two of spec-guardian's findings (G2, G3) are the same severity
class as F1 — a real, if not-yet-reachable, full-chain-bypass shape — found
by the second reviewer specifically because the first reviewer's fix
introduced it. The rest are correctness gaps in what feeds the gates (D4,
D5, D6) and documentation drift (D1, D7, D8, D9, D10, the five undocumented
items). All fixed same session, per your no-iteration instruction. `make
verify` reconfirmed 8/8 after every fix in this entry.
