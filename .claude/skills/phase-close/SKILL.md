---
name: phase-close
description: Run the definition-of-done ritual at the end of a build phase - full verify, the required subagent reviews, PRD reconciliation, NOTES.md entry, and the commit. Use when a phase from PRD section 11 is finished, or whenever asked to wrap up and commit a day's work.
---

# Closing a phase

CLAUDE.md's definition of done, executed in order. Do not report a phase
complete with any step unfinished — an unearned green is the one thing this
repository cannot afford, because the whole submission is an argument about
rigour.

## 1. Verify

```bash
make verify
```

All seven steps. If it fails, fix the code. Do not weaken a check to make it
pass, do not add an ignore, do not narrow a coverage target. If a check is
genuinely wrong, fix the check and say in the commit message why it was wrong.

## 2. Tests, including the failure path

For every behaviour this phase added, there is a test for the failure path, not
only the happy one. Name them from `SALVAGE_SPEC.md` §9 where they apply —
duplicate webhook, out-of-order delivery, malformed LLM output, scheduler drift,
Razorpay 5xx, timezone. Those six are demo material; every one you have is a
sentence in the video.

## 3. Subagent review

- Diff touches `api/gates/`, `api/policy/` or `api/executor/` → run
  **compliance-auditor**. Required, not advisory.
- Diff touches the gate chain, scheduler or executor → also run
  **bypass-hunter**.
- Phase produced or changed a number → run **eval-statistician**.
- Always, at a phase boundary → run **spec-guardian**.

Address the findings. Where you disagree with one, write down why rather than
dropping it silently.

## 4. Reconcile the PRD

If the design changed, `PRD.md` changes in this commit, with the reason. If a
requirement moved phase, PRD §11 says so. If something was cut, it is cut
explicitly and named — a requirement that quietly stopped being mentioned is
the failure mode this step exists to catch.

## 5. Log what surprised you

Append to `NOTES.md`: what broke, what the platform did that the docs did not
say, what you got wrong and how you found out. This file is the raw material for
the "what broke" beat of the video and for `LIMITATIONS.md`. Bugs found and
fixed are demo material for this submission, not embarrassments.

## 6. Commit

One commit per phase where possible. The message says what changed and why —
the why is the part that will matter when you are reading this back on day 10.

```
day N: <what shipped>

<why it is shaped this way; what you decided against and why>
<PRD changes, if any, and the reason>
```

## 7. Report

State plainly: which PRD §11 gate-to-advance this phase had, and whether it is
met. If it is not, say what remains rather than rounding up.
