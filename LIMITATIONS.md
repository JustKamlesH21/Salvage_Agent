# Limitations

Written by us, and meant to be harsher than anything a judge would ask.
Volunteering a weakness before the panel finds it is the highest-leverage move
available in the defence round.

> **Day 1.** The list below started from the design and now includes what
> driving the real platform on day 1 taught us. It grows as the system is
> built, and it is not trimmed before submission.

## Structural

- **The audit hash chain is tamper-evident, not tamper-proof.** It detects that
  a stored document was modified; it does not prevent the modification. Anyone
  with write access to the database can rewrite a document *and* recompute the
  rest of the chain. A production system needs append-only storage or a WORM
  bucket. What the chain buys is that casual or accidental mutation cannot hide.

- **The boundary checks are AST heuristics, not proofs.**
  `check_boundaries.py` catches a mutating call whose receiver expression
  mentions `events` or `audit`. A collection reached through a variable naming
  neither — `c = db[name]; c.update_one(...)` — is not caught. The append-only
  repositories close most of that gap by being the only sanctioned route to
  those collections, but "the build passes" is weaker than "this cannot happen".

- **Compliance gates encode our reading of the regulation.** They are
  engineering controls, not legal advice. A production deployment needs counsel
  review.

- **One safety claim about the events index is verified against real MongoDB
  only by hand, not in CI.** `api/db/collections.py`'s `apply_indexes()`
  creates the replacement `rz_event_id` index before dropping any superseded
  one, which is only safe when the superseded index is at least as strong as
  its replacement. That holds for every legacy shape this project has
  declared — but mongomock, which the whole automated suite runs on, does
  not model partial-index constraint semantics and cannot demonstrate the
  one case where it doesn't hold (a document with a missing/non-boolean
  `signature_valid` value under the old partial-index shape). The
  reproduction lives in `tests/local_only/test_real_mongo_migration.py`,
  which needs a reachable local `mongod` and is deliberately excluded from
  `make verify` and CI (see `pyproject.toml`). It passed 3/3 against a real
  MongoDB 8.3.7 on day 2 (see NOTES.md); that result is not re-checked
  automatically on every change to `api/db/collections.py`.

## Money path (Day 4)

Found by `compliance-auditor`'s single Day 4 pass and fixed the same
session, except the three below — kept out of code for time, logged here
rather than silently absent, same discipline as everything else in this file.

