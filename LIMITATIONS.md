# Limitations

Written by us, and meant to be harsher than anything a judge would ask.
Volunteering a weakness before the panel finds it is the highest-leverage move
available in the defence round.

> **Day 0.** The list below is what is already known to be true from the design.
> It grows as the system is built, and it is not trimmed before submission.

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

## Platform

- **Manual charging of a domestic card is not supported by Razorpay.** For
  domestic card mandates the recovery path is pre-debit notice → Payment Link →
  customer-initiated, not a silent manual charge. The manual-invoice-charge
  lever — the one that does not consume the subscription's retry budget —
  applies only where supported.

- **Test-mode caps constrain the live portion of the demo.** Card tokens are
  valid three days. Thirty Payment Links per business, ever. A test charge on a
  subscription can only be triggered from the Dashboard. Only the ~8 hand-run
  demo cases touch the live API; the batch runs on the simulator.

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
