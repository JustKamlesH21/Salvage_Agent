---
name: bug-evidence
description: Capture a real bug as evidence - failing test first, then the fix, then the NOTES.md entry - so it becomes demo material instead of a silent correction. Use the moment a genuine defect is found in Salvage, especially in the gates, normaliser, executor, scheduler or timezone handling.
---

# Turning a bug into evidence

CLAUDE.md: *when you find a real bug, keep the evidence.* Track 03 and the
program both ask explicitly what broke. A bug you fixed silently is a wasted
asset; a bug with a failing test, a fix and a paragraph is forty seconds of the
strongest material in the video.

Do these in order. The order is the point — a test written after the fix proves
the fix compiles, not that the bug existed.

## 1. Reproduce it as a failing test

Before touching the implementation, write the smallest test that fails for the
reason the bug exists. Put it where it belongs permanently, not in a scratch
file. Run it. Watch it fail, and read the failure message — if the message would
not tell a stranger what is wrong, rewrite the test.

If you cannot reproduce it, you do not yet understand it. Keep going until you
can. A fix applied to a bug you could not reproduce is a guess.

## 2. Record the shape before you fix it

In `NOTES.md`, while it is still fresh:

```markdown
### <date> — <one-line symptom>

**What happened:** the observable behaviour, concretely.
**Why:** the actual mechanism, not the first plausible story.
**How it was found:** which check, test or run surfaced it — and if nothing
did, say that, because a bug the harness could not catch is a finding about
the harness.
**Fix:** what changed.
**Class:** timezone / ordering / idempotency / boundary / spec-drift / other.
```

The "how it was found" line is the most valuable one. If `make verify` caught
it, that is the harness earning its keep and worth saying on camera. If a human
caught it by accident, the harness has a hole — consider whether a check in
`scripts/` should have found it, and add it.

## 3. Fix it

Smallest change that makes the test pass without weakening any other check.
Then run `make verify` in full: fixes in this codebase have a habit of moving
the problem into the gate chain.

## 4. Check whether it has siblings

Most bugs here come in families. A naive datetime in one gate means looking at
every gate. A missing idempotency key on one dispatch means auditing all of
them. Grep for the pattern, not just the instance.

## 5. Decide if it is video material

A bug is worth naming in the video when it is one a judge would worry about:
timezone, ordering, double-execution, a gate that failed open. Mark those in
`NOTES.md` with `**demo**` so day 10 does not have to go hunting.

Volunteering a weakness before the panel finds it is the highest-leverage move
available in the defence round.