- **A case's `amount_paise`, `category`, `mandate_type` and
  `subscription_status` are set once at case creation and never refreshed.**
  `api/pipeline.py`'s `build_gate_context()` now takes `amount_paise` as an
  explicit argument sourced from the *current* webhook payload (fixed —
  G3_AFA no longer evaluates a stale, possibly smaller amount from the case's
  first failure). `category`, `mandate_type` and `subscription_status`
  still come from the case document and are not: this build only processes
  `payment.failed` (PRD §2's scope), so nothing ever tells a case its
  subscription went `halted` — G9_HALTED is structurally unable to fire in
  production today, not merely untested. `category` defaults `"standard"`
  (the conservative direction — it only ever makes G3_AFA's threshold
  *lower*, never silently permits a larger no-AFA debit). `salvage_attempt_count`
  is seeded 0 and never incremented from the case side either; G4_BUDGET
  still enforces its own independent count from `audit` directly (unaffected
  by this gap), so this is a loss of a second, policy-side layer of the same
  protection, not a compliance violation on its own. Fixing all three
  properly needs the other five webhook types' state-machine handling,
  explicitly out of this phase's scope.

- **A `REPLAN` now re-fires, but only while the process is running and only
  when switched on.** This entry described a real gap until day 6: through
  day 5, `fire_due_jobs()` was fully built and tested yet had no caller, so a
  replanned case sat at its scheduled time forever. `api/scheduler/live.py`
  closed that — `tick()` calls `fire_due_jobs()`, and `api/main.py`'s lifespan
  starts it. Gates genuinely re-evaluate at fire time against the real clock.
  What remains true, and is the honest residue of the original gap:
  **`SCHEDULER_TICK_SECONDS` defaults to 0 and no loop starts unless it is set**
  — deliberate, for the same reason `DRY_RUN` defaults true, but it means an
  unconfigured deployment still drops every REPLAN. There is also no job store:
  a due job *is* the latest decision on a case whose `final.verdict` is
  `replan` and whose `final.at` has passed, read back each tick. That avoids two
  records disagreeing, but it means pending work lives only as long as the
  decision history and is discovered by scan, not by queue. A decision written
  before day 6 carries no `amount_paise`, and the tick **refuses** such a job
  rather than re-gating against an amount it cannot verify — visible on the
  Command Deck as older rows that say `due now` and never fire.

- **Opt-out (G6_OPTOUT) fails open when a case has no resolvable
  `customer_id`.** `api/pipeline.py`'s `build_gate_context()` reads
  `opted_out` from the linked `customers` document; a case with no
  `customer_id` (the common shape for real captures — fixture 029, the one
  real usable decline, has `customer_id: null`, per NOTES.md's day-1
  finding) defaults to `opted_out=False`. Making this fail closed instead
  (treat "we cannot confirm consent" as "do not contact") was considered and
  rejected for this pass: nothing in the codebase calls
  `CustomerRepository.set_opt_out()` yet — opt-out itself is not wired to
  any trigger — so the residual risk today is theoretical, and failing
  closed unconditionally would have made the majority of real payloads
  (which lack `customer_id`) unable to dispatch anything at all, for a
  protection nothing can currently activate. Revisit this the moment an
  opt-out trigger is built, not before.

## Platform

- **Manual charging of a domestic card is not supported by Razorpay.** For
  domestic card mandates the recovery path is pre-debit notice → Payment Link →
  customer-initiated, not a silent manual charge. The manual-invoice-charge
  lever — the one that does not consume the subscription's retry budget —
  applies only where supported.

- **Test-mode caps constrain the live portion of the demo.** Card tokens are
  valid three days. Thirty Payment Links per business, ever. A test charge on a
  subscription can only be triggered from the Dashboard. The shortest billing
  cycle Razorpay's test mode allows is 7 days — longer than the token life, so
  a natural (unforced) recurring charge can never fire against a token that
  hasn't already expired. Mandate registration also has an amount ceiling
  somewhere between ₹47,500 and ₹95,000 in this account, found by hitting it;
  not documented, not narrowed further. Only the ~8 hand-run demo cases touch
  the live API; the batch runs on the simulator.

- **Arm A's T+1/T+2/T+3 baseline is doc-derived, not observed, and that is a
  real weakness in the comparison, not a formality.** Razorpay's docs state
  the auto-retry timer; day 1's attempts to observe it directly in test mode
  hit two structural walls instead. First, the platform's only reachable
  recurring-charge failure mechanism — the Dashboard's manual test-charge
  trigger — produces a single validation artifact ("mandate debit not as per
  frequency"), not a simulated decline, and checkout has no fail option for
  subscription mandates the way it does for one-time payments. Second, as
  above, the shortest test-mode billing cycle (7 days) outlives the card token
  (3 days), so even a natural cycle attempt collides with an expired token
  before it could demonstrate the retry ladder cleanly. One forced-failure
  case observed directly: `active` → `halted` in 8 seconds, zero
  `subscription.pending`, no T+1/T+2/T+3 — but that was a manually forced
  charge, and whether the same holds for a real scheduled failure remains
  unconfirmed as of this writing (day 1 investigation ongoing, see NOTES.md).
  **Wherever arm A's baseline numbers are reported, this document says the
  same thing: the comparator is built from Razorpay's stated behaviour, not
  from a measured one, and a doc-derived baseline is weaker evidence than a
  measured one.** Say that next to the number, not just here.

- **Razorpay's documented test-card decline reasons did not reproduce in this
  account.** A card documented as producing `error_code: insufficient_fund`
  produced a generic `BAD_REQUEST_ERROR` / `payment_failed` instead — one data
  point, not confirmed to generalise. `taxonomy/failure_classes.yaml`'s
  specific reason-string mappings may be untestable against real test-mode
  traffic at all. **The actual number, as of day 3: 1 of 110 published
  reasons (`payment_failed`, fixture 029) has a real fixture behind it; the
  other 109 are doc-only.** Of the 110 published reasons, 56 classify into
  one of SPEC §4.1's 8 real `FailureClass` values and 54 classify `UNKNOWN`
  by design — not a coverage gap, a domain boundary, made explicit per
  reason rather than left implicit: 28 are merchant/integration errors, not
  a customer payment failing at all (a broken API call, a missing merchant
  feature flag); 24 are real Razorpay reasons for a flow Salvage's domain
  never triggers — raw card/VPA/CVV re-entry on a stored mandate that is
  never re-collected, or Cardless EMI, out of PRD §5's scope; 2 are
  genuinely ambiguous low-volume reasons flagged rather than force-fit into
  a class. One additional description-only rule (not counted in the 110)
  classifies the two `payment.failed` fixtures that carry no `error_reason`
  at all (020, 028) — a Dashboard forced-charge artifact, not a real
  decline. Full breakdown and sourcing: `taxonomy/failure_classes.yaml`,
  `taxonomy/razorpay_published_reasons.yaml`, NOTES.md's Phase 3 entry.

- **A pre-flight rejection produces no webhook at all — a real gap in the
  product, found by hitting it.** A subscription authentication attempt above
  the (undocumented) test-mode amount ceiling is rejected before any payment
  entity is created, so it fires nothing — not `payment.failed`, nothing.
  Salvage is entirely webhook-driven: every gate, decision and audit row
  starts from an inbound event. A mandate can fail this way repeatedly and
  Salvage never learns, with zero audit trail, in exactly the system whose
  thesis is an auditable record of every money-relevant event. The fix — a
  periodic reconciliation sweep polling subscription state for drift no
  webhook explained — is logged as `PRD.md` FR-28 (P1), not built for this
  submission. State this one on camera: an agent that only trusts its event
  stream has a blind spot, and it was found by driving the platform, not by
  reading documentation.

## Measurement

- **Batch outcomes come from a simulator, not from real issuer behaviour, and
  the simulator has zero `measured` parameters.** Every value in
  `sim/calibration.yaml` is `cited` or `assumed`; none was observed in this
  project's own traffic, because Razorpay test mode would not produce a
  realistic decline distribution (1 of 110 published reasons reproduced at
  all). `sim/CALIBRATION.md` shows the per-attempt-versus-cumulative
  arithmetic rather than leaving it implicit.

- **The ±20% sensitivity sweep FLIPS the ranking, and that is the reported
  result.** At ×0.8 the baseline leads on net recovery; at ×1.2 Salvage does.
  FR-12's acceptance criterion asked whether the ranking survives ±20%: it
  does not. **This submission therefore does not claim Salvage recovers more
  money than the baseline.** The recovery comparison sits inside the noise of
  a calibration we wrote ourselves. The differences that do hold across every
  seed — ~35% fewer attempts per recovery, zero wasted attempts on hard
  declines against the baseline's 30 per run, zero compliance violations
  against the baseline's 22 out-of-window contacts — do not depend on the
  calibration being right, and those are the only comparative claims made.

- **Arm A is modelled as retrying regardless of the AFA threshold.** In
  reality the RBI e-mandate framework binds Razorpay's own auto-debits too, so
  a real baseline would also be blocked above ₹15,000. Modelling it that way
  would have made the baseline look worse. It was left as SPEC §5.10's literal
  "three blind retries" description instead — the comparison is harsher on
  Salvage this way, which is the direction an honest simplification should
  err.

- **The batch's contact-window violations for arm A apply Salvage's own
  reading of the Fair Practices Code to both arms.** We read the circular as
  covering automated messaging, so the baseline's blind dunning email counts
  as a violation when it lands outside 08:00–19:00 IST. That reading is stated
  in COMPLIANCE.md and applied consistently; a reader who disagrees with it
  should discount that row for both arms, not one.

- **Cases needing an AFA or re-authorisation link resolve as closed, not
  recovered.** No webhook in this build's scope reports that a customer
  completed such a flow, so the simulation issues the link once and the case
  ends. The deliberate above-₹15,000 slice in the cohort exists to exercise
  G3_AFA's block-and-remedy path, not to model a completion that does not
  exist. Real recovery rates for those classes would be higher than reported.

- **The batch runs against `mongomock`, not a real MongoDB.** The pipeline,
  gates and executor code are identical; the storage engine is not.
  `mongomock` enforces the plain unique indexes the executor's idempotency
  depends on, but does not model partial-index semantics (see the events-index
  entry above). A batch against real Atlas has not been run.

- **Payday inference is a heuristic** over prior successful charge dates, not
  real salary data.

- **The LLM's marginal contribution is measured in arm C and reported as
  measured**, whatever the number turns out to be. If it is worth little, that
  finding ships.

- **Eval scope was compressed for time, on 2026-09-02: 30–50 cases across
  3–5 seeds, not the original 500-case/20-seed design.** The submission
  window shrank to three days (see PRD §11, NOTES.md's 2026-09-02 entry).
  Results are reported as a range across seeds — "recovery held between X%
  and Y% across N independent runs" — not a mean with a 95% confidence
  interval. N=3–5 does not support a defensible CI; a range shows direction
  and rough stability, nothing more. Nowhere in this codebase, the README, or
  the video does the eval output claim statistical significance it doesn't
  have.

- **In-doubt reconciliation — the executor's response to a crash between
  dispatch and audit write — was cut for time, not designed away.** The
  executor's pre/post audit pattern (§5.6, PRD invariant 9) still makes
  double-execution structurally impossible: a second `pre` insert for an
  existing idempotency key raises `DuplicateKeyError`, and a pre-row with no
  matching post-row is correctly recognised as "dispatch is in flight or died
  mid-flight," not "never attempted." What is missing is the active step that
  resolves that in-flight state — checking Razorpay for the action's real
  outcome and writing the missing post-row. Without it, a case whose executor
  process dies between the pre-audit insert and the dispatch call stays
  stuck in that state indefinitely; nothing currently re-checks it.
  Undefined, not silently absent: this entry is the record of the gap, and
  it shares machinery with the FR-28 reconciliation sweep above rather than
  needing a separate mechanism when it is eventually built.
