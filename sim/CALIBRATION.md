# Calibration

What every number in `sim/calibration.yaml` rests on, and what it does not.

## The three labels

Every parameter carries a `source:` of `measured`, `cited` or `assumed`.

- **`measured`** — observed in this project's own Razorpay test-mode traffic.
- **`cited`** — taken from, or arithmetically derived from, a named external
  source. Where a figure is derived, the arithmetic is shown below rather than
  left implicit.
- **`assumed`** — reasoned, not sourced. The honest label for a number we chose.

**There are zero `measured` values.** That is the most important sentence in
this file. Day 1 established why:

- Razorpay's documented test-mode decline reasons did not reproduce in this
  account. Of 110 published `error.reason` values, exactly **one**
  (`payment_failed`) has a real fixture behind it.
- A mandate-authentication attempt above the (undocumented) test-mode amount
  ceiling is rejected before any payment entity exists, so it fires no webhook
  at all.
- The shortest test-mode billing cycle (7 days) outlives the card token
  (3 days), so a natural recurring failure cannot be observed cleanly.

Test mode cannot produce a realistic decline distribution, which is exactly
why SPEC §5.9 specifies a simulator for the batch rather than pretending test
mode is a population. It is not a shortcut around missing data; it is a stated
substitute for data this platform will not give us.

## Per-attempt versus cumulative

Published recovery figures are almost always **cumulative over a whole dunning
campaign**. The simulator needs a **per-attempt** probability. Comparing one
against the other silently is how a simulator flatters itself, so the
conversion is written out.

`TEMPORARY_LIQUIDITY` at 0.35 per attempt compounds over three attempts to:

```
1 − (1 − 0.35)³  =  1 − 0.65³  =  1 − 0.275  =  72.5%
```

which sits inside the 45–70%-and-above band the cited sources give for
well-timed soft-decline retries. 0.35 is deliberately at the **low** end of
what that band implies. Overstating this class inflates the arm we are
arguing for, so it gets the conservative reading.

## Sources consulted

- Slicker's 2026 soft/hard decline retry material, Recurly and Paddle on
  failed-payment recovery: soft declines recover 45–70% cumulatively with
  well-timed retries; a benchmark target of 65%+; roughly half of all failed
  payments are insufficient-funds declines; retrying ~24 hours after the
  initial failure improves recovery by ~6.5%; syncing retries to paydays
  matters specifically for insufficient funds.
- The same sources on hard declines: `expired_card`, `lost_card` and
  persistent `do_not_honor` recover near 0%; hard declines are 10–15% of
  failed transactions; `do_not_honor` (Visa 05) is worth one retry after ~24
  hours and behaves like a permanent block after that.
- SALVAGE_SPEC.md §5.9 for the five multiplicative modifiers, which the brief
  states directly.
- Razorpay's subscription documentation for arm A's T+1/T+2/T+3 ladder.

## What is not modelled, and why not

- **`mandate_type`.** SPEC §5.9's model signature includes it. Nothing in the
  sources consulted gives a basis for card-versus-UPI-autopay success rates,
  and inventing a modifier to satisfy a signature would be a fabricated number
  in the file where fabrication does the most damage. It is not a parameter.
- **`days_since_fail`.** Fully expressed by the attempt's own timestamp
  together with `attempt_no`. Passing it separately would let a caller
  describe a temporally impossible attempt.
- **AFA and reauth completion.** No webhook in this build's scope tells
  Salvage that a customer finished an AFA or re-authorisation flow, so a case
  routed to a link gets that link issued once and then closes. The
  above-₹15,000 slice in the cohort exists to exercise G3_AFA's block and
  remedy, not to simulate a completion that is not modelled.
- **Issuer goodwill.** Repeated failed attempts convert soft declines into
  hard ones. That cost is real and is not a rupee figure we can source, so
  `failed_attempt_paise` is 0 rather than invented. G4_BUDGET is the control
  standing in for it.

## Sensitivity: does the ranking hold at ±20%?

**No.** Run `make batch` and read `eval/results.json`.

Every base rate and every modifier scaled by 0.8 and by 1.2:

| Factor | Ranking by net recovery |
|---|---|
| ×0.8 | A > C — same order as the unperturbed run |
| ×1.2 | C > A — **reversed** |

The `hard_decline_retry` zeroing rule is not perturbed. Scaling it changes
nothing (0.0 stays 0.0 at any factor), and that is correct: "hard declines
never recover on retry" is a categorical claim, not a number we are uncertain
about by ±20%. Pretending to perturb it would overstate how much of the model
the sweep covers.

**What this means, stated plainly:** the recovery-rate comparison between
Salvage and the baseline is *within the noise of the calibration*. This
submission does not claim Salvage recovers more money than three blind
retries, because this simulation cannot support that claim. The differences it
*can* support — attempts per recovery, wasted attempts on hard declines, and
compliance violations — are large, hold across every seed, and do not depend
on the calibration being right.
