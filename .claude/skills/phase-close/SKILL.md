---
name: phase-close
description: The end-of-phase ritual - run make verify, run compliance-auditor on any money-path diff, run spec-guardian, update the PRD if the design moved, commit with a why-message, and write one line into NOTES.md about anything surprising. Use when a phase from PRD section 11 is finished, or when asked to wrap up and commit a day's work.
---

# Closing a phase

CLAUDE.md's definition of done, executed in order. Do not report a phase
complete with any step unfinished. An unearned green is the one thing this
repository cannot afford, because the whole submission is an argument about
rigour.

## 1. `make verify`

All seven steps. If it fails, fix the code. Do not weaken a check to make it
pass, do not add an ignore, do not narrow a coverage target. If a check is
genuinely wrong, fix the check and say in the commit message why it was wrong.

New behaviour needs a test for the failure path, not only the happy one. Name
them from `SALVAGE_SPEC.md` §9 where they apply — duplicate webhook,
out-of-order delivery, malformed LLM output, scheduler drift, Razorpay 5xx,
timezone. Each one you have is a sentence in the video.

## 2. `compliance-auditor` on any money-path diff

Required whenever the diff touches `api/gates/`, `api/policy/` or
`api/executor/`. Not advisory, and not skippable because the change looks
small — the small changes are where bypasses appear.

Also run `bypass-hunter` if the gate chain, scheduler or executor changed, and
`eval-statistician` if the phase produced or moved a number.

Address the findings. Where you disagree with one, write down why rather than
dropping it silently.

## 3. `spec-guardian`

Always, at a phase boundary. It reads the repo against `PRD.md` and
`SALVAGE_SPEC.md` and reports drift, gaps and undocumented decisions.

## 4. Update the PRD if the design moved

`PRD.md` is the contract. When code and PRD disagree, the PRD wins — or the PRD
changes **in this commit**, with the reason stated in the document, not only in
the commit message. If a requirement moved phase, PRD §11 says so. If something
was cut, it is cut explicitly and named; a requirement that quietly stopped
being mentioned is the failure mode this step exists to catch.

## 5. One line in `NOTES.md`

Anything that surprised you: what broke, what the platform did that the docs did
not say, what you got wrong and how you found out.

**That line is video material.** Track 03 and the program both ask explicitly
what broke, and this file is where the answer comes from on day 10 — along with
`LIMITATIONS.md`. Write it while it is fresh; you will not reconstruct it later.
The most valuable part is *how it was found*: if `make verify` caught it, the
harness is earning its keep and that is worth saying on camera. If a human
caught it by accident, the harness has a hole — consider whether a check in
`scripts/` should have found it, and add one.

For a real bug rather than a surprise, use the `bug-evidence` skill instead:
failing test first, then the fix.

## 6. Commit

One commit per phase where possible. The message says what changed **and why** —
the why is the part that will matter when you read this back on day 10.

```
day N: <what shipped>

<why it is shaped this way; what you decided against and why>
<PRD changes, if any, and the reason>
```

## 7. Report honestly

State plainly which PRD §11 gate-to-advance this phase had, and whether it is
met. If it is not, say what remains rather than rounding up.
