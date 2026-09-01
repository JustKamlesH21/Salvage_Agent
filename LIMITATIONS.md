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
  traffic at all; coverage should be reported as **N of M published reasons
  have a real fixture**, not implied as complete.

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

- **Batch outcomes come from a simulator, not from real issuer behaviour.**
  Calibration sources are documented and marked *measured*, *cited* or
  *assumed*; a sensitivity analysis at ±20% is published. The A-versus-B
  *ranking* is the claim. The absolute ₹ figure is not a prediction.

- **Payday inference is a heuristic** over prior successful charge dates, not
  real salary data.

- **The LLM's marginal contribution is measured in arm C and reported as
  measured**, whatever the number turns out to be. If it is worth little, that
  finding ships.
