---
name: gate-authoring
description: The procedure for adding a compliance gate to the G0-G10 chain - module shape, the CLAUSE_REF/RULE_TEXT contract, the ALLOW/BLOCK/REPLAN return type, registration order, and the mandatory test template. Use when writing any gate in api/gates/, changing a gate's threshold or window, or turning a regulatory constraint into enforceable code.
---

# Authoring a compliance gate

Gates are the product. Correctness here is not a quality bar, it is the feature
being judged. Work test-first: the test states the rule in the regulator's
terms before an implementation exists to bias it.

## Before you write anything

Read the gate's row in `PRD.md` §9 and `SALVAGE_SPEC.md` §5.5. Find the actual
clause — the RBI E-mandate Framework 2026 section, or the Fair Practices Code
paragraph — and copy its text. `RULE_TEXT` is a quotation, not a summary you
wrote from memory. If you cannot find the clause, stop and say so. A gate with
an invented citation is worse than no gate: it is a false statement inside an
audit trail, and it renders in the UI.

## Module shape

One gate per module in `api/gates/`, named `g<n>_<slug>.py`, exporting exactly
this surface and nothing wider:

```python
CLAUSE_REF = "RBI E-mandate Framework 2026 §<n>"  # the citation, verbatim
RULE_TEXT = "<the clause as the regulator wrote it>"


def evaluate(ctx: GateContext) -> GateVerdict: ...
```

`scripts/check_clauses.py` reads both constants by AST and fails the build if
either is missing. It also knows the eleven gate names from PRD §9, so a gate
module that is not on that list fails too — adding a gate means adding a row to
PRD §9 *and* a line to that script's `EXPECTED_GATES`. Doing one without the
other is drift.

## The return type

Three verdicts, and every one of them carries its clause:

| Verdict | Means | Carries |
|---|---|---|
| `ALLOW` | this gate has no objection | gate id, evaluated_at |
| `BLOCK(clause, detail)` | the action must not happen, now or later, on this gate's authority | `CLAUSE_REF`, a `detail` string naming the specific input that failed |
| `REPLAN(ts, clause)` | the action is permissible, but not at this moment | `CLAUSE_REF`, the earliest compliant timestamp |

`detail` is written for a human reading the Decision Tape. `"₹18,000 > ₹15,000
AFA threshold"` is a detail. `"threshold exceeded"` is not — it makes the
operator go and look up what the threshold was.

`REPLAN` is not a softer `BLOCK`. Use it only when the same action becomes
lawful purely by waiting: `G1_WINDOW` outside 08:00–19:00 IST replans to the
next window open. `G3_AFA` above the threshold does not replan, because waiting
changes nothing — it blocks, and the policy engine proposes an AFA link instead.

## Registration and order

Order is part of the specification, not an implementation detail. `G0_KILL` is
evaluated first, always, and the chain is not reorderable by configuration —
CLAUDE.md invariant 4. A gate cannot be skipped by a flag, an argument or an
environment variable. If you find yourself wanting a "skip for tests" parameter,
build the test fixture differently.

Every gate in the chain is evaluated even after one blocks. The Decision Tape
shows passes, blocks and suppressed alternatives together; a chain that
short-circuits on the first block throws away the evidence the UI exists to
display.

## Mandatory test template

`tests/gates/test_<gate>.py`, written before the implementation. Every gate has
all six:

1. **Allow.** The ordinary situation where the gate has no objection.
2. **Block.** The situation the clause exists to prevent, asserting the verdict
   carries `CLAUSE_REF` — not just that it blocked.
3. **Replan.** If the gate can replan, the timestamp it computes, including
   across a day boundary. If it cannot, a test asserting it never returns
   `REPLAN`.
4. **Boundary values, from both sides.** 07:59 and 08:00. 19:00 and 19:01.
   ₹15,000 and ₹15,001. ₹1,00,000 for insurance, mutual funds and credit-card
   bills, and ₹15,000 for everything else. 48h and 48h minus a second. An
   off-by-one at a regulatory threshold is a compliance violation, not a
   rounding issue.
5. **Fire-time re-evaluation.** Scheduled at a compliant moment, fired at a
   non-compliant one, blocked. Gates re-evaluate at fire time, always
   (SPEC §5.5). Write it even when it feels redundant — this is failure test #4
   in SPEC §9 and one of the two picked for the video.
6. **Malformed input.** Missing or nonsense inputs block. A gate that fails open
   is a bypass with extra steps.

Construct timestamps in UTC and assert verdicts in IST. The server runs UTC and
every gate reasons in IST; most timezone bugs in this codebase will be here.

100% branch coverage is required on `api/gates/`. Reach it by covering
behaviour, not by deleting branches.

## Closing out

- `make verify` green.
- Run the `compliance-auditor` subagent on the diff. Do not skip it because the
  gate looks simple — the simple ones are where bypasses appear.
- Add the gate's row to `COMPLIANCE.md` with the verbatim clause.
- If a threshold or window differs from PRD §9, change the PRD in the same
  commit and say why.
