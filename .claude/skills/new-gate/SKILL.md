---
name: new-gate
description: Add a compliance gate to the G0-G10 chain, test-first, with its clause reference and fire-time re-evaluation test. Use when writing any gate in api/gates/, when changing a gate's threshold or window, or when a new regulatory constraint has to become enforceable code.
---

# Adding a compliance gate

Gates are the product. Correctness here is not a quality bar, it is the feature
being judged. Work test-first: the test states the rule in the regulator's terms
before any implementation exists to bias it.

## Before you write anything

Read the gate's row in `PRD.md` §9 and `SALVAGE_SPEC.md` §5.5. Find the actual
clause — the RBI E-mandate Framework 2026 section, or the Fair Practices Code
paragraph — and copy its text. `RULE_TEXT` is a quotation, not a summary you
wrote from memory. If you cannot find the clause, stop and say so; a gate with
an invented citation is worse than no gate.

## The shape

Every gate module in `api/gates/` exports exactly this surface:

```python
CLAUSE_REF = "RBI E-mandate Framework 2026 §<n>"  # the citation, verbatim
RULE_TEXT = "<the clause, quoted>"  # what the regulator said


def evaluate(ctx: GateContext) -> GateVerdict:  # ALLOW | BLOCK | REPLAN
    ...
```

`scripts/check_clauses.py` fails the build if either constant is missing, and it
knows the eleven gate names from PRD §9 — so a new gate also needs a line in
that script's `EXPECTED_GATES` and a row in PRD §9. Adding a gate without both
is drift.

## Write these tests first

In `tests/gates/test_<gate>.py`, before the implementation:

1. **The allow case.** The ordinary situation where the gate should not fire.
2. **The block case.** The situation the clause exists to prevent, with the
   verdict carrying the clause reference — the UI renders it, so it must be
   present in the verdict, not only in the module.
3. **Every boundary, from both sides.** 07:59 and 08:00. 19:00 and 19:01.
   ₹15,000 and ₹15,001. ₹1,00,000 for insurance, mutual funds and credit-card
   bills, and ₹15,000 for everything else. Off-by-one at a regulatory threshold
   is a compliance violation, not a rounding issue.
4. **Fire-time re-evaluation.** Scheduled at a compliant moment, fired at a
   non-compliant one, blocked. Gates re-evaluate at fire time, always —
   SPEC §5.5. Build this test even when it feels redundant.
5. **Timezone.** Construct the input in UTC, assert the verdict in IST. The
   server runs UTC and every gate reasons in IST; most timezone bugs in this
   codebase will be here.
6. **The exception path.** If the gate's inputs are missing or malformed, it
   blocks. A gate that fails open is a bypass with extra steps.

100% branch coverage is required on `api/gates/`. Reach it by covering
behaviour, not by deleting branches.

## After

- `make verify` green.
- Run the `compliance-auditor` subagent on the diff. Do not skip it because the
  gate looks simple — the simple ones are where bypasses appear.
- If a threshold or window differs from PRD §9, change the PRD in the same
  commit and say why.
